import random
from datetime import date, timedelta

import pytest

from smc_analysis.permutation import DayEvent, day_block_permutation_test, day_bootstrap_ci


def _spread_over_days(n_days: int, per_day_a: int, per_day_b: int, value_a: float, value_b: float) -> list[DayEvent]:
    start = date(2026, 10, 1)
    events = []
    for i in range(n_days):
        day = start + timedelta(days=i)
        events.extend(DayEvent(day=day, is_group_a=True, value=value_a) for _ in range(per_day_a))
        events.extend(DayEvent(day=day, is_group_a=False, value=value_b) for _ in range(per_day_b))
    return events


def _spread_over_days_with_noise(
    n_days: int, per_day_a: int, per_day_b: int, mean_a: float, mean_b: float, noise: float, seed: int = 99
) -> list[DayEvent]:
    """Comme _spread_over_days, mais avec un petit bruit par événement.

    Important pour ces tests : avec des valeurs strictement constantes (deux
    masses ponctuelles), la médiane d'un grand groupe se fige presque
    toujours sur l'une des deux valeurs, y compris sous le mélange aléatoire
    (ré-étiqueter un mélange 50/50 de deux constantes donne encore une
    médiane proche de l'une des deux constantes la plupart du temps) — la
    statistique de permutation ne peut alors pas distinguer signal et hasard.
    Un vrai rendement n'est jamais une constante exacte ; le bruit évite ce
    cas dégénéré et rend le test représentatif.
    """
    rng = random.Random(seed)
    start = date(2026, 10, 1)
    events = []
    for i in range(n_days):
        day = start + timedelta(days=i)
        events.extend(DayEvent(day=day, is_group_a=True, value=mean_a + rng.uniform(-noise, noise)) for _ in range(per_day_a))
        events.extend(DayEvent(day=day, is_group_a=False, value=mean_b + rng.uniform(-noise, noise)) for _ in range(per_day_b))
    return events


class TestDayBlockPermutationTest:
    def test_empty_group_a_returns_none(self):
        events = [DayEvent(day=date(2026, 10, 1), is_group_a=False, value=1.0)]
        observed, p_value = day_block_permutation_test(events, n_permutations=100, random_seed=1)
        assert observed is None
        assert p_value is None

    def test_identical_values_give_p_value_one(self):
        """Aucune vraie différence : la statistique observée est nulle, et
        toute permutation produit aussi une statistique nulle (0 >= 0 est
        toujours vrai) -> p-value de 1."""
        events = _spread_over_days(n_days=10, per_day_a=5, per_day_b=5, value_a=5.0, value_b=5.0)
        observed, p_value = day_block_permutation_test(events, n_permutations=500, random_seed=1)
        assert observed == 0.0
        assert p_value == 1.0

    def test_strong_separation_gives_low_p_value(self):
        """Séparation nette (10.0 vs 0.0, bruitée) sur 15 jours distincts
        avec effectifs équilibrés par jour : une relabelisation aléatoire
        ne recrée quasiment jamais la séparation d'origine."""
        events = _spread_over_days_with_noise(n_days=15, per_day_a=6, per_day_b=6, mean_a=10.0, mean_b=0.0, noise=0.5)
        observed, p_value = day_block_permutation_test(events, n_permutations=2000, random_seed=1)
        assert observed == pytest.approx(10.0, abs=1.0)
        assert p_value < 0.05

    def test_p_value_is_reproducible_with_same_seed(self):
        events = _spread_over_days(n_days=12, per_day_a=4, per_day_b=4, value_a=3.0, value_b=1.0)
        r1 = day_block_permutation_test(events, n_permutations=300, random_seed=7)
        r2 = day_block_permutation_test(events, n_permutations=300, random_seed=7)
        assert r1 == r2

    def test_different_seeds_still_within_monte_carlo_noise(self):
        events = _spread_over_days_with_noise(n_days=12, per_day_a=4, per_day_b=4, mean_a=10.0, mean_b=0.0, noise=0.5)
        _, p1 = day_block_permutation_test(events, n_permutations=1000, random_seed=1)
        _, p2 = day_block_permutation_test(events, n_permutations=1000, random_seed=2)
        assert p1 < 0.1 and p2 < 0.1


class TestDayBootstrapCi:
    def test_returns_none_without_any_days(self):
        assert day_bootstrap_ci([], n_bootstrap=100, random_seed=1, confidence_level=0.95) is None

    def test_ci_low_never_exceeds_ci_high(self):
        events = _spread_over_days(n_days=10, per_day_a=5, per_day_b=5, value_a=4.0, value_b=1.0)
        ci = day_bootstrap_ci(events, n_bootstrap=500, random_seed=1, confidence_level=0.95)
        assert ci is not None
        lo, hi = ci
        assert lo <= hi

    def test_ci_centered_near_true_constant_difference(self):
        events = _spread_over_days(n_days=20, per_day_a=5, per_day_b=5, value_a=4.0, value_b=1.0)
        lo, hi = day_bootstrap_ci(events, n_bootstrap=1000, random_seed=1, confidence_level=0.95)
        assert lo <= 3.0 <= hi
