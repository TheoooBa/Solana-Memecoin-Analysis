"""Évaluation d'une règle (config.RuleConfig) contre les champs d'un pool.

Jamais d'eval() de texte libre : chaque condition est un triplet
(champ, opérateur, valeur) structuré dans analysis.yaml, comparé ici avec
un opérateur fixé parmi une liste connue. Un champ absent rend la règle
inapplicable pour ce pool (exclusion), jamais une valeur devinée.
"""

from __future__ import annotations

import operator as _operator
from typing import Any

from .config import ConditionConfig, RuleConfig

_OPERATORS = {
    ">": _operator.gt,
    "<": _operator.lt,
    ">=": _operator.ge,
    "<=": _operator.le,
    "==": _operator.eq,
    "!=": _operator.ne,
}


def evaluate_condition(row: dict[str, Any], condition: ConditionConfig) -> bool | None:
    """None si le champ est absent ou vide : la condition est inapplicable,
    jamais interprétée comme vraie ou fausse."""
    value = row.get(condition.field)
    if value is None or value == "":
        return None
    try:
        if isinstance(condition.value, bool):
            comparable = str(value).strip().lower() in ("true", "1")
        else:
            comparable = float(value)
    except (TypeError, ValueError):
        return None
    return _OPERATORS[condition.operator](comparable, condition.value)


def evaluate_rule(row: dict[str, Any], rule: RuleConfig) -> bool | None:
    """ET logique de toutes les conditions. None (exclusion) dès qu'une
    seule condition est inapplicable — jamais un ET partiel."""
    results = []
    for condition in rule.conditions:
        result = evaluate_condition(row, condition)
        if result is None:
            return None
        results.append(result)
    return all(results)
