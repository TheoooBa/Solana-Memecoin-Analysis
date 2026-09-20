from pathlib import Path

import pytest

from smc_collector.config import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "collection.yaml"


def test_load_real_config_file():
    config = load_config(CONFIG_PATH)
    assert config.api.network == "solana"
    assert config.api.calls_per_minute > 0
    assert config.univers.sampling_seed == 42
    assert 0.0 <= config.univers.new_pools_sample_probability <= 1.0
    assert 0.0 <= config.univers.min_coverage_ratio <= 1.0


def test_api_key_read_from_env(monkeypatch):
    config = load_config(CONFIG_PATH)
    monkeypatch.delenv(config.api.api_key_env_var, raising=False)
    assert config.api.api_key is None
    monkeypatch.setenv(config.api.api_key_env_var, "secret-value")
    assert config.api.api_key == "secret-value"


def test_invalid_probability_rejected(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        CONFIG_PATH.read_text().replace("new_pools_sample_probability: 0.25", "new_pools_sample_probability: 1.5")
    )
    with pytest.raises(ValueError):
        load_config(bad_config)


def test_paths_resolved_relative_to_base_dir(tmp_path):
    config = load_config(CONFIG_PATH, base_dir=tmp_path)
    assert config.storage.raw_dir == tmp_path / "data" / "raw"
    assert config.snapshots_dir == tmp_path / "data" / "raw" / "snapshots"
