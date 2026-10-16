from datetime import datetime, timedelta, timezone

import pytest

from smc_collector.models import PoolDiscovery, PoolSnapshot, RugcheckInfoRecord, SCHEMA_VERSION
from smc_collector.storage import AppendOnlyCsvWriter
from smc_analysis.data import load_witness_pool_records, net_return, price_at_horizon


def _discovery(pool_address, discovered_at, group="random_new", **overrides):
    defaults = dict(
        pool_address=pool_address, network="solana", dex="pumpswap", group=group,
        discovered_at_utc=discovered_at.isoformat(), pool_created_at=discovered_at.isoformat(),
        reason="random_new", sampling_probability=0.25,
        tracking_until_utc=(discovered_at + timedelta(hours=48)).isoformat(),
        base_token_address="TOKEN1", schema_version=SCHEMA_VERSION,
    )
    defaults.update(overrides)
    return PoolDiscovery(**defaults)


def _snapshot(pool_address, ts, price_usd, source="snapshot", locked_liquidity_pct=None, **overrides):
    period_fields = {}
    for period in ("m5", "m15", "m30", "h1", "h6", "h24"):
        period_fields[f"volume_usd_{period}"] = None
        period_fields[f"price_change_pct_{period}"] = None
        for f in ("buys", "sells", "buyers", "sellers"):
            period_fields[f"{f}_{period}"] = None
    defaults = dict(
        request_timestamp_utc=ts.isoformat(), pool_address=pool_address, network="solana", dex="pumpswap",
        base_token_address="TOKEN1", base_token_symbol="SYM", quote_token_address="SOL", quote_token_symbol="SOL",
        pool_created_at=ts.isoformat(), price_usd=price_usd, fdv_usd=None, market_cap_usd=None, reserve_usd=1000.0,
        quote_token_price_usd=180.0, locked_liquidity_pct=locked_liquidity_pct, pool_fee_pct=0.25,
        source=source, ohlcv_timeframe=None, ohlcv_open=None, ohlcv_high=None, ohlcv_low=None,
        ohlcv_close=None, ohlcv_volume=None, raw_cache_path=None, schema_version=SCHEMA_VERSION,
        **period_fields,
    )
    defaults.update(overrides)
    return PoolSnapshot(**defaults)


def _rugcheck(pool_address, **overrides):
    defaults = dict(
        pool_address=pool_address, token_address="TOKEN1", network="solana",
        requested_at_utc=datetime(2026, 10, 15, tzinfo=timezone.utc).isoformat(),
        score=1000.0, score_normalised=20.0, rugged=False, risks_count=0, risks_json=None,
        permanent_delegate_present=False, transfer_hook_present=False, pausable_present=False,
        mint_close_authority_present=False, non_transferable=False, default_account_state=None,
        transfer_fee_pct=0.0, lp_locked_pct=100.0, locker_scan_status="none", graph_insiders_detected=0,
        top_holder_pct=10.0, insider_holders_count=0, insider_holders_pct_sum=0.0,
        creator="Creator1", creator_balance=0.0, token_program="TokenzQd...", metadata_mutable=False,
        schema_version=SCHEMA_VERSION,
    )
    defaults.update(overrides)
    return RugcheckInfoRecord(**defaults)


class TestLoadWitnessPoolRecords:
    def test_excludes_trending_pools(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at, group="trending").as_row()], dt=discovered_at
        )
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(
            [_snapshot("p1", discovered_at, 1.0).as_row()], dt=discovered_at
        )
        records = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)
        assert records == []

    def test_excludes_pools_discovered_before_freeze(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_before = frozen_at - timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_before).as_row()], dt=discovered_before
        )
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(
            [_snapshot("p1", discovered_before, 1.0).as_row()], dt=discovered_before
        )
        records = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)
        assert records == []

    def test_excludes_pools_without_any_price(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at).as_row()], dt=discovered_at
        )
        records = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)
        assert records == []

    def test_merges_snapshot_and_rugcheck_fields(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at).as_row()], dt=discovered_at
        )
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(
            [_snapshot("p1", discovered_at, 1.0, locked_liquidity_pct=95.0).as_row()], dt=discovered_at
        )
        AppendOnlyCsvWriter(rc_dir, RugcheckInfoRecord.fieldnames()).append_rows(
            [_rugcheck("p1").as_row()], dt=discovered_at
        )
        records = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)
        assert len(records) == 1
        record = records[0]
        assert record.rule_fields["locked_liquidity_pct"] == "95.0"
        assert record.rule_fields["rugged"] == "False"
        assert record.entry_price == 1.0

    def test_ohlcv_backfill_rows_contribute_to_price_series_not_rule_fields(self, tmp_path):
        """Un instantané ohlcv_backfill a un prix utilisable, mais ses
        champs de règle (locked_liquidity_pct etc.) sont toujours None par
        construction : ne doit jamais écraser ceux du premier snapshot réel."""
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at).as_row()], dt=discovered_at
        )
        rows = [
            _snapshot("p1", discovered_at - timedelta(minutes=30), 0.5, source="ohlcv_backfill").as_row(),
            _snapshot("p1", discovered_at + timedelta(minutes=5), 1.0, source="snapshot", locked_liquidity_pct=80.0).as_row(),
        ]
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(rows, dt=discovered_at)
        records = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)
        assert len(records) == 1
        assert records[0].rule_fields["locked_liquidity_pct"] == "80.0"
        assert len(records[0].price_series) == 2


class TestPriceAtHorizon:
    def test_finds_price_within_tolerance(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at).as_row()], dt=discovered_at
        )
        rows = [
            _snapshot("p1", discovered_at, 1.0).as_row(),
            _snapshot("p1", discovered_at + timedelta(hours=1, minutes=5), 2.0).as_row(),
        ]
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(rows, dt=discovered_at)
        record = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)[0]
        assert price_at_horizon(record, 1) == 2.0

    def test_returns_none_outside_tolerance(self, tmp_path):
        frozen_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
        discovered_at = frozen_at + timedelta(hours=1)
        registry_dir, snap_dir, rc_dir, ti_dir = (tmp_path / x for x in ("reg", "snap", "rc", "ti"))
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows(
            [_discovery("p1", discovered_at).as_row()], dt=discovered_at
        )
        rows = [
            _snapshot("p1", discovered_at, 1.0).as_row(),
            _snapshot("p1", discovered_at + timedelta(hours=5), 9.0).as_row(),  # bien hors tolérance pour h1
        ]
        AppendOnlyCsvWriter(snap_dir, PoolSnapshot.fieldnames()).append_rows(rows, dt=discovered_at)
        record = load_witness_pool_records(registry_dir, snap_dir, rc_dir, ti_dir, frozen_at)[0]
        assert price_at_horizon(record, 1) is None


class TestNetReturn:
    def test_applies_round_trip_cost(self):
        assert net_return(entry_price=1.0, horizon_price=1.10, cost_round_trip_pct=3.0) == pytest.approx(0.07)

    def test_negative_return(self):
        assert net_return(entry_price=1.0, horizon_price=0.90, cost_round_trip_pct=3.0) == pytest.approx(-0.13)
