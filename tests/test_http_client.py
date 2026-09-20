import json

import pytest

from smc_collector.http_client import (
    CallBudgetExceeded,
    GeckoTerminalApiError,
    GeckoTerminalClient,
    RateLimiter,
)


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    """Rejoue une séquence de réponses préparées à l'avance, une par appel .get()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self._responses:
            raise AssertionError("Plus de réponses préparées pour ce test")
        return self._responses.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time_fn(self):
        return self.now

    def sleep_fn(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, responses, **kwargs):
    clock = FakeClock()
    client = GeckoTerminalClient(
        base_url="https://api.geckoterminal.com/api/v2",
        network="solana",
        calls_per_minute=kwargs.pop("calls_per_minute", 1000),  # rapide par défaut dans les tests
        max_calls_per_run=kwargs.pop("max_calls_per_run", 100),
        request_timeout_seconds=5.0,
        max_retries=kwargs.pop("max_retries", 3),
        backoff_base_seconds=1.0,
        backoff_max_seconds=10.0,
        cache_dir=tmp_path / "cache",
        session=FakeSession(responses),
        sleep_fn=clock.sleep_fn,
        time_fn=clock.time_fn,
        **kwargs,
    )
    return client, clock


class TestRateLimiter:
    def test_spaces_calls_according_to_calls_per_minute(self):
        clock = FakeClock()
        limiter = RateLimiter(calls_per_minute=60, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
        limiter.wait_for_slot()
        limiter.wait_for_slot()
        assert clock.sleeps == [1.0]  # 60 appels/min => 1s minimum entre deux appels

    def test_no_sleep_if_enough_time_elapsed(self):
        clock = FakeClock()
        limiter = RateLimiter(calls_per_minute=60, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
        limiter.wait_for_slot()
        clock.now += 5.0
        limiter.wait_for_slot()
        assert clock.sleeps == []


class TestGeckoTerminalClientSuccess:
    def test_get_trending_pools_returns_parsed_json(self, tmp_path):
        body = {"data": [{"id": "1"}]}
        client, _ = make_client(tmp_path, [FakeResponse(200, body)])
        result = client.get_trending_pools()
        assert result == body
        assert client.calls_made == 1

    def test_successful_response_is_cached_to_disk(self, tmp_path):
        body = {"data": []}
        client, _ = make_client(tmp_path, [FakeResponse(200, body)])
        client.get_new_pools()
        cached_files = list((tmp_path / "cache").rglob("*.json"))
        assert len(cached_files) == 1
        cached = json.loads(cached_files[0].read_text())
        assert cached["body"] == body

    def test_multi_pools_rejects_more_than_30_addresses(self, tmp_path):
        client, _ = make_client(tmp_path, [])
        with pytest.raises(ValueError):
            client.get_multi_pools([f"addr{i}" for i in range(31)])

    def test_multi_pools_empty_list_returns_empty_without_calling(self, tmp_path):
        client, _ = make_client(tmp_path, [])
        result = client.get_multi_pools([])
        assert result == {"data": [], "included": []}
        assert client.calls_made == 0


class TestGeckoTerminalClientRetries:
    def test_retries_on_429_then_succeeds(self, tmp_path):
        responses = [
            FakeResponse(429, headers={"Retry-After": "2"}),
            FakeResponse(200, {"data": []}),
        ]
        client, clock = make_client(tmp_path, responses)
        result = client.get_trending_pools()
        assert result == {"data": []}
        assert client.calls_made == 2
        assert 2.0 in clock.sleeps  # a respecté Retry-After

    def test_exhausts_retries_and_raises(self, tmp_path):
        responses = [FakeResponse(500) for _ in range(3)]
        client, _ = make_client(tmp_path, responses, max_retries=3)
        with pytest.raises(GeckoTerminalApiError):
            client.get_trending_pools()
        assert client.calls_made == 3

    def test_non_transitory_4xx_does_not_retry(self, tmp_path):
        responses = [FakeResponse(404, text="not found")]
        client, _ = make_client(tmp_path, responses, max_retries=5)
        with pytest.raises(GeckoTerminalApiError):
            client.get_trending_pools()
        assert client.calls_made == 1  # aucune tentative supplémentaire


class TestCallBudget:
    def test_raises_call_budget_exceeded_before_request(self, tmp_path):
        client, _ = make_client(tmp_path, [], max_calls_per_run=0)
        with pytest.raises(CallBudgetExceeded):
            client.get_trending_pools()

    def test_budget_counts_retries(self, tmp_path):
        responses = [FakeResponse(500), FakeResponse(500), FakeResponse(200, {"data": []})]
        client, _ = make_client(tmp_path, responses, max_calls_per_run=2, max_retries=5)
        with pytest.raises(CallBudgetExceeded):
            client.get_trending_pools()
        assert client.calls_made == 2
