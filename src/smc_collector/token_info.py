"""Appels /tokens/{address}/info : un par pool, au mieux, jamais bloquant.

Cet endpoint est gratuit en pratique sur `api.geckoterminal.com/api/v2` (vérifié
le 2026-09-21) mais documenté comme réservé à un plan payant sur la nouvelle
API CoinGecko — il peut donc se fermer sans préavis. Toute la logique de
détection et d'arrêt propre (coupe-circuit) vit ici, isolée du reste de la
collecte : un problème sur cet endpoint ne doit jamais empêcher la prise
d'instantanés de base (prix, liquidité, volume).

Sélection et reprise : aucun état séparé. À chaque exécution, la liste des
pools encore "en attente d'info" est entièrement recalculée à partir des
fichiers déjà écrits (registre des pools, succès déjà obtenus, échecs déjà
comptés) — même principe que le reste du projet.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .http_client import CallBudgetExceeded, GeckoTerminalApiError, GeckoTerminalClient
from .models import SCHEMA_VERSION, TokenInfoRecord, TokenInfoStatusEvent
from .storage import read_all_rows

logger = logging.getLogger(__name__)

# Un 401/402/403 signifie presque certainement que l'endpoint gratuit s'est
# fermé (authentification désormais requise) plutôt qu'un problème ponctuel
# sur ce token précis : on arrête tout de suite plutôt que d'épuiser le
# budget d'appels sur des échecs qui ne se résoudront pas ce run.
ENDPOINT_LIKELY_CLOSED_STATUS_CODES = frozenset({401, 402, 403})


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_token_info(raw: dict[str, Any]) -> dict[str, Any]:
    """Extraction défensive : un champ absent ou d'une forme inattendue
    devient None et une ligne de log, jamais une exception."""
    attrs = ((raw or {}).get("data") or {}).get("attributes") or {}
    holders = attrs.get("holders") or {}
    distribution = holders.get("distribution_percentage") or {}

    known_tranches = {"top_10", "11_20", "21_40", "rest"}
    unexpected = set(distribution.keys()) - known_tranches
    if unexpected:
        logger.warning(
            "tokens/info : tranches de détenteurs inattendues ignorées : %s", sorted(unexpected)
        )

    honeypot = attrs.get("is_honeypot")

    return {
        "gt_score": _to_float(attrs.get("gt_score")),
        "holders_top10_pct": _to_float(distribution.get("top_10")),
        "holders_11_20_pct": _to_float(distribution.get("11_20")),
        "holders_21_40_pct": _to_float(distribution.get("21_40")),
        "holders_rest_pct": _to_float(distribution.get("rest")),
        "mint_authority": attrs.get("mint_authority"),
        "freeze_authority": attrs.get("freeze_authority"),
        "developer_holding_percentage": _to_float(attrs.get("developer_holding_percentage")),
        # Jamais interprété : "unknown" reste "unknown", un booléen devient sa
        # représentation texte telle quelle.
        "is_honeypot": None if honeypot is None else str(honeypot),
    }


@dataclass
class PendingTokenInfo:
    pool_address: str
    base_token_address: str
    discovered_at: datetime
    failure_count: int = 0


def select_pools_pending_info(registry_dir, token_info_dir, token_info_status_dir, max_count: int) -> list[PendingTokenInfo]:
    """Pools éligibles à un appel /tokens/info, triés du plus ancien découvert
    au plus récent (FIFO), en excluant : les pools déjà réussis, ceux marqués
    "unavailable", et ceux dont on ne connaît pas l'adresse du token de base
    (pools découverts avant l'ajout de cette colonne — voir models.py v2)."""
    candidates: dict[str, PendingTokenInfo] = {}
    for row in read_all_rows(registry_dir):
        base_addr = (row.get("base_token_address") or "").strip()
        if not base_addr:
            continue
        pool_addr = row["pool_address"]
        if pool_addr in candidates:
            continue
        candidates[pool_addr] = PendingTokenInfo(
            pool_address=pool_addr,
            base_token_address=base_addr,
            discovered_at=datetime.fromisoformat(row["discovered_at_utc"]),
        )

    for row in read_all_rows(token_info_dir):
        candidates.pop(row["pool_address"], None)

    unavailable: set[str] = set()
    failure_counts: dict[str, int] = {}
    for row in read_all_rows(token_info_status_dir):
        addr = row["pool_address"]
        if row["event_type"] == "fetch_failed":
            failure_counts[addr] = failure_counts.get(addr, 0) + 1
        elif row["event_type"] == "unavailable":
            unavailable.add(addr)

    pending = [c for addr, c in candidates.items() if addr not in unavailable]
    for c in pending:
        c.failure_count = failure_counts.get(c.pool_address, 0)
    pending.sort(key=lambda c: c.discovered_at)
    return pending[:max_count]


@dataclass
class TokenInfoPhaseResult:
    info_rows: list[dict]
    status_rows: list[dict]
    calls_attempted: int
    stopped_reason: str | None  # None | "time_budget" | "circuit_breaker" | "call_budget"


def run_token_info_phase(
    client: GeckoTerminalClient,
    pending: list[PendingTokenInfo],
    network: str,
    run_started_at: datetime,
    max_total_elapsed: timedelta,
    max_failures_before_unavailable: int,
    circuit_breaker_consecutive_failures: int,
    max_raw_json_bytes: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TokenInfoPhaseResult:
    info_rows: list[dict] = []
    status_rows: list[dict] = []
    consecutive_failures = 0
    stopped_reason: str | None = None
    calls_attempted = 0

    for candidate in pending:
        if now_fn() - run_started_at > max_total_elapsed:
            stopped_reason = "time_budget"
            break

        try:
            calls_attempted += 1
            raw = client.get_token_info(candidate.base_token_address)
        except CallBudgetExceeded:
            stopped_reason = "call_budget"
            break
        except GeckoTerminalApiError as exc:
            now_iso = now_fn().isoformat()
            status_rows.append(
                TokenInfoStatusEvent(
                    pool_address=candidate.pool_address,
                    event_at_utc=now_iso,
                    event_type="fetch_failed",
                    detail=str(exc)[:300],
                    schema_version=SCHEMA_VERSION,
                ).as_row()
            )
            new_failure_count = candidate.failure_count + 1
            if new_failure_count >= max_failures_before_unavailable:
                status_rows.append(
                    TokenInfoStatusEvent(
                        pool_address=candidate.pool_address,
                        event_at_utc=now_iso,
                        event_type="unavailable",
                        detail=f"{new_failure_count} échecs cumulés",
                        schema_version=SCHEMA_VERSION,
                    ).as_row()
                )

            status_code = getattr(exc, "status_code", None)
            if status_code in ENDPOINT_LIKELY_CLOSED_STATUS_CODES:
                logger.warning(
                    "tokens/info a répondu %s : l'endpoint gratuit semble fermé, "
                    "arrêt des appels info pour ce run", status_code,
                )
                stopped_reason = "circuit_breaker"
                break

            consecutive_failures += 1
            if consecutive_failures >= circuit_breaker_consecutive_failures:
                logger.warning(
                    "%d échecs consécutifs sur tokens/info, arrêt pour ce run", consecutive_failures
                )
                stopped_reason = "circuit_breaker"
                break
            continue

        consecutive_failures = 0
        parsed = parse_token_info(raw)
        raw_json_str = json.dumps(raw, ensure_ascii=False)
        raw_to_store = raw_json_str if len(raw_json_str.encode("utf-8")) < max_raw_json_bytes else None

        info_rows.append(
            TokenInfoRecord(
                pool_address=candidate.pool_address,
                base_token_address=candidate.base_token_address,
                network=network,
                requested_at_utc=now_fn().isoformat(),
                raw_response_json=raw_to_store,
                schema_version=SCHEMA_VERSION,
                **parsed,
            ).as_row()
        )

    return TokenInfoPhaseResult(info_rows, status_rows, calls_attempted, stopped_reason)
