"""Orchestration de l'évaluation : charge les données, applique chaque
règle à chaque horizon, calcule le verdict. Voir
reports/analysis_design_2026-10-14.md pour la méthodologie complète.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from .config import AnalysisConfig, RuleConfig
from .data import PoolEvalRecord, net_return, price_at_horizon
from .permutation import DayEvent, day_block_permutation_test, day_bootstrap_ci
from .rules import evaluate_rule

VERDICT_INSUFFICIENT = "donnees_insuffisantes"
VERDICT_NO_EDGE = "ne_bat_pas_le_hasard"
VERDICT_BEATS_CHANCE = "bat_le_hasard"


@dataclass
class RuleHorizonResult:
    rule_id: str
    horizon_hours: int
    n_events_a: int
    n_events_b: int
    n_distinct_days: int
    median_net_return_a: float | None
    median_net_return_b: float | None
    win_rate_a: float | None
    win_rate_b: float | None
    p_value: float | None
    ci_low: float | None
    ci_high: float | None
    verdict: str
    frozen_config_hash: str
    evaluated_at_utc: str

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(RuleHorizonResult)]

    def as_row(self) -> dict:
        return asdict(self)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _win_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v > 0) / len(values)


def evaluate_rule_at_horizon(
    records: list[PoolEvalRecord],
    rule: RuleConfig,
    horizon_hours: int,
    config: AnalysisConfig,
    frozen_config_hash: str,
    now_fn=lambda: datetime.now(timezone.utc),
) -> RuleHorizonResult:
    events: list[DayEvent] = []
    returns_a: list[float] = []
    returns_b: list[float] = []

    for record in records:
        if record.entry_price is None or record.entry_price <= 0:
            continue
        verdict = evaluate_rule(record.rule_fields, rule)
        if verdict is None:
            continue  # champ requis absent : exclu, jamais deviné
        horizon_price = price_at_horizon(record, horizon_hours)
        if horizon_price is None:
            continue  # pas d'instantané assez proche de cet horizon

        r = net_return(record.entry_price, horizon_price, config.cost_round_trip_pct)
        events.append(DayEvent(day=record.discovered_at_utc.date(), is_group_a=verdict, value=r))
        (returns_a if verdict else returns_b).append(r)

    n_distinct_days_a = len({e.day for e in events if e.is_group_a})
    min_sample = config.minimum_sample
    perm = config.permutation_test

    if len(returns_a) < min_sample.min_events or n_distinct_days_a < min_sample.min_distinct_days:
        verdict_label = VERDICT_INSUFFICIENT
        p_value = None
        ci_low, ci_high = None, None
    else:
        _, p_value = day_block_permutation_test(events, perm.n_permutations, perm.random_seed)
        ci = day_bootstrap_ci(events, perm.n_bootstrap, perm.random_seed, perm.confidence_level)
        ci_low, ci_high = ci if ci is not None else (None, None)
        significant = p_value is not None and p_value < perm.significance_alpha
        ci_excludes_zero = ci_low is not None and ci_high is not None and (ci_low > 0 or ci_high < 0)
        verdict_label = VERDICT_BEATS_CHANCE if (significant and ci_excludes_zero) else VERDICT_NO_EDGE

    return RuleHorizonResult(
        rule_id=rule.id,
        horizon_hours=horizon_hours,
        n_events_a=len(returns_a),
        n_events_b=len(returns_b),
        n_distinct_days=n_distinct_days_a,
        median_net_return_a=_median(returns_a),
        median_net_return_b=_median(returns_b),
        win_rate_a=_win_rate(returns_a),
        win_rate_b=_win_rate(returns_b),
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        verdict=verdict_label,
        frozen_config_hash=frozen_config_hash,
        evaluated_at_utc=now_fn().isoformat(),
    )


def run_analysis(
    records: list[PoolEvalRecord], config: AnalysisConfig, frozen_config_hash: str,
) -> list[RuleHorizonResult]:
    results = []
    for rule in config.rules:
        for horizon in config.horizons_hours:
            results.append(evaluate_rule_at_horizon(records, rule, horizon, config, frozen_config_hash))
    return results


def append_results_csv(results: list[RuleHorizonResult], output_path: str | Path) -> None:
    """Registre de toutes les règles testées : jamais tronqué ni réécrit,
    une évaluation de plus s'ajoute toujours aux précédentes (voir
    reports/analysis_design_2026-10-14.md, section 6)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RuleHorizonResult.fieldnames())
        if write_header:
            writer.writeheader()
        for result in results:
            writer.writerow(result.as_row())
