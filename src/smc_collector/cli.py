"""Interface en ligne de commande : `python -m smc_collector <sous-commande>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .build_db import build_database
from .collect import run_collect
from .config import load_config
from .diagnostics import run_diagnose_dead_pools


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

    return 1


if __name__ == "__main__":
    sys.exit(main())
