"""Appels RugCheck.xyz /tokens/{mint}/report : un par pool, au mieux, jamais bloquant.

Même principe d'isolation que token_info.py : un problème sur cet endpoint ne
doit jamais empêcher la collecte de base. Client, budget et hôte totalement
indépendants de GeckoTerminal (voir rugcheck_client.py).

Particularité propre à RugCheck (absente de token_info) : un 404 signifie
probablement que le mint n'est pas encore indexé par RugCheck plutôt qu'un
échec réel — l'audit du 2026-09-29 recommande explicitement de ne jamais le
compter comme un échec (pas de compteur, pas de passage en "indisponible"),
seulement de le journaliser et de retenter au run suivant.

Sélection et reprise : comme token_info, aucun état séparé. La liste des
pools "en attente de rugcheck" est entièrement recalculée à partir des
fichiers déjà écrits à chaque exécution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import SCHEMA_VERSION, RugcheckInfoRecord, RugcheckStatusEvent
from .rugcheck_client import CallBudgetExceeded, RugCheckApiError, RugCheckClient
from .storage import read_all_rows

logger = logging.getLogger(__name__)

# Un mint pas encore indexé par RugCheck (pool très récent) : pas un échec du
# service, ne doit jamais compter contre le pool ni déclencher le coupe-circuit.
NOT_YET_INDEXED_STATUS_CODES = frozenset({404})

# Authentification soudain requise : l'API gratuite s'est probablement fermée
# plutôt qu'un problème ponctuel sur ce token précis.
ENDPOINT_LIKELY_CLOSED_STATUS_CODES = frozenset({401, 402, 403})


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_rugcheck_report(raw: dict[str, Any]) -> dict[str, Any]:
    """Extraction défensive : un champ absent ou d'une forme inattendue
    devient None (ou un compte à zéro) et jamais une exception.

    Champs vérifiés contre de vraies réponses de l'API le 2026-09-30, sur des
    tokens issus de notre propre jeu de données (dont un pool créé quelques
    secondes plus tôt) : la structure est plate (pas d'enveloppe JSON:API
    contrairement à GeckoTerminal).
    """
    raw = raw or {}
    token_extensions = raw.get("token_extensions") or {}
    markets = raw.get("markets") or []
    top_holders = raw.get("topHolders") or []
    risks = raw.get("risks") or []

    # Un token peut apparaître dans plusieurs marchés suivis par RugCheck ;
    # on ne retient que le premier pour lp_locked_pct, faute de pouvoir le
    # faire correspondre de façon fiable au pool précis qu'on suit nous-mêmes.
    # Vérifié le 2026-09-30 sur une vraie réponse : le pourcentage vit sous
    # markets[i].lp.lpLockedPct, pas directement sur l'objet marché.
    primary_market = (markets[0].get("lp") or {}) if markets else {}
    if len(markets) > 1:
        logger.info(
            "rugcheck: %d marchés retournés pour ce token, seul le premier est utilisé pour lp_locked_pct",
            len(markets),
        )

    insider_holders = [h for h in top_holders if h.get("insider")]

    return {
        "score": _to_float(raw.get("score")),
        "score_normalised": _to_float(raw.get("score_normalised")),
        "rugged": raw.get("rugged"),
        "risks_count": len(risks),
        "risks_json": json.dumps(risks, ensure_ascii=False) if risks else None,
        "permanent_delegate_present": token_extensions.get("permanentDelegate") is not None,
        "transfer_hook_present": token_extensions.get("transferHook") is not None,
        "pausable_present": token_extensions.get("pausableConfig") is not None,
        "mint_close_authority_present": token_extensions.get("mintCloseAuthority") is not None,
        "non_transferable": token_extensions.get("nonTransferable"),
        "default_account_state": token_extensions.get("defaultAccountState"),
        "transfer_fee_pct": _to_float((raw.get("transferFee") or {}).get("pct")),
        "lp_locked_pct": _to_float(primary_market.get("lpLockedPct")),
        "locker_scan_status": raw.get("lockerScanStatus"),
        "graph_insiders_detected": _to_int(raw.get("graphInsidersDetected")),
        "top_holder_pct": _to_float(top_holders[0].get("pct")) if top_holders else None,
        "insider_holders_count": len(insider_holders) if top_holders else None,
        "insider_holders_pct_sum": (
            sum(_to_float(h.get("pct")) or 0.0 for h in insider_holders) if top_holders else None
        ),
        "creator": raw.get("creator"),
        "creator_balance": _to_float(raw.get("creatorBalance")),
        "token_program": raw.get("tokenProgram"),
        "metadata_mutable": (raw.get("tokenMeta") or {}).get("mutable"),
    }


@dataclass
class PendingRugcheck:
    pool_address: str
    token_address: str
    discovered_at: datetime
    failure_count: int = 0


def select_pools_pending_rugcheck(registry_dir, rugcheck_dir, rugcheck_status_dir, max_count: int) -> list["PendingRugcheck"]:
    """Pools éligibles à un appel RugCheck, triés du plus ancien découvert au
    plus récent (FIFO), en excluant : les pools déjà réussis, ceux marqués
    "indisponible" (échecs réels répétés), et ceux dont on ne connaît pas
    l'adresse du token de base. Un pool "pas encore indexé" reste éligible
    indéfiniment (jamais exclu), conformément à l'audit."""
    candidates: dict[str, PendingRugcheck] = {}
    for row in read_all_rows(registry_dir):
        token_addr = (row.get("base_token_address") or "").strip()
        if not token_addr:
            continue
        pool_addr = row["pool_address"]
        if pool_addr in candidates:
            continue
        candidates[pool_addr] = PendingRugcheck(
            pool_address=pool_addr,
            token_address=token_addr,
            discovered_at=datetime.fromisoformat(row["discovered_at_utc"]),
        )

    for row in read_all_rows(rugcheck_dir):
        candidates.pop(row["pool_address"], None)

    unavailable: set[str] = set()
    failure_counts: dict[str, int] = {}
    for row in read_all_rows(rugcheck_status_dir):
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
class RugcheckPhaseResult:
    info_rows: list[dict]
    status_rows: list[dict]
    calls_attempted: int
    stopped_reason: str | None  # None | "time_budget" | "circuit_breaker" | "call_budget"


def run_rugcheck_phase(
    client: RugCheckClient,
    pending: list[PendingRugcheck],
    network: str,
    run_started_at: datetime,
    max_total_elapsed: timedelta,
    max_failures_before_unavailable: int,
    circuit_breaker_consecutive_failures: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RugcheckPhaseResult:
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
            raw = client.get_token_report(candidate.token_address)
        except CallBudgetExceeded:
            stopped_reason = "call_budget"
            break
        except RugCheckApiError as exc:
            now_iso = now_fn().isoformat()
            status_code = getattr(exc, "status_code", None)

            if status_code in NOT_YET_INDEXED_STATUS_CODES:
                status_rows.append(
                    RugcheckStatusEvent(
                        pool_address=candidate.pool_address,
                        event_at_utc=now_iso,
                        event_type="not_yet_indexed",
                        detail=str(exc)[:300],
                        schema_version=SCHEMA_VERSION,
                    ).as_row()
                )
                # Pas un échec du service : ne compte ni pour ce pool ni pour le coupe-circuit.
                consecutive_failures = 0
                continue

            status_rows.append(
                RugcheckStatusEvent(
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
                    RugcheckStatusEvent(
                        pool_address=candidate.pool_address,
                        event_at_utc=now_iso,
                        event_type="unavailable",
                        detail=f"{new_failure_count} échecs cumulés",
                        schema_version=SCHEMA_VERSION,
                    ).as_row()
                )

            if status_code in ENDPOINT_LIKELY_CLOSED_STATUS_CODES:
                logger.warning(
                    "rugcheck a répondu %s : authentification peut-être requise désormais, "
                    "arrêt des appels rugcheck pour ce run", status_code,
                )
                stopped_reason = "circuit_breaker"
                break

            consecutive_failures += 1
            if consecutive_failures >= circuit_breaker_consecutive_failures:
                logger.warning(
                    "%d échecs consécutifs sur rugcheck, arrêt pour ce run", consecutive_failures
                )
                stopped_reason = "circuit_breaker"
                break
            continue

        consecutive_failures = 0
        parsed = parse_rugcheck_report(raw)
        info_rows.append(
            RugcheckInfoRecord(
                pool_address=candidate.pool_address,
                token_address=candidate.token_address,
                network=network,
                requested_at_utc=now_fn().isoformat(),
                schema_version=SCHEMA_VERSION,
                **parsed,
            ).as_row()
        )

    return RugcheckPhaseResult(info_rows, status_rows, calls_attempted, stopped_reason)
