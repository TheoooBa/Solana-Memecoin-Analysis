"""Client HTTP pour l'API publique RugCheck.xyz (détection anti-honeypot réelle).

Endpoint : GET /tokens/{mint}/report, sans authentification — vérifié par
appels réels le 2026-09-29 (voir reports/rugcheck_audit_2026-09-29.md) et
reconfirmé juste avant l'implémentation, y compris sur des pools découverts
quelques secondes plus tôt (indexation apparemment immédiate, contrairement
à la prudence initiale de l'audit). En-tête de débit observé
(`x-rate-limit-limit: 15`) sans fenêtre temporelle confirmée par la
documentation : mêmes précautions que pour GeckoTerminal à l'origine (débit
conservateur, reprises avec délai, coupe-circuit sur échecs en série).

Client séparé de `GeckoTerminalClient` (hôte différent, aucune clé, budget
d'appels indépendant) mais même mécanique de limitation de débit
(`RateLimiter`, réutilisé depuis http_client.py). Contrairement à
`GeckoTerminalClient`, aucune réponse brute n'est archivée sur disque ici :
l'audit a déjà conclu qu'elle est trop volumineuse (~12 Ko) pour la valeur
des champs non retenus, et le cache de GeckoTerminal lui-même n'est de toute
façon jamais persisté d'une exécution GitHub Actions à l'autre (répertoire
non versionné) — l'archiver ici n'apporterait donc aucune traçabilité réelle.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .http_client import CallBudgetExceeded, RateLimiter

logger = logging.getLogger(__name__)


class RugCheckApiError(Exception):
    """Levée quand un appel à RugCheck échoue définitivement après reprises.

    `status_code` (None si exception réseau) permet à rugcheck.py de
    distinguer un 404 (mint pas encore indexé, à retenter au run suivant,
    jamais compté comme un échec) d'un 401/403 (authentification devenue
    nécessaire : l'API gratuite pourrait s'être fermée, arrêt immédiat) et
    d'un échec ordinaire.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RugCheckClient:
    def __init__(
        self,
        base_url: str,
        calls_per_minute: float,
        max_calls_per_run: int,
        request_timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        session: requests.Session | None = None,
        sleep_fn=time.sleep,
        time_fn=time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_calls_per_run = max_calls_per_run
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._time = time_fn
        self._limiter = RateLimiter(calls_per_minute=calls_per_minute, time_fn=time_fn, sleep_fn=sleep_fn)

        self.calls_made = 0
        self.errors: list[str] = []

    def get_token_report(self, mint_address: str) -> dict[str, Any]:
        return self._get(f"/tokens/{mint_address}/report", endpoint_name="rugcheck_report")

    def _get(self, path: str, endpoint_name: str) -> dict[str, Any]:
        if self.calls_made >= self.max_calls_per_run:
            raise CallBudgetExceeded(
                f"Budget d'appels RugCheck épuisé ({self.calls_made}/{self.max_calls_per_run})"
            )

        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.calls_made >= self.max_calls_per_run:
                raise CallBudgetExceeded(
                    f"Budget d'appels RugCheck épuisé ({self.calls_made}/{self.max_calls_per_run})"
                )
            self._limiter.wait_for_slot()
            self.calls_made += 1
            try:
                response = self._session.get(
                    url, headers={"Accept": "application/json"}, timeout=self.request_timeout_seconds
                )
            except requests.RequestException as exc:
                last_exc = exc
                self.errors.append(f"{endpoint_name}: exception réseau ({exc}) tentative {attempt}")
                self._backoff(attempt, retry_after=None)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                self.errors.append(f"{endpoint_name}: HTTP {response.status_code} tentative {attempt}")
                if attempt < self.max_retries:
                    self._backoff(attempt, retry_after=retry_after)
                    continue
                raise RugCheckApiError(
                    f"{endpoint_name}: échec définitif après {attempt} tentatives (HTTP {response.status_code})",
                    status_code=response.status_code,
                )

            # 4xx non transitoire (404 "pas encore indexé" compris) : inutile de réessayer ici,
            # rugcheck.py décide de la suite (retenter au run suivant ou compter comme échec).
            raise RugCheckApiError(
                f"{endpoint_name}: HTTP {response.status_code} non transitoire : {response.text[:300]}",
                status_code=response.status_code,
            )

        raise RugCheckApiError(f"{endpoint_name}: échec après {self.max_retries} tentatives ({last_exc})")

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
