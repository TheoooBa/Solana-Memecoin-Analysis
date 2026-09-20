from datetime import datetime, timedelta, timezone

from smc_collector.models import PoolDiscovery, PoolStatusEvent
from smc_collector.parsing import ParsedPool
from smc_collector.registry import (
    PoolState,
    deterministic_sample_decision,
    load_universe,
    select_new_discoveries,
)
from smc_collector.storage import AppendOnlyCsvWriter


def _make_parsed(address: str, dex: str = "pumpswap") -> ParsedPool:
    p = ParsedPool()
    p.pool_address = address
    p.dex = dex
    p.base_token_address = "BASE"
    p.base_token_symbol = "SYM"
    p.quote_token_address = "SOL"
    p.quote_token_symbol = "SOL"
    p.pool_created_at = "2026-09-20T00:00:00Z"
    p.price_usd = 0.001
    p.fdv_usd = 1000.0
    p.market_cap_usd = 1000.0
    p.reserve_usd = 500.0
    p.volume_usd_m5 = 1.0
    p.volume_usd_h1 = 2.0
    p.volume_usd_h24 = 3.0
    for period in ("m5", "h1", "h24"):
        for field in ("buys", "sells", "buyers", "sellers"):
            setattr(p, f"{field}_{period}", 1)
    return p


class TestDeterministicSampling:
    def test_reproducible_for_same_inputs(self):
        results_a = [deterministic_sample_decision(42, f"addr{i}", 0.3) for i in range(200)]
        results_b = [deterministic_sample_decision(42, f"addr{i}", 0.3) for i in range(200)]
        assert results_a == results_b

    def test_probability_zero_never_selects(self):
        assert all(not deterministic_sample_decision(1, f"a{i}", 0.0) for i in range(50))

    def test_probability_one_always_selects(self):
        assert all(deterministic_sample_decision(1, f"a{i}", 1.0) for i in range(50))

    def test_roughly_matches_target_probability(self):
        n = 5000
        p = 0.25
        hits = sum(deterministic_sample_decision(7, f"pool_{i}", p) for i in range(n))
        observed = hits / n
        assert abs(observed - p) < 0.03  # marge large pour éviter un test flaky

    def test_different_seed_changes_decision_for_some_pools(self):
        a = [deterministic_sample_decision(1, f"pool_{i}", 0.5) for i in range(100)]
        b = [deterministic_sample_decision(2, f"pool_{i}", 0.5) for i in range(100)]
        assert a != b


class TestSelectNewDiscoveries:
    def test_trending_pools_always_added_when_not_tracked(self):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        trending = [_make_parsed("trend1"), _make_parsed("trend2")]
        discoveries = select_new_discoveries(
            trending_pools=trending,
            new_pools=[],
            already_tracked=set(),
            network="solana",
            sampling_seed=42,
            sample_probability=0.25,
            tracking_duration_hours=48,
            now=now,
        )
        addrs = {d.pool_address for d in discoveries}
        assert addrs == {"trend1", "trend2"}
        assert all(d.group == "trending" and d.sampling_probability == 1.0 for d in discoveries)

    def test_already_tracked_pools_are_skipped(self):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        trending = [_make_parsed("trend1")]
        discoveries = select_new_discoveries(
            trending_pools=trending,
            new_pools=[],
            already_tracked={"trend1"},
            network="solana",
            sampling_seed=42,
            sample_probability=0.25,
            tracking_duration_hours=48,
            now=now,
        )
        assert discoveries == []

    def test_new_pools_sampled_deterministically(self):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        new_pools = [_make_parsed(f"new_{i}") for i in range(20)]
        discoveries = select_new_discoveries(
            trending_pools=[],
            new_pools=new_pools,
            already_tracked=set(),
            network="solana",
            sampling_seed=42,
            sample_probability=0.25,
            tracking_duration_hours=48,
            now=now,
        )
        expected = {p.pool_address for p in new_pools if deterministic_sample_decision(42, p.pool_address, 0.25)}
        assert {d.pool_address for d in discoveries} == expected
        assert all(d.group == "random_new" and d.sampling_probability == 0.25 for d in discoveries)

    def test_tracking_until_respects_duration(self):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        discoveries = select_new_discoveries(
            trending_pools=[_make_parsed("trend1")],
            new_pools=[],
            already_tracked=set(),
            network="solana",
            sampling_seed=42,
            sample_probability=0.25,
            tracking_duration_hours=48,
            now=now,
        )
        expected_until = now + timedelta(hours=48)
        assert discoveries[0].tracking_until_utc == expected_until.isoformat()


class TestLoadUniverse:
    def test_reconstructs_state_from_files(self, tmp_path):
        registry_dir = tmp_path / "pool_registry"
        status_dir = tmp_path / "pool_status"
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)

        discovery = PoolDiscovery(
            pool_address="p1",
            network="solana",
            dex="pumpswap",
            group="trending",
            discovered_at_utc=now.isoformat(),
            pool_created_at="2026-09-20T00:00:00Z",
            reason="trending_rank_1",
            sampling_probability=1.0,
            tracking_until_utc=(now + timedelta(hours=48)).isoformat(),
        )
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows([discovery.as_row()], dt=now)

        universe = load_universe(registry_dir, status_dir)
        assert "p1" in universe
        state = universe["p1"]
        assert isinstance(state, PoolState)
        assert state.is_active(now + timedelta(hours=1))
        assert not state.stopped

    def test_stop_event_deactivates_pool(self, tmp_path):
        registry_dir = tmp_path / "pool_registry"
        status_dir = tmp_path / "pool_status"
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)

        discovery = PoolDiscovery(
            pool_address="p1",
            network="solana",
            dex="pumpswap",
            group="trending",
            discovered_at_utc=now.isoformat(),
            pool_created_at="2026-09-20T00:00:00Z",
            reason="trending_rank_1",
            sampling_probability=1.0,
            tracking_until_utc=(now + timedelta(hours=48)).isoformat(),
        )
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows([discovery.as_row()], dt=now)

        event = PoolStatusEvent(
            pool_address="p1",
            event_at_utc=(now + timedelta(hours=49)).isoformat(),
            event_type="tracking_window_ended",
            detail="fin de fenêtre",
        )
        AppendOnlyCsvWriter(status_dir, PoolStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

        universe = load_universe(registry_dir, status_dir)
        assert universe["p1"].stopped is True
        assert not universe["p1"].is_active(now + timedelta(hours=50))

    def test_non_stop_event_does_not_deactivate(self, tmp_path):
        registry_dir = tmp_path / "pool_registry"
        status_dir = tmp_path / "pool_status"
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)

        discovery = PoolDiscovery(
            pool_address="p1", network="solana", dex="pumpswap", group="trending",
            discovered_at_utc=now.isoformat(), pool_created_at="2026-09-20T00:00:00Z",
            reason="trending_rank_1", sampling_probability=1.0,
            tracking_until_utc=(now + timedelta(hours=48)).isoformat(),
        )
        AppendOnlyCsvWriter(registry_dir, PoolDiscovery.fieldnames()).append_rows([discovery.as_row()], dt=now)

        event = PoolStatusEvent(
            pool_address="p1", event_at_utc=now.isoformat(),
            event_type="missing_from_multi_pool_response", detail="absent une fois",
        )
        AppendOnlyCsvWriter(status_dir, PoolStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

        universe = load_universe(registry_dir, status_dir)
        assert universe["p1"].stopped is False
        assert universe["p1"].is_active(now + timedelta(hours=1))
