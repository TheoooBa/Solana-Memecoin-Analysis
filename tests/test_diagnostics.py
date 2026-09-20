from smc_collector.diagnostics import DeadPoolProbeResult, find_dead_pool_candidates, render_report


class FakeNewPoolsClient:
    """Sert des pages de new_pools préparées à l'avance sans appel réseau."""

    def __init__(self, pages: dict[int, dict]):
        self._pages = pages
        self.calls = 0

    def get_new_pools(self, page: int = 1):
        self.calls += 1
        return self._pages.get(page, {"data": []})


def _pool_entry(address: str, reserve: str) -> dict:
    return {
        "id": f"solana_{address}",
        "type": "pool",
        "attributes": {
            "address": address,
            "pool_created_at": "2026-09-20T22:40:00Z",
            "reserve_in_usd": reserve,
            "volume_usd": {},
            "transactions": {},
        },
        "relationships": {},
    }


class TestFindDeadPoolCandidates:
    def test_deduplicates_pools_seen_on_multiple_pages(self):
        # Le même pool apparaît en page 1 et en page 2 (chevauchement de pagination
        # observé empiriquement sur l'API réelle) : il ne doit être compté qu'une fois.
        pages = {
            1: {"data": [_pool_entry("dead1", "0.0"), _pool_entry("alive1", "5000.0")]},
            2: {"data": [_pool_entry("dead1", "0.0"), _pool_entry("dead2", "0.5")]},
        }
        client = FakeNewPoolsClient(pages)
        candidates = find_dead_pool_candidates(client, page_count=2, max_candidates=10)
        assert candidates.count("dead1") == 1
        assert set(candidates) == {"dead1", "dead2"}

    def test_respects_reserve_threshold(self):
        pages = {1: {"data": [_pool_entry("dead1", "0.0"), _pool_entry("alive1", "1000.0")]}}
        client = FakeNewPoolsClient(pages)
        candidates = find_dead_pool_candidates(client, page_count=1, max_candidates=10)
        assert candidates == ["dead1"]

    def test_caps_at_max_candidates_lowest_reserve_first(self):
        pages = {
            1: {
                "data": [
                    _pool_entry("mid", "0.5"),
                    _pool_entry("lowest", "0.0"),
                    _pool_entry("high", "0.9"),
                ]
            }
        }
        client = FakeNewPoolsClient(pages)
        candidates = find_dead_pool_candidates(client, page_count=1, max_candidates=2)
        assert candidates == ["lowest", "mid"]


class TestRenderReport:
    def test_empty_results_reports_no_candidates(self):
        report = render_report([])
        assert "Aucun candidat" in report

    def test_report_contains_summary_counts(self):
        results = [
            DeadPoolProbeResult("p1", "2026-09-20T00:00:00Z", 0.0, 1.0, True, 1, 1758000000, 1758000000),
            DeadPoolProbeResult("p2", None, None, None, False, 0, None, None),
        ]
        report = render_report(results)
        assert "Pools sondés : 2" in report
        assert "1/2" in report  # résolvables et avec historique
