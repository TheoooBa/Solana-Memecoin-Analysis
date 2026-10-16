import random
from datetime import datetime, timedelta, timezone

from smc_analysis.config import (
    AnalysisConfig, ConditionConfig, MinimumSampleConfig, PermutationTestConfig, RuleConfig,
)
from smc_analysis.data import PoolEvalRecord
from smc_analysis.evaluate import (
    VERDICT_BEATS_CHANCE, VERDICT_INSUFFICIENT, VERDICT_NO_EDGE,
    append_results_csv, evaluate_rule_at_horizon, run_analysis,
)

RULE = RuleConfig(
    id="liquidite_90", description="x", type="technique",
    conditions=(ConditionConfig(field="locked_liquidity_pct", operator=">", value=90),),
)


def _config(min_events=100, min_distinct_days=10, n_permutations=500, n_bootstrap=500) -> AnalysisConfig:
    return AnalysisConfig(
        horizons_hours=(1,),
        cost_round_trip_pct=3.0,
        permutation_test=PermutationTestConfig(
            n_permutations=n_permutations, n_bootstrap=n_bootstrap, random_seed=1,
            confidence_level=0.95, significance_alpha=0.05,
        ),
        minimum_sample=MinimumSampleConfig(min_events=min_events, min_distinct_days=min_distinct_days),
        rules=(RULE,),
        source_path="unused",
    )


def _records(n_days, per_day_a, per_day_b, return_a, return_b, locked_field=True, noise=0.0, seed=99):
    """Construit des PoolEvalRecord avec un rendement net contrôlé à
    l'horizon de 1h, répartis sur n_days jours distincts.

    `noise` ajoute une petite variation aléatoire autour de return_a/return_b
    (sinon deux groupes à rendement strictement constant font dégénérer la
    médiane sur une seule des deux valeurs, y compris sous mélange aléatoire
    — voir test_permutation.py pour la même remarque)."""
    rng = random.Random(seed)
    records = []
    start = datetime(2026, 10, 15, tzinfo=timezone.utc)
    for d in range(n_days):
        day = start + timedelta(days=d)
        for i in range(per_day_a):
            entry_price = 1.0
            jitter = rng.uniform(-noise, noise)
            horizon_price = entry_price * (1 + return_a + jitter + 0.03)  # +3% pour compenser le coût dans net_return
            records.append(PoolEvalRecord(
                pool_address=f"a{d}_{i}", discovered_at_utc=day,
                rule_fields={"locked_liquidity_pct": "95.0"} if locked_field else {},
                entry_price=entry_price,
                price_series=[(day, entry_price), (day + timedelta(hours=1), horizon_price)],
            ))
        for i in range(per_day_b):
            entry_price = 1.0
            jitter = rng.uniform(-noise, noise)
            horizon_price = entry_price * (1 + return_b + jitter + 0.03)
            records.append(PoolEvalRecord(
                pool_address=f"b{d}_{i}", discovered_at_utc=day,
                rule_fields={"locked_liquidity_pct": "10.0"},
                entry_price=entry_price,
                price_series=[(day, entry_price), (day + timedelta(hours=1), horizon_price)],
            ))
    return records


class TestEvaluateRuleAtHorizon:
    def test_insufficient_data_when_below_min_events(self):
        records = _records(n_days=3, per_day_a=2, per_day_b=2, return_a=0.5, return_b=0.0)
        config = _config(min_events=100, min_distinct_days=10)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        assert result.verdict == VERDICT_INSUFFICIENT
        assert result.p_value is None

    def test_insufficient_data_when_below_min_distinct_days(self):
        """Beaucoup d'événements mais concentrés sur un seul jour : toujours
        données insuffisantes malgré l'effectif."""
        records = _records(n_days=1, per_day_a=200, per_day_b=200, return_a=0.5, return_b=0.0)
        config = _config(min_events=100, min_distinct_days=10)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        assert result.verdict == VERDICT_INSUFFICIENT

    def test_no_edge_when_groups_identical(self):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.1, return_b=0.1)
        config = _config(min_events=100, min_distinct_days=10)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        assert result.verdict == VERDICT_NO_EDGE
        assert result.p_value == 1.0

    def test_beats_chance_with_strong_separation(self):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.5, return_b=0.0, noise=0.05)
        config = _config(min_events=100, min_distinct_days=10, n_permutations=1000, n_bootstrap=1000)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        assert result.verdict == VERDICT_BEATS_CHANCE
        assert result.p_value < 0.05
        assert result.ci_low > 0

    def test_excludes_records_missing_rule_field(self):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.5, return_b=0.0, locked_field=False)
        config = _config(min_events=100, min_distinct_days=10)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        # Tous les "A" sont exclus (champ manquant) : effectif A tombe à 0.
        assert result.n_events_a == 0
        assert result.verdict == VERDICT_INSUFFICIENT

    def test_excludes_records_without_entry_price(self):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.5, return_b=0.0)
        records[0].entry_price = None
        config = _config(min_events=100, min_distinct_days=10)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash123")
        assert result.n_events_a + result.n_events_b == len(records) - 1

    def test_records_frozen_config_hash_and_timestamp(self):
        records = _records(n_days=3, per_day_a=2, per_day_b=2, return_a=0.1, return_b=0.1)
        config = _config()
        fixed_now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "deadbeef", now_fn=lambda: fixed_now)
        assert result.frozen_config_hash == "deadbeef"
        assert result.evaluated_at_utc == fixed_now.isoformat()


class TestRunAnalysis:
    def test_produces_one_result_per_rule_per_horizon(self):
        config = AnalysisConfig(
            horizons_hours=(1, 6),
            cost_round_trip_pct=3.0,
            permutation_test=PermutationTestConfig(
                n_permutations=50, n_bootstrap=50, random_seed=1, confidence_level=0.95, significance_alpha=0.05,
            ),
            minimum_sample=MinimumSampleConfig(min_events=1, min_distinct_days=1),
            rules=(RULE,),
            source_path="unused",
        )
        records = _records(n_days=2, per_day_a=2, per_day_b=2, return_a=0.1, return_b=0.1)
        # Étendre la série de prix pour couvrir l'horizon de 6h aussi.
        for r in records:
            day = r.discovered_at_utc
            r.price_series.append((day + timedelta(hours=6), r.entry_price * 1.1))
        results = run_analysis(records, config, "hash")
        assert len(results) == 2
        assert {r.horizon_hours for r in results} == {1, 6}


class TestAppendResultsCsv:
    def test_creates_file_with_header_on_first_call(self, tmp_path):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.5, return_b=0.0)
        config = _config()
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash1")
        out = tmp_path / "registre.csv"
        append_results_csv([result], out)
        content = out.read_text()
        assert content.count("\n") == 2  # en-tête + 1 ligne
        assert "rule_id" in content.splitlines()[0]

    def test_second_call_appends_without_rewriting_header(self, tmp_path):
        records = _records(n_days=15, per_day_a=10, per_day_b=10, return_a=0.5, return_b=0.0)
        config = _config()
        result = evaluate_rule_at_horizon(records, RULE, 1, config, "hash1")
        out = tmp_path / "registre.csv"
        append_results_csv([result], out)
        append_results_csv([result], out)
        lines = out.read_text().splitlines()
        assert len(lines) == 3  # en-tête + 2 lignes, jamais tronqué
        assert lines.count("rule_id,horizon_hours,n_events_a,n_events_b,n_distinct_days,median_net_return_a,median_net_return_b,win_rate_a,win_rate_b,p_value,ci_low,ci_high,verdict,frozen_config_hash,evaluated_at_utc") == 1
