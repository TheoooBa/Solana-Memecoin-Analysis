from datetime import datetime, timedelta, timezone

import pytest

from smc_collector.http_client import CallBudgetExceeded, GeckoTerminalApiError
from smc_collector.models import PoolDiscovery, SCHEMA_VERSION
from smc_collector.storage import AppendOnlyCsvWriter
from smc_collector.token_info import (
    PendingTokenInfo,
    parse_token_info,
    run_token_info_phase,
    select_pools_pending_info,
)

SAMPLE_INFO_RESPONSE = {
    "data": {
        "attributes": {
            "gt_score": 77.24675324675326,
            "holders": {
                "count": 1932,
                "distribution_percentage": {"top_10": "59.6494", "11_20": "12.9412", "21_40": "11.3781", "rest": "16.0313"},
                "last_updated": "2026-09-20T05:45:43Z",
            },
            "mint_authority": "no",
            "freeze_authority": "no",
            "is_honeypot": "unknown",
            "developer_holding_percentage": "41.85",
        }
    }
}


class TestParseTokenInfo:
    def test_parses_known_good_response(self):
        parsed = parse_token_info(SAMPLE_INFO_RESPONSE)
        assert parsed["gt_score"] == pytest.approx(77.2467, rel=1e-3)
        assert parsed["holders_top10_pct"] == pytest.approx(59.6494)
        assert parsed["holders_11_20_pct"] == pytest.approx(12.9412)
        assert parsed["holders_21_40_pct"] == pytest.approx(11.3781)
        assert parsed["holders_rest_pct"] == pytest.approx(16.0313)
        assert parsed["mint_authority"] == "no"
        assert parsed["freeze_authority"] == "no"
        assert parsed["developer_holding_percentage"] == pytest.approx(41.85)
        assert parsed["is_honeypot"] == "unknown"

    def test_is_honeypot_stored_as_is_never_interpreted(self):
        # Booléen -> représentation texte, jamais transformé en logique métier.
        raw = {"data": {"attributes": {"is_honeypot": True}}}
        assert parse_token_info(raw)["is_honeypot"] == "True"
        raw_false = {"data": {"attributes": {"is_honeypot": False}}}
        assert parse_token_info(raw_false)["is_honeypot"] == "False"

    def test_missing_holders_block_is_defensive(self):
        raw = {"data": {"attributes": {"gt_score": 50.0}}}
        parsed = parse_token_info(raw)
        assert parsed["holders_top10_pct"] is None
        assert parsed["holders_rest_pct"] is None
        assert parsed["is_honeypot"] is None

    def test_completely_empty_response_is_defensive(self):
        parsed = parse_token_info({})
        assert all(v is None for v in parsed.values())

    def test_unexpected_tranche_keys_are_ignored_not_crashing(self, caplog):
        raw = {
            "data": {
                "attributes": {
                    "holders": {"distribution_percentage": {"top_10": "50", "unknown_bucket": "5"}}
                }
            }
        }
        parsed = parse_token_info(raw)
        assert parsed["holders_top10_pct"] == 50.0


def _write_discovery(registry_dir, dt, **overrides):
    defaults = dict(
        pool_address="p1", network="solana", dex="pumpswap", group="trending",
        discovered_at_utc=dt.isoformat(), pool_created_at="2026-09-20T00:00:00Z",
        reason="trending_rank_1", sampling_probability=1.0,
        tracking_until_utc=(dt + timedelta(hours=48)).isoformat(),
        base_token_address="TOKEN1", schema_version=SCHEMA_VERSION,
    )
    defaults.update(overrides)
    discovery = PoolDiscovery(**defaults)
    AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows([discovery.as_row()], dt=dt)


class TestSelectPoolsPendingInfo:
    def test_selects_pool_with_known_base_token_address(self, tmp_path):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        _write_discovery(registry_dir, now)

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert len(pending) == 1
        assert pending[0].pool_address == "p1"
        assert pending[0].base_token_address == "TOKEN1"
        assert pending[0].failure_count == 0

    def test_excludes_pool_without_base_token_address(self, tmp_path):
        """Pools découverts avant l'ajout de cette colonne (v1) : jamais interrogeables."""
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        v1_fields = [f for f in PoolDiscovery.fieldnames() if f not in ("base_token_address", "schema_version")]
        row = {
            "pool_address": "old_pool", "network": "solana", "dex": "pumpswap", "group": "trending",
            "discovered_at_utc": now.isoformat(), "pool_created_at": "2026-09-01T00:00:00Z",
            "reason": "trending_rank_1", "sampling_probability": "1.0",
            "tracking_until_utc": (now + timedelta(hours=48)).isoformat(),
        }
        AppendOnlyCsvWriter(registry_dir, v1_fields).append_rows([row], dt=now)

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert pending == []

    def test_excludes_pool_already_successful(self, tmp_path):
        from smc_collector.models import TokenInfoRecord

        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        _write_discovery(registry_dir, now)
        record = TokenInfoRecord(
            pool_address="p1", base_token_address="TOKEN1", network="solana",
            requested_at_utc=now.isoformat(), gt_score=50.0, holders_top10_pct=None,
            holders_11_20_pct=None, holders_21_40_pct=None, holders_rest_pct=None,
            mint_authority=None, freeze_authority=None, developer_holding_percentage=None,
            is_honeypot=None, raw_response_json=None, schema_version=SCHEMA_VERSION,
        )
        AppendOnlyCsvWriter(info_dir, TokenInfoRecord.fieldnames()).append_rows([record.as_row()], dt=now)

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert pending == []

    def test_excludes_pool_marked_unavailable(self, tmp_path):
        from smc_collector.models import TokenInfoStatusEvent

        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        _write_discovery(registry_dir, now)
        event = TokenInfoStatusEvent(
            pool_address="p1", event_at_utc=now.isoformat(), event_type="unavailable",
            detail="3 échecs cumulés", schema_version=SCHEMA_VERSION,
        )
        AppendOnlyCsvWriter(status_dir, TokenInfoStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert pending == []

    def test_fifo_order_oldest_discovered_first(self, tmp_path):
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        older = datetime(2026, 9, 20, tzinfo=timezone.utc)
        newer = datetime(2026, 9, 21, tzinfo=timezone.utc)
        _write_discovery(registry_dir, newer, pool_address="new_pool", discovered_at_utc=newer.isoformat())
        _write_discovery(registry_dir, older, pool_address="old_pool", discovered_at_utc=older.isoformat())

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert [p.pool_address for p in pending] == ["old_pool", "new_pool"]

    def test_respects_max_count(self, tmp_path):
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        for i in range(5):
            _write_discovery(registry_dir, now, pool_address=f"p{i}", discovered_at_utc=now.isoformat())
        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=2)
        assert len(pending) == 2

    def test_reports_current_failure_count(self, tmp_path):
        from smc_collector.models import TokenInfoStatusEvent

        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        registry_dir, info_dir, status_dir = tmp_path / "reg", tmp_path / "info", tmp_path / "status"
        _write_discovery(registry_dir, now)
        events = [
            TokenInfoStatusEvent(pool_address="p1", event_at_utc=now.isoformat(), event_type="fetch_failed", detail="x", schema_version=SCHEMA_VERSION)
            for _ in range(2)
        ]
        AppendOnlyCsvWriter(status_dir, TokenInfoStatusEvent.fieldnames()).append_rows([e.as_row() for e in events], dt=now)

        pending = select_pools_pending_info(registry_dir, info_dir, status_dir, max_count=10)
        assert pending[0].failure_count == 2


class FakeTokenInfoClient:
    """Rejoue une séquence de résultats scriptés pour get_token_info, un par appel."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def get_token_info(self, address):
        self.calls.append(address)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _pending(addr="p1", token="TOKEN1", failure_count=0):
    return PendingTokenInfo(pool_address=addr, base_token_address=token, discovered_at=datetime(2026, 9, 21, tzinfo=timezone.utc), failure_count=failure_count)


class TestRunTokenInfoPhase:
    def _run(self, client, pending, **overrides):
        params = dict(
            client=client,
            pending=pending,
            network="solana",
            run_started_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            max_total_elapsed=timedelta(minutes=3),
            max_failures_before_unavailable=3,
            circuit_breaker_consecutive_failures=3,
            max_raw_json_bytes=5000,
            now_fn=lambda: datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
        params.update(overrides)
        return run_token_info_phase(**params)

    def test_successful_fetch_produces_one_row(self):
        client = FakeTokenInfoClient([SAMPLE_INFO_RESPONSE])
        result = self._run(client, [_pending()])
        assert len(result.info_rows) == 1
        assert result.info_rows[0]["pool_address"] == "p1"
        assert result.info_rows[0]["gt_score"] is not None
        assert result.stopped_reason is None
        assert result.calls_attempted == 1

    def test_single_failure_then_success_resets_consecutive_counter(self):
        client = FakeTokenInfoClient([
            GeckoTerminalApiError("404", status_code=404),
            SAMPLE_INFO_RESPONSE,
        ])
        result = self._run(client, [_pending("p1", "T1"), _pending("p2", "T2")])
        assert len(result.info_rows) == 1
        assert len(result.status_rows) == 1
        assert result.status_rows[0]["event_type"] == "fetch_failed"
        assert result.stopped_reason is None

    def test_three_consecutive_failures_trigger_circuit_breaker(self):
        client = FakeTokenInfoClient([
            GeckoTerminalApiError("500", status_code=500),
            GeckoTerminalApiError("500", status_code=500),
            GeckoTerminalApiError("500", status_code=500),
            SAMPLE_INFO_RESPONSE,  # ne doit jamais être atteint
        ])
        pending = [_pending(f"p{i}", f"T{i}") for i in range(4)]
        result = self._run(client, pending)
        assert result.stopped_reason == "circuit_breaker"
        assert result.calls_attempted == 3
        assert len(result.info_rows) == 0

    def test_third_failure_for_same_pool_marks_unavailable(self):
        client = FakeTokenInfoClient([GeckoTerminalApiError("404", status_code=404)])
        result = self._run(client, [_pending("p1", "T1", failure_count=2)])
        event_types = [r["event_type"] for r in result.status_rows]
        assert "fetch_failed" in event_types
        assert "unavailable" in event_types

    def test_401_stops_immediately_even_on_first_attempt(self):
        client = FakeTokenInfoClient([GeckoTerminalApiError("401", status_code=401)])
        pending = [_pending("p1", "T1"), _pending("p2", "T2")]
        result = self._run(client, pending)
        assert result.stopped_reason == "circuit_breaker"
        assert result.calls_attempted == 1  # p2 jamais tenté

    def test_time_budget_exceeded_stops_before_any_call(self):
        client = FakeTokenInfoClient([SAMPLE_INFO_RESPONSE])
        result = self._run(
            client, [_pending()],
            max_total_elapsed=timedelta(seconds=1),
            now_fn=lambda: datetime(2026, 9, 21, 0, 5, tzinfo=timezone.utc),  # 5 min après le run_started_at
        )
        assert result.stopped_reason == "time_budget"
        assert result.calls_attempted == 0
        assert client.calls == []

    def test_call_budget_exceeded_stops_without_raising(self):
        client = FakeTokenInfoClient([CallBudgetExceeded("épuisé")])
        result = self._run(client, [_pending()])
        assert result.stopped_reason == "call_budget"
        assert result.info_rows == []

    def test_raw_response_stored_when_small(self):
        client = FakeTokenInfoClient([SAMPLE_INFO_RESPONSE])
        result = self._run(client, [_pending()], max_raw_json_bytes=5000)
        assert result.info_rows[0]["raw_response_json"] is not None

    def test_raw_response_dropped_when_too_large(self):
        client = FakeTokenInfoClient([SAMPLE_INFO_RESPONSE])
        result = self._run(client, [_pending()], max_raw_json_bytes=10)  # bien plus petit que la réponse réelle
        assert result.info_rows[0]["raw_response_json"] is None
        # Les champs parsés restent disponibles même sans la réponse brute.
        assert result.info_rows[0]["gt_score"] is not None
