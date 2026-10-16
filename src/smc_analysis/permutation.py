"""Test de permutation par bloc de jour et intervalle de confiance par
bootstrap journalier. Voir reports/analysis_design_2026-10-14.md, section 5.

Les deux reposent sur le même principe : des pools découverts le même jour
partagent souvent le même régime de marché (prix du SOL, humeur générale),
les traiter comme des événements indépendants sous-estimerait l'incertitude
réelle. On ne mélange/rééchantillonne donc jamais un événement isolément,
toujours par jour entier.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class DayEvent:
    day: date
    is_group_a: bool
    value: float


def _median_diff(events: Sequence[DayEvent]) -> float | None:
    a_vals = [e.value for e in events if e.is_group_a]
    b_vals = [e.value for e in events if not e.is_group_a]
    if not a_vals or not b_vals:
        return None
    return statistics.median(a_vals) - statistics.median(b_vals)


def day_block_permutation_test(
    events: Sequence[DayEvent], n_permutations: int, random_seed: int
) -> tuple[float | None, float | None]:
    """Retourne (statistique_observée, p-value bilatérale). (None, None) si
    l'un des deux groupes est vide (aucun test possible)."""
    observed = _median_diff(events)
    if observed is None:
        return None, None

    by_day: dict[date, list[DayEvent]] = defaultdict(list)
    for e in events:
        by_day[e.day].append(e)

    rng = random.Random(random_seed)
    count_as_extreme = 0
    for _ in range(n_permutations):
        shuffled: list[DayEvent] = []
        for day_events in by_day.values():
            labels = [e.is_group_a for e in day_events]
            rng.shuffle(labels)
            shuffled.extend(
                DayEvent(day=e.day, is_group_a=label, value=e.value)
                for e, label in zip(day_events, labels)
            )
        permuted_stat = _median_diff(shuffled)
        if permuted_stat is not None and abs(permuted_stat) >= abs(observed):
            count_as_extreme += 1

    p_value = count_as_extreme / n_permutations
    return observed, p_value


def day_bootstrap_ci(
    events: Sequence[DayEvent], n_bootstrap: int, random_seed: int, confidence_level: float
) -> tuple[float, float] | None:
    """Intervalle de confiance sur l'écart de médiane, par rééchantillonnage
    de jours entiers (avec remise) — pas d'événements individuels."""
    by_day: dict[date, list[DayEvent]] = defaultdict(list)
    for e in events:
        by_day[e.day].append(e)
    days = list(by_day.keys())
    if not days:
        return None

    rng = random.Random(random_seed)
    stats: list[float] = []
    for _ in range(n_bootstrap):
        sampled_days = [rng.choice(days) for _ in range(len(days))]
        resampled = [e for d in sampled_days for e in by_day[d]]
        stat = _median_diff(resampled)
        if stat is not None:
            stats.append(stat)

    if not stats:
        return None
    stats.sort()
    alpha = 1 - confidence_level
    lo_idx = int((alpha / 2) * len(stats))
    hi_idx = min(int((1 - alpha / 2) * len(stats)), len(stats) - 1)
    return stats[lo_idx], stats[hi_idx]
