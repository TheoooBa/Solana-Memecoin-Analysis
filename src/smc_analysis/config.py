"""Chargement et validation de config/analysis.yaml.

Même philosophie que smc_collector/config.py : tout vient du fichier YAML,
aucune valeur par défaut magique en dur dans le code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ConditionConfig:
    field: str
    operator: str  # ">" | "<" | ">=" | "<=" | "==" | "!="
    value: float | bool


@dataclass(frozen=True)
class RuleConfig:
    id: str
    description: str
    type: str
    conditions: tuple[ConditionConfig, ...]


@dataclass(frozen=True)
class PermutationTestConfig:
    n_permutations: int
    n_bootstrap: int
    random_seed: int
    confidence_level: float
    significance_alpha: float


@dataclass(frozen=True)
class MinimumSampleConfig:
    min_events: int
    min_distinct_days: int


@dataclass(frozen=True)
class AnalysisConfig:
    horizons_hours: tuple[int, ...]
    cost_round_trip_pct: float
    permutation_test: PermutationTestConfig
    minimum_sample: MinimumSampleConfig
    rules: tuple[RuleConfig, ...]
    source_path: Path


_VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}


def _parse_condition(raw: dict) -> ConditionConfig:
    operator = raw["operator"]
    if operator not in _VALID_OPERATORS:
        raise ValueError(f"Opérateur de condition invalide : {operator!r} (attendu : {sorted(_VALID_OPERATORS)})")
    return ConditionConfig(field=raw["field"], operator=operator, value=raw["value"])


def _parse_rule(raw: dict) -> RuleConfig:
    conditions = tuple(_parse_condition(c) for c in raw["conditions"])
    if not conditions:
        raise ValueError(f"La règle {raw.get('id')!r} n'a aucune condition")
    return RuleConfig(id=raw["id"], description=raw["description"], type=raw["type"], conditions=conditions)


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Fichier de config invalide (pas un mapping) : {path}")

    try:
        horizons_raw = raw["horizons_hours"]
        costs_raw = raw["costs"]
        perm_raw = raw["permutation_test"]
        min_sample_raw = raw["minimum_sample"]
        rules_raw = raw["rules"]
    except KeyError as exc:
        raise ValueError(f"Section manquante dans {path} : {exc}") from exc

    if not horizons_raw:
        raise ValueError("horizons_hours ne peut pas être vide")

    permutation_test = PermutationTestConfig(
        n_permutations=int(perm_raw["n_permutations"]),
        n_bootstrap=int(perm_raw["n_bootstrap"]),
        random_seed=int(perm_raw["random_seed"]),
        confidence_level=float(perm_raw["confidence_level"]),
        significance_alpha=float(perm_raw["significance_alpha"]),
    )
    if not (0.0 < permutation_test.confidence_level < 1.0):
        raise ValueError("permutation_test.confidence_level doit être dans ]0, 1[")
    if not (0.0 < permutation_test.significance_alpha < 1.0):
        raise ValueError("permutation_test.significance_alpha doit être dans ]0, 1[")

    minimum_sample = MinimumSampleConfig(
        min_events=int(min_sample_raw["min_events"]),
        min_distinct_days=int(min_sample_raw["min_distinct_days"]),
    )

    rules = tuple(_parse_rule(r) for r in rules_raw)
    rule_ids = [r.id for r in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError(f"Identifiants de règles dupliqués dans {path} : {rule_ids}")

    return AnalysisConfig(
        horizons_hours=tuple(int(h) for h in horizons_raw),
        cost_round_trip_pct=float(costs_raw["round_trip_pct"]),
        permutation_test=permutation_test,
        minimum_sample=minimum_sample,
        rules=rules,
        source_path=path,
    )
