"""Commande `diagnose-dead-pools` : vérifie si l'historique OHLCV reste
accessible pour des pools déjà morts, et produit un rapport.

« Mort » ici est défini simplement pour les besoins du diagnostic : réserve de
liquidité (reserve_usd) proche de zéro alors que le pool a déjà quelques
minutes d'existence. Cette définition n'a aucune valeur d'analyse (elle ne
sert pas à décider quoi que ce soit dans le simulateur, qui viendra à l'étape
3 avec ses propres seuils gelés) : c'est un outil ponctuel pour répondre à la
question opérationnelle « l'historique existe-t-il encore pour un pool mort ? »
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import CollectionConfig
from .http_client import GeckoTerminalApiError, GeckoTerminalClient
from .parsing import parse_ohlcv_candles, parse_pool_list

logger = logging.getLogger(__name__)

DEAD_POOL_RESERVE_THRESHOLD_USD = 1.0


@dataclass
class DeadPoolProbeResult:
    pool_address: str
    pool_created_at: str | None
    reserve_usd: float | None
    volume_usd_h24: float | None
    still_resolvable_via_multi: bool
    ohlcv_candle_count: int
    ohlcv_oldest_ts: int | None
    ohlcv_newest_ts: int | None


def find_dead_pool_candidates(client: GeckoTerminalClient, page_count: int, max_candidates: int) -> list[str]:
    """Cherche, parmi les nouveaux pools, ceux dont la liquidité est déjà
    quasi nulle : candidats plausibles de pools déjà morts, pour tester
    empiriquement la disponibilité de leur historique.
    """
    candidates: dict[str, float] = {}  # adresse -> réserve ; déduplique les pools vus sur plusieurs pages
    for page in range(1, page_count + 1):
        raw = client.get_new_pools(page=page)
        for pool in parse_pool_list(raw):
            reserve = pool.reserve_usd if pool.reserve_usd is not None else float("inf")
            if reserve <= DEAD_POOL_RESERVE_THRESHOLD_USD:
                candidates[pool.pool_address] = reserve
    ordered = sorted(candidates.items(), key=lambda item: item[1])
    return [addr for addr, _ in ordered[:max_candidates]]


def probe_pools(client: GeckoTerminalClient, addresses: list[str], config: CollectionConfig) -> list[DeadPoolProbeResult]:
    results = []
    for i in range(0, len(addresses), 30):
        batch = addresses[i : i + 30]
        try:
            multi_raw = client.get_multi_pools(batch)
        except GeckoTerminalApiError as exc:
            logger.warning("Sondage multi-pools en échec pour %s : %s", batch, exc)
            multi_raw = {"data": []}
        resolved = {p.pool_address: p for p in parse_pool_list(multi_raw)}

        for addr in batch:
            pool = resolved.get(addr)
            try:
                ohlcv_raw = client.get_pool_ohlcv(
                    pool_address=addr,
                    timeframe=config.ohlcv_backfill.timeframe,
                    aggregate=config.ohlcv_backfill.aggregate,
                    limit=config.ohlcv_backfill.max_candles_per_pool,
                )
                candles = parse_ohlcv_candles(ohlcv_raw)
            except GeckoTerminalApiError as exc:
                logger.warning("Sondage OHLCV en échec pour %s : %s", addr, exc)
                candles = []

            results.append(
                DeadPoolProbeResult(
                    pool_address=addr,
                    pool_created_at=pool.pool_created_at if pool else None,
                    reserve_usd=pool.reserve_usd if pool else None,
                    volume_usd_h24=pool.volume_usd_h24 if pool else None,
                    still_resolvable_via_multi=pool is not None,
                    ohlcv_candle_count=len(candles),
                    ohlcv_oldest_ts=candles[0][0] if candles else None,
                    ohlcv_newest_ts=candles[-1][0] if candles else None,
                )
            )
    return results


def render_report(results: list[DeadPoolProbeResult]) -> str:
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Rapport : disponibilité de l'historique OHLCV pour des pools morts",
        "",
        f"Généré le {now}. Source : GeckoTerminal (attribution requise, voir README).",
        "",
        "Définition utilisée UNIQUEMENT pour ce diagnostic ponctuel (pas une "
        "définition d'analyse gelée) : un pool est considéré « mort » si sa "
        f"réserve de liquidité déclarée est ≤ {DEAD_POOL_RESERVE_THRESHOLD_USD} $ "
        "peu après sa création.",
        "",
        f"Pools sondés : {len(results)}",
        "",
    ]
    if not results:
        lines.append("Aucun candidat trouvé sur les pages de nouveaux pools consultées.")
        return "\n".join(lines) + "\n"

    resolvable = sum(1 for r in results if r.still_resolvable_via_multi)
    with_history = sum(1 for r in results if r.ohlcv_candle_count > 0)
    lines += [
        f"- Toujours résolvables via l'endpoint multi-pools : {resolvable}/{len(results)}",
        f"- Avec au moins une bougie OHLCV disponible : {with_history}/{len(results)}",
        "",
        "| Adresse | Créé le | Réserve $ | Volume 24h $ | Résolvable | Bougies OHLCV | Plage temporelle |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        span = ""
        if r.ohlcv_oldest_ts and r.ohlcv_newest_ts:
            oldest = datetime.fromtimestamp(r.ohlcv_oldest_ts, tz=timezone.utc).isoformat()
            newest = datetime.fromtimestamp(r.ohlcv_newest_ts, tz=timezone.utc).isoformat()
            span = f"{oldest} → {newest}"
        lines.append(
            f"| {r.pool_address} | {r.pool_created_at or '?'} | {r.reserve_usd} | "
            f"{r.volume_usd_h24} | {'oui' if r.still_resolvable_via_multi else 'non'} | "
            f"{r.ohlcv_candle_count} | {span} |"
        )
    lines.append("")
    lines.append(
        "**Lecture prudente** : cet échantillon ne couvre que des pools morts "
        "*très récemment* (quelques minutes à quelques heures avant le sondage), "
        "trouvés sur les premières pages de `new_pools`. Il ne dit rien sur la "
        "disponibilité de l'historique pour des pools morts depuis plusieurs "
        "semaines ou mois — seul le suivi 48h en conditions réelles (étape "
        "collecte GitHub Actions) permettra de vérifier ce point sur la durée."
    )
    return "\n".join(lines) + "\n"


def run_diagnose_dead_pools(
    config: CollectionConfig, output_path: Path, page_count: int = 3, max_candidates: int = 10
) -> Path:
    client = GeckoTerminalClient(
        base_url=config.api.base_url,
        network=config.api.network,
        calls_per_minute=config.api.calls_per_minute,
        max_calls_per_run=config.api.max_calls_per_run,
        request_timeout_seconds=config.api.request_timeout_seconds,
        max_retries=config.api.max_retries,
        backoff_base_seconds=config.api.backoff_base_seconds,
        backoff_max_seconds=config.api.backoff_max_seconds,
        cache_dir=config.storage.cache_dir,
        api_key=config.api.api_key,
        api_key_header=config.api.api_key_header,
    )
    candidates = find_dead_pool_candidates(client, page_count=page_count, max_candidates=max_candidates)
    results = probe_pools(client, candidates, config)
    report = render_report(results)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logger.info("Rapport écrit dans %s (%d appels HTTP consommés)", output_path, client.calls_made)
    return output_path
