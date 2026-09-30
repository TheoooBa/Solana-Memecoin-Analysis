from datetime import datetime, timedelta, timezone

import pytest

from smc_collector.models import PoolDiscovery, SCHEMA_VERSION
from smc_collector.rugcheck import (
    PendingRugcheck,
    parse_rugcheck_report,
    run_rugcheck_phase,
    select_pools_pending_rugcheck,
)
from smc_collector.rugcheck_client import CallBudgetExceeded, RugCheckApiError
from smc_collector.storage import AppendOnlyCsvWriter

# Capturé sur un vrai appel à l'API le 2026-09-30, sur un token de notre
# propre jeu de données (voir reports/rugcheck_audit_2026-09-29.md), réduit
# aux seuls champs que le parseur lit. Cas "légitime" : pas de risque
# critique, extensions Token-2022 toutes absentes, aucun insider détecté.
SAMPLE_RUGCHECK_RESPONSE = {
    "score": 14500,
    "score_normalised": 55,
    "rugged": False,
    "risks": [
        {"name": "Large Amount of LP Unlocked", "value": "100.00%", "score": 11000, "level": "danger"},
        {"name": "Low Liquidity", "value": "$0.73", "score": 2999, "level": "danger"},
    ],
    "graphInsidersDetected": 103,
    "mintAuthority": None,
    "freezeAuthority": None,
    "transferFee": {"pct": 0, "maxAmount": 0, "authority": "11111111111111111111111111111111"},
    "tokenProgram": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "creator": "J6bFxaRkLvhhAHNv7iw15B478CWxm4C6ynCtsar14SUc",
    "creatorBalance": 32458558926692,
    "lockerScanStatus": "none",
    "token_extensions": {
        "nonTransferable": False,
        "defaultAccountState": None,
        "permanentDelegate": None,
        "mintCloseAuthority": None,
        "transferHook": None,
        "pausableConfig": None,
    },
    "markets": [{"lp": {"lpLockedPct": 0}}],
    "topHolders": [
        {"address": "2YgV...", "pct": 85.43986403389572, "insider": False},
        {"address": "4rnB...", "pct": 3.2629918006121037, "insider": False},
    ],
    "tokenMeta": {"name": "Pay and Inspire Dreams", "symbol": "PAID", "mutable": False},
}

# Cas synthétique (même forme vérifiée que ci-dessus) exerçant les champs
# "directement ce qu'on cherchait" de l'audit : extensions dangereuses
# présentes, réseau d'insiders détecté sur les détenteurs.
SAMPLE_DANGEROUS_RESPONSE = {
    "score": 90000,
    "score_normalised": 5,
    "rugged": True,
    "risks": [{"name": "Permanent Delegate", "value": "present", "score": 40000, "level": "danger"}],
    "graphInsidersDetected": 12,
    "mintAuthority": "AuthorityAddr111111111111111111111111111",
    "freezeAuthority": "AuthorityAddr222222222222222222222222222",
    "transferFee": {"pct": 15.0, "maxAmount": 0, "authority": "Creator1111111111111111111111111111111111"},
    "tokenProgram": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "creator": "Creator1111111111111111111111111111111111",
    "creatorBalance": 1000,
    "lockerScanStatus": "none",
    "token_extensions": {
        "nonTransferable": False,
        "defaultAccountState": "frozen",
        "permanentDelegate": "Delegate1111111111111111111111111111111111",
        "mintCloseAuthority": "CloseAuth11111111111111111111111111111111",
        "transferHook": "Hook111111111111111111111111111111111111",
        "pausableConfig": {"authority": "Pause1111111111111111111111111111111111111"},
    },
    "markets": [{"lp": {"lpLockedPct": 0}}],
    "topHolders": [
        {"address": "Insider1", "pct": 40.0, "insider": True},
        {"address": "Insider2", "pct": 20.0, "insider": True},
        {"address": "Regular1", "pct": 5.0, "insider": False},
    ],
    "tokenMeta": {"name": "Scam", "symbol": "SCAM", "mutable": True},
}


class TestParseRugcheckReport:
    def test_parses_legitimate_looking_response(self):
        parsed = parse_rugcheck_report(SAMPLE_RUGCHECK_RESPONSE)
        assert parsed["score"] == 14500.0
        assert parsed["score_normalised"] == 55.0
        assert parsed["rugged"] is False
        assert parsed["risks_count"] == 2
        assert "Large Amount of LP Unlocked" in parsed["risks_json"]
        assert parsed["permanent_delegate_present"] is False
        assert parsed["transfer_hook_present"] is False
        assert parsed["pausable_present"] is False
        assert parsed["mint_close_authority_present"] is False
        assert parsed["non_transferable"] is False
        assert parsed["default_account_state"] is None
        assert parsed["transfer_fee_pct"] == 0.0
        assert parsed["lp_locked_pct"] == 0.0
        assert parsed["locker_scan_status"] == "none"
        assert parsed["graph_insiders_detected"] == 103
        assert parsed["top_holder_pct"] == pytest.approx(85.4398640, rel=1e-6)
        assert parsed["insider_holders_count"] == 0
        assert parsed["insider_holders_pct_sum"] == 0.0
        assert parsed["creator"] == "J6bFxaRkLvhhAHNv7iw15B478CWxm4C6ynCtsar14SUc"
        assert parsed["creator_balance"] == 32458558926692.0
        assert parsed["token_program"] == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
        assert parsed["metadata_mutable"] is False

    def test_detects_dangerous_token_2022_extensions(self):
        parsed = parse_rugcheck_report(SAMPLE_DANGEROUS_RESPONSE)
        assert parsed["rugged"] is True
        assert parsed["permanent_delegate_present"] is True
        assert parsed["transfer_hook_present"] is True
        assert parsed["pausable_present"] is True
        assert parsed["mint_close_authority_present"] is True
        assert parsed["default_account_state"] == "frozen"
        assert parsed["transfer_fee_pct"] == 15.0

    def test_insider_holders_summarized_from_top_holders(self):
        parsed = parse_rugcheck_report(SAMPLE_DANGEROUS_RESPONSE)
        assert parsed["insider_holders_count"] == 2
        assert parsed["insider_holders_pct_sum"] == pytest.approx(60.0)
        assert parsed["top_holder_pct"] == pytest.approx(40.0)

    def test_missing_token_extensions_key_entirely_is_defensive(self):
        """Contrairement au cas testé ci-dessus (clé présente, valeur None,
        cas réel des tokens SPL classiques), une réponse sans la clé du tout
        ne doit pas non plus faire planter le parseur."""
        raw = {"score": 10}
        parsed = parse_rugcheck_report(raw)
        assert parsed["permanent_delegate_present"] is False
        assert parsed["non_transferable"] is None

    def test_null_token_extensions_value_is_defensive(self):
        """Cas réel vérifié le 2026-09-30 : un token SPL classique (pas
        Token-2022) renvoie la clé token_extensions avec la valeur None."""
        raw = {"score": 10, "token_extensions": None}
        parsed = parse_rugcheck_report(raw)
        assert parsed["permanent_delegate_present"] is False
        assert parsed["non_transferable"] is None

    def test_no_markets_leaves_lp_locked_pct_none(self):
        raw = {"markets": []}
        parsed = parse_rugcheck_report(raw)
        assert parsed["lp_locked_pct"] is None

    def test_no_top_holders_leaves_holder_fields_none_not_zero(self):
        """Distinction importante : aucune donnée de détenteurs disponible
        (None) est différent de "on a vérifié, zéro insider" (0)."""
        raw = {"topHolders": []}
        parsed = parse_rugcheck_report(raw)
        assert parsed["top_holder_pct"] is None
        assert parsed["insider_holders_count"] is None
        assert parsed["insider_holders_pct_sum"] is None

    def test_no_risks_gives_none_json_and_zero_count(self):
        raw = {"risks": []}
        parsed = parse_rugcheck_report(raw)
        assert parsed["risks_count"] == 0
        assert parsed["risks_json"] is None

    def test_completely_empty_response_is_defensive(self):
        parsed = parse_rugcheck_report({})
        assert parsed["score"] is None
        assert parsed["rugged"] is None
        assert parsed["risks_count"] == 0
        assert parsed["lp_locked_pct"] is None

    def test_response_never_carries_a_raw_json_field(self):
        """L'audit exclut explicitement la conservation de la réponse brute
        (contrairement à token_info) : le dict parsé ne doit contenir aucune
        clé de ce type, quelle que soit la taille de la réponse source."""
        parsed = parse_rugcheck_report(SAMPLE_RUGCHECK_RESPONSE)
        assert not any("raw" in k for k in parsed)


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


class TestSelectPoolsPendingRugcheck:
    def test_selects_pool_with_known_token_address(self, tmp_path):
        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        _write_discovery(registry_dir, now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert len(pending) == 1
        assert pending[0].pool_address == "p1"
        assert pending[0].token_address == "TOKEN1"
        assert pending[0].failure_count == 0

    def test_excludes_pool_without_token_address(self, tmp_path):
        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        v1_fields = [f for f in PoolDiscovery.fieldnames() if f not in ("base_token_address", "schema_version")]
        row = {
            "pool_address": "old_pool", "network": "solana", "dex": "pumpswap", "group": "trending",
            "discovered_at_utc": now.isoformat(), "pool_created_at": "2026-09-01T00:00:00Z",
            "reason": "trending_rank_1", "sampling_probability": "1.0",
            "tracking_until_utc": (now + timedelta(hours=48)).isoformat(),
        }
        AppendOnlyCsvWriter(registry_dir, v1_fields).append_rows([row], dt=now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert pending == []

    def test_excludes_pool_already_successful(self, tmp_path):
        from smc_collector.models import RugcheckInfoRecord

        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        _write_discovery(registry_dir, now)
        parsed = parse_rugcheck_report(SAMPLE_RUGCHECK_RESPONSE)
        record = RugcheckInfoRecord(
            pool_address="p1", token_address="TOKEN1", network="solana",
            requested_at_utc=now.isoformat(), schema_version=SCHEMA_VERSION, **parsed,
        )
        AppendOnlyCsvWriter(rc_dir, RugcheckInfoRecord.fieldnames()).append_rows([record.as_row()], dt=now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert pending == []

    def test_excludes_pool_marked_unavailable(self, tmp_path):
        from smc_collector.models import RugcheckStatusEvent

        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        _write_discovery(registry_dir, now)
        event = RugcheckStatusEvent(
            pool_address="p1", event_at_utc=now.isoformat(), event_type="unavailable",
            detail="3 échecs cumulés", schema_version=SCHEMA_VERSION,
        )
        AppendOnlyCsvWriter(status_dir, RugcheckStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert pending == []

    def test_not_yet_indexed_pool_remains_pending(self, tmp_path):
        """Différence clé avec token_info : un mint "pas encore indexé" ne
        doit jamais être exclu, quel que soit le nombre de fois où l'événement
        s'est produit."""
        from smc_collector.models import RugcheckStatusEvent

        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        _write_discovery(registry_dir, now)
        events = [
            RugcheckStatusEvent(
                pool_address="p1", event_at_utc=now.isoformat(), event_type="not_yet_indexed",
                detail="404", schema_version=SCHEMA_VERSION,
            )
            for _ in range(5)
        ]
        AppendOnlyCsvWriter(status_dir, RugcheckStatusEvent.fieldnames()).append_rows([e.as_row() for e in events], dt=now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert len(pending) == 1
        assert pending[0].failure_count == 0  # not_yet_indexed ne compte jamais comme un échec

    def test_fifo_order_oldest_discovered_first(self, tmp_path):
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        older = datetime(2026, 9, 29, tzinfo=timezone.utc)
        newer = datetime(2026, 9, 30, tzinfo=timezone.utc)
        _write_discovery(registry_dir, newer, pool_address="new_pool", discovered_at_utc=newer.isoformat())
        _write_discovery(registry_dir, older, pool_address="old_pool", discovered_at_utc=older.isoformat())

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert [p.pool_address for p in pending] == ["old_pool", "new_pool"]

    def test_respects_max_count(self, tmp_path):
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        for i in range(5):
            _write_discovery(registry_dir, now, pool_address=f"p{i}", discovered_at_utc=now.isoformat())
        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=2)
        assert len(pending) == 2

    def test_reports_current_failure_count_excluding_not_yet_indexed(self, tmp_path):
        from smc_collector.models import RugcheckStatusEvent

        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry_dir, rc_dir, status_dir = tmp_path / "reg", tmp_path / "rc", tmp_path / "status"
        _write_discovery(registry_dir, now)
        events = [
            RugcheckStatusEvent(pool_address="p1", event_at_utc=now.isoformat(), event_type="fetch_failed", detail="500", schema_version=SCHEMA_VERSION),
            RugcheckStatusEvent(pool_address="p1", event_at_utc=now.isoformat(), event_type="not_yet_indexed", detail="404", schema_version=SCHEMA_VERSION),
        ]
        AppendOnlyCsvWriter(status_dir, RugcheckStatusEvent.fieldnames()).append_rows([e.as_row() for e in events], dt=now)

        pending = select_pools_pending_rugcheck(registry_dir, rc_dir, status_dir, max_count=10)
        assert pending[0].failure_count == 1


class FakeRugcheckClient:
    """Rejoue une séquence de résultats scriptés pour get_token_report, un par appel."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def get_token_report(self, address):
        self.calls.append(address)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _pending(addr="p1", token="TOKEN1", failure_count=0):
    return PendingRugcheck(pool_address=addr, token_address=token, discovered_at=datetime(2026, 9, 30, tzinfo=timezone.utc), failure_count=failure_count)


class TestRunRugcheckPhase:
    def _run(self, client, pending, **overrides):
        params = dict(
            client=client,
            pending=pending,
            network="solana",
            run_started_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
            max_total_elapsed=timedelta(minutes=1, seconds=30),
            max_failures_before_unavailable=3,
            circuit_breaker_consecutive_failures=3,
            now_fn=lambda: datetime(2026, 9, 30, tzinfo=timezone.utc),
        )
        params.update(overrides)
        return run_rugcheck_phase(**params)

    def test_successful_fetch_produces_one_row(self):
        client = FakeRugcheckClient([SAMPLE_RUGCHECK_RESPONSE])
        result = self._run(client, [_pending()])
        assert len(result.info_rows) == 1
        assert result.info_rows[0]["pool_address"] == "p1"
        assert result.info_rows[0]["score"] is not None
        assert result.stopped_reason is None
        assert result.calls_attempted == 1

    def test_404_produces_not_yet_indexed_event_not_a_failure(self):
        client = FakeRugcheckClient([RugCheckApiError("404", status_code=404)])
        result = self._run(client, [_pending()])
        assert result.info_rows == []
        assert len(result.status_rows) == 1
        assert result.status_rows[0]["event_type"] == "not_yet_indexed"
        assert result.stopped_reason is None

    def test_repeated_404s_never_trigger_circuit_breaker(self):
        client = FakeRugcheckClient([RugCheckApiError("404", status_code=404) for _ in range(5)])
        pending = [_pending(f"p{i}", f"T{i}") for i in range(5)]
        result = self._run(client, pending, circuit_breaker_consecutive_failures=3)
        assert result.stopped_reason is None
        assert result.calls_attempted == 5
        assert all(r["event_type"] == "not_yet_indexed" for r in result.status_rows)

    def test_single_failure_then_success_resets_consecutive_counter(self):
        client = FakeRugcheckClient([
            RugCheckApiError("500", status_code=500),
            SAMPLE_RUGCHECK_RESPONSE,
        ])
        result = self._run(client, [_pending("p1", "T1"), _pending("p2", "T2")])
        assert len(result.info_rows) == 1
        assert len(result.status_rows) == 1
        assert result.status_rows[0]["event_type"] == "fetch_failed"
        assert result.stopped_reason is None

    def test_three_consecutive_failures_trigger_circuit_breaker(self):
        client = FakeRugcheckClient([
            RugCheckApiError("500", status_code=500),
            RugCheckApiError("500", status_code=500),
            RugCheckApiError("500", status_code=500),
            SAMPLE_RUGCHECK_RESPONSE,  # ne doit jamais être atteint
        ])
        pending = [_pending(f"p{i}", f"T{i}") for i in range(4)]
        result = self._run(client, pending)
        assert result.stopped_reason == "circuit_breaker"
        assert result.calls_attempted == 3
        assert len(result.info_rows) == 0

    def test_third_failure_for_same_pool_marks_unavailable(self):
        client = FakeRugcheckClient([RugCheckApiError("500", status_code=500)])
        result = self._run(client, [_pending("p1", "T1", failure_count=2)])
        event_types = [r["event_type"] for r in result.status_rows]
        assert "fetch_failed" in event_types
        assert "unavailable" in event_types

    def test_401_stops_immediately_even_on_first_attempt(self):
        client = FakeRugcheckClient([RugCheckApiError("401", status_code=401)])
        pending = [_pending("p1", "T1"), _pending("p2", "T2")]
        result = self._run(client, pending)
        assert result.stopped_reason == "circuit_breaker"
        assert result.calls_attempted == 1  # p2 jamais tenté

    def test_time_budget_exceeded_stops_before_any_call(self):
        client = FakeRugcheckClient([SAMPLE_RUGCHECK_RESPONSE])
        result = self._run(
            client, [_pending()],
            max_total_elapsed=timedelta(seconds=1),
            now_fn=lambda: datetime(2026, 9, 30, 0, 5, tzinfo=timezone.utc),
        )
        assert result.stopped_reason == "time_budget"
        assert result.calls_attempted == 0
        assert client.calls == []

    def test_call_budget_exceeded_stops_without_raising(self):
        client = FakeRugcheckClient([CallBudgetExceeded("épuisé")])
        result = self._run(client, [_pending()])
        assert result.stopped_reason == "call_budget"
        assert result.info_rows == []

    def test_info_row_never_carries_a_raw_response_field(self):
        client = FakeRugcheckClient([SAMPLE_RUGCHECK_RESPONSE])
        result = self._run(client, [_pending()])
        assert not any("raw" in k for k in result.info_rows[0])
