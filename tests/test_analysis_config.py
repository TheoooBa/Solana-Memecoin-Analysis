from pathlib import Path

import pytest

from smc_analysis.config import load_analysis_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "analysis.yaml"


def test_load_real_config_file():
    config = load_analysis_config(CONFIG_PATH)
    assert config.horizons_hours == (1, 6, 24)
    assert config.cost_round_trip_pct == 3.0
    assert config.permutation_test.n_permutations == 10000
    assert config.permutation_test.random_seed == 42
    assert config.minimum_sample.min_events == 100
    assert config.minimum_sample.min_distinct_days == 10
    assert len(config.rules) == 4
    rule_ids = {r.id for r in config.rules}
    assert "liquidite_verrouillee_90" in rule_ids
    assert "rugcheck_sain" in rule_ids


def test_rugcheck_sain_has_three_conditions():
    config = load_analysis_config(CONFIG_PATH)
    rule = next(r for r in config.rules if r.id == "rugcheck_sain")
    assert len(rule.conditions) == 3
    assert rule.conditions[0].field == "rugged"
    assert rule.conditions[0].operator == "=="
    assert rule.conditions[0].value is False


def test_invalid_operator_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        CONFIG_PATH.read_text().replace('operator: ">"', 'operator: "=~"')
    )
    with pytest.raises(ValueError):
        load_analysis_config(bad)


def test_duplicate_rule_ids_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    content = CONFIG_PATH.read_text().replace(
        'id: "rugcheck_sain"', 'id: "liquidite_verrouillee_90"'
    )
    bad.write_text(content)
    with pytest.raises(ValueError):
        load_analysis_config(bad)


def test_empty_horizons_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(CONFIG_PATH.read_text().replace("horizons_hours: [1, 6, 24]", "horizons_hours: []"))
    with pytest.raises(ValueError):
        load_analysis_config(bad)


def test_rule_without_conditions_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    content = CONFIG_PATH.read_text() + "\n  - id: \"vide\"\n    description: \"x\"\n    type: \"technique\"\n    conditions: []\n"
    bad.write_text(content)
    with pytest.raises(ValueError):
        load_analysis_config(bad)
