"""Client HTTP pour l'API publique GeckoTerminal.

Responsabilités :
- limiter le débit d'appels (calls_per_minute) ;
- reprises avec délai exponentiel sur 429 / 5xx, en respectant l'en-tête
  Retry-After quand elle est présente ;
- couper l'exécution dès qu'un budget d'appels par run est atteint ;
- archiver chaque réponse brute reçue sur disque (traçabilité, rejouabilité).

Ce client ne garde aucun état entre deux exécutions du programme : à chaque
lancement, un nouveau client est créé et repart d'un débit prudent dès le
premier appel (voir README, section "Débit observé", pour la justification
de la valeur par défaut).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


class CallBudgetExceeded(Exception):
    """Levée quand le budget d'appels HTTP de l'exécution est épuisé."""


class GeckoTerminalApiError(Exception):
    """Levée quand un appel échoue définitivement après toutes les reprises."""


@dataclass
class CallRecord:
    endpoint: str
    url: str
    status_code: int | None
    attempt: int
    duration_seconds: float
    cached_at: Path | None


@dataclass
class RateLimiter:
    """Limiteur de débit à fenêtre glissante, simple et déterministe.

    `time_fn`/`sleep_fn` sont injectables pour permettre aux tests de simuler
    l'écoulement du temps sans attendre réellement.
    """

    calls_per_minute: float
    time_fn: Any = time.monotonic
    sleep_fn: Any = time.sleep
    _timestamps: deque[float] = field(default_factory=deque)

    def wait_for_slot(self) -> None:
        if self.calls_per_minute <= 0:
            return
        min_interval = 60.0 / self.calls_per_minute
        now = self.time_fn()
        if self._timestamps:
            elapsed = now - self._timestamps[-1]
            if elapsed < min_interval:
                self.sleep_fn(min_interval - elapsed)
        self._timestamps.append(self.time_fn())


class GeckoTerminalClient:
    def __init__(
        self,
        base_url: str,
        network: str,
        calls_per_minute: float,
        max_calls_per_run: int,
        request_timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        cache_dir: Path,
        api_key: str | None = None,
        api_key_header: str = "x-cg-demo-api-key",
        session: requests.Session | None = None,
        sleep_fn=time.sleep,
        time_fn=time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.network = network
        self.max_calls_per_run = max_calls_per_run
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.cache_dir = Path(cache_dir)
        self.api_key = api_key
        self.api_key_header = api_key_header
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._time = time_fn
        self._limiter = RateLimiter(calls_per_minute=calls_per_minute, time_fn=time_fn, sleep_fn=sleep_fn)

        self.calls_made = 0
        self.call_records: list[CallRecord] = []
        self.errors: list[str] = []

    # -- Endpoints ---------------------------------------------------------

    def get_trending_pools(self, page: int = 1) -> dict[str, Any]:
        path = f"/networks/{self.network}/trending_pools"
        params = {"page": page, "include": "base_token,quote_token,dex"}
        return self._get(path, params, endpoint_name="trending_pools")

    def get_new_pools(self, page: int = 1) -> dict[str, Any]:
        path = f"/networks/{self.network}/new_pools"
        params = {"page": page, "include": "base_token,quote_token,dex"}
        return self._get(path, params, endpoint_name="new_pools")

    def get_multi_pools(self, addresses: list[str]) -> dict[str, Any]:
        if not addresses:
            return {"data": [], "included": []}
        if len(addresses) > 30:
            raise ValueError("L'API accepte au maximum 30 adresses par appel multi-pools")
        joined = ",".join(addresses)
        path = f"/networks/{self.network}/pools/multi/{joined}"
        params = {"include": "base_token,quote_token,dex"}
        return self._get(path, params, endpoint_name="multi_pools")

    def get_pool_ohlcv(
        self,
        pool_address: str,
        timeframe: str,
        aggregate: int,
        before_timestamp: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        path = f"/networks/{self.network}/pools/{pool_address}/ohlcv/{timeframe}"
        params: dict[str, Any] = {"aggregate": aggregate, "limit": limit}
        if before_timestamp is not None:
            params["before_timestamp"] = before_timestamp
        return self._get(path, params, endpoint_name="ohlcv")

    # -- Mécanique interne ---------------------------------------------------

    def _get(self, path: str, params: dict[str, Any], endpoint_name: str) -> dict[str, Any]:
        if self.calls_made >= self.max_calls_per_run:
            raise CallBudgetExceeded(
                f"Budget d'appels épuisé ({self.calls_made}/{self.max_calls_per_run})"
            )

        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers[self.api_key_header] = self.api_key

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.calls_made >= self.max_calls_per_run:
                raise CallBudgetExceeded(
                    f"Budget d'appels épuisé ({self.calls_made}/{self.max_calls_per_run})"
                )
            self._limiter.wait_for_slot()
            self.calls_made += 1
            started = time.monotonic()
            try:
                response = self._session.get(
                    url, params=params, headers=headers, timeout=self.request_timeout_seconds
                )
            except requests.RequestException as exc:
                last_exc = exc
                self.errors.append(f"{endpoint_name}: exception réseau ({exc}) tentative {attempt}")
                self._backoff(attempt, retry_after=None)
                continue
            duration = time.monotonic() - started

            if response.status_code == 200:
                payload = response.json()
                cached_at = self._write_cache(endpoint_name, url, params, payload)
                self.call_records.append(
                    CallRecord(endpoint_name, url, 200, attempt, duration, cached_at)
                )
                return payload

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                self.errors.append(
                    f"{endpoint_name}: HTTP {response.status_code} tentative {attempt}"
                )
                self.call_records.append(
                    CallRecord(endpoint_name, url, response.status_code, attempt, duration, None)
                )
                if attempt < self.max_retries:
                    self._backoff(attempt, retry_after=retry_after)
                    continue
                raise GeckoTerminalApiError(
                    f"{endpoint_name}: échec définitif après {attempt} tentatives "
                    f"(HTTP {response.status_code})"
                )

            # Erreur non transitoire (4xx hors 429) : inutile de réessayer.
            self.call_records.append(
                CallRecord(endpoint_name, url, response.status_code, attempt, duration, None)
            )
            raise GeckoTerminalApiError(
                f"{endpoint_name}: HTTP {response.status_code} non transitoire : {response.text[:300]}"
            )

        raise GeckoTerminalApiError(
            f"{endpoint_name}: échec après {self.max_retries} tentatives ({last_exc})"
        )

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            delay = retry_after
        else:
            delay = min(self.backoff_base_seconds * (2 ** (attempt - 1)), self.backoff_max_seconds)
        self._sleep(delay)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _write_cache(
        self, endpoint_name: str, url: str, params: dict[str, Any], payload: dict[str, Any]
    ) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self.cache_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{url}?{sorted(params.items())}".encode()).hexdigest()[:12]
        filename = f"{now.strftime('%H%M%S%f')}_{endpoint_name}_{digest}.json"
        out_path = day_dir / filename
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                {"request_url": url, "request_params": params, "fetched_at": now.isoformat(), "body": payload},
                f,
                ensure_ascii=False,
            )
        return out_path
