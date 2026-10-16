"""Interface en ligne de commande : `python -m smc_collector <sous-commande>`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .build_db import build_database
from .collect import run_collect
from .config import load_config
from .diagnostics import run_diagnose_dead_pools

from smc_analysis.config import load_analysis_config
from smc_analysis.data import load_witness_pool_records
from smc_analysis.evaluate import append_results_csv, run_analysis
from smc_analysis.freeze import FreezeMismatchError, freeze, verify_freeze


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smc_collector")
    parser.add_argument("--config", default="config/collection.yaml", help="Chemin vers collection.yaml")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="Exécute une collecte unique (idempotente).")

    build_db_parser = sub.add_parser("build-db", help="Charge les CSV dans une base SQLite locale.")
    build_db_parser.add_argument("--db-path", default="data/smc.sqlite3")

    diag_parser = sub.add_parser(
        "diagnose-dead-pools", help="Teste la disponibilité de l'historique OHLCV pour des pools déjà morts."
    )
    diag_parser.add_argument("--output", default="reports/dead_pool_history_probe.md")
    diag_parser.add_argument("--page-count", type=int, default=3)
    diag_parser.add_argument("--max-candidates", type=int, default=10)

    freeze_parser = sub.add_parser(
        "analyze-freeze", help="Gèle config/analysis.yaml (voir reports/analysis_design_2026-10-14.md)."
    )
    freeze_parser.add_argument("--analysis-config", default="config/analysis.yaml")
    freeze_parser.add_argument("--freeze-path", default="config/analysis_freeze.json")

    run_parser = sub.add_parser(
        "analyze-run", help="Évalue chaque règle gelée contre les données postérieures au gel."
    )
    run_parser.add_argument("--analysis-config", default="config/analysis.yaml")
    run_parser.add_argument("--freeze-path", default="config/analysis_freeze.json")
    run_parser.add_argument("--output", default="analysis/registre_regles.csv")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    config = load_config(args.config)

    if args.command == "collect":
        run_log = run_collect(config)
        print(run_log.as_row())
        return 0 if run_log.exit_reason in ("completed", "budget_exhausted") else 1

    if args.command == "build-db":
        counts = build_database(config, Path(args.db_path))
        print(counts)
        return 0

    if args.command == "diagnose-dead-pools":
        output = run_diagnose_dead_pools(
            config, Path(args.output), page_count=args.page_count, max_candidates=args.max_candidates
        )
        print(f"Rapport écrit : {output}")
        return 0

    if args.command == "analyze-freeze":
        record = freeze(args.analysis_config, args.freeze_path)
        print(f"Gelé : {args.analysis_config} (hash {record.config_hash[:12]}…) à {record.frozen_at_utc}")
        return 0

    if args.command == "analyze-run":
        try:
            freeze_record = verify_freeze(args.analysis_config, args.freeze_path)
        except FreezeMismatchError as exc:
            print(f"Erreur : {exc}", file=sys.stderr)
            return 1

        analysis_config = load_analysis_config(args.analysis_config)
        frozen_at = datetime.fromisoformat(freeze_record.frozen_at_utc)
        records = load_witness_pool_records(
            config.registry_dir, config.snapshots_dir, config.rugcheck_info_dir, config.token_info_dir,
            frozen_at_utc=frozen_at,
        )
        results = run_analysis(records, analysis_config, freeze_record.config_hash)
        append_results_csv(results, args.output)
        for r in results:
            print(f"{r.rule_id} @ {r.horizon_hours}h : {r.verdict} (n={r.n_events_a}, p={r.p_value})")
        print(f"Résultats ajoutés à {args.output}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
