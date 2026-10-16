import time

import pytest

from smc_analysis.freeze import FreezeMismatchError, compute_config_hash, freeze, load_freeze, verify_freeze


def _write_config(path, content="horizons_hours: [1, 6, 24]\n"):
    path.write_text(content)
    return path


class TestFreeze:
    def test_freeze_writes_hash_and_timestamp(self, tmp_path):
        config_path = _write_config(tmp_path / "analysis.yaml")
        freeze_path = tmp_path / "analysis_freeze.json"

        record = freeze(config_path, freeze_path)

        assert record.config_hash == compute_config_hash(config_path)
        assert record.frozen_at_utc
        loaded = load_freeze(freeze_path)
        assert loaded == record

    def test_load_freeze_returns_none_when_never_frozen(self, tmp_path):
        assert load_freeze(tmp_path / "does_not_exist.json") is None

    def test_verify_freeze_succeeds_when_unchanged(self, tmp_path):
        config_path = _write_config(tmp_path / "analysis.yaml")
        freeze_path = tmp_path / "analysis_freeze.json"
        freeze(config_path, freeze_path)

        record = verify_freeze(config_path, freeze_path)
        assert record.config_hash == compute_config_hash(config_path)

    def test_verify_freeze_fails_when_never_frozen(self, tmp_path):
        config_path = _write_config(tmp_path / "analysis.yaml")
        with pytest.raises(FreezeMismatchError):
            verify_freeze(config_path, tmp_path / "never_frozen.json")

    def test_verify_freeze_fails_when_config_changed_after_freeze(self, tmp_path):
        config_path = _write_config(tmp_path / "analysis.yaml")
        freeze_path = tmp_path / "analysis_freeze.json"
        freeze(config_path, freeze_path)

        _write_config(config_path, content="horizons_hours: [1, 6, 24, 48]\n")

        with pytest.raises(FreezeMismatchError):
            verify_freeze(config_path, freeze_path)

    def test_two_freezes_have_different_timestamps(self, tmp_path):
        config_path = _write_config(tmp_path / "analysis.yaml")
        freeze_path = tmp_path / "analysis_freeze.json"
        first = freeze(config_path, freeze_path)
        time.sleep(0.01)
        second = freeze(config_path, freeze_path)
        assert first.frozen_at_utc != second.frozen_at_utc
        assert first.config_hash == second.config_hash
