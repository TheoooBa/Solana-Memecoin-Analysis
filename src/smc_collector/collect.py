"""Commande `collect` : une exécution unique, idempotente, sans état local.

Séquence d'un run :
1. Charger la config et reconstruire l'univers suivi depuis les fichiers déjà
   écrits (aucun état séparé requis).
2. Récupérer la liste "tendance" et une page de "nouveaux pools".
3. Ajouter au suivi les pools tendance jamais vus, et un échantillon
   déterministe des nouveaux pools (groupe témoin).
4. Marquer comme terminés les pools dont la fenêtre de suivi est dépassée.
5. Prendre un instantané des pools actifs restants (endpoint multi-pools,
   par lots de 30).
6. Pour les pools découverts CE run, reconstituer leur historique antérieur
   via OHLCV (bougies marquées source="ohlcv_backfill").
7. Journaliser le run (appels, erreurs, pools vus) pour repérer les trous.

Le budget d'appels (config.api.max_calls_per_run) peut être atteint avant la
fin de cette séquence : dans ce cas, le run s'arrête proprement et journalise
exit_reason="budget_exhausted". Ce n'est pas une erreur : c'est attendu sous
un débit gratuit, et l'exécution suivante reprendra les pools restants.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from .config import CollectionConfig
from .http_client import CallBudgetExceeded, GeckoTerminalApiError, GeckoTerminalClient
from .models import PoolDiscovery, PoolSnapshot, PoolStatusEvent, RunLog
from .parsing import ParsedPool, parse_ohlcv_candles, parse_pool_list
from .registry import load_universe, select_new_discoveries
from .storage import AppendOnlyCsvWriter, utc_now_iso

logger = logging.getLogger(__name__)


def _snapshot_from_parsed(
    pool: ParsedPool,
    network: str,
    request_timestamp: str,
    source: str = "snapshot",
    raw_cache_path: str | None = None,
) -> PoolSnapshot:
    return PoolSnapshot(
        request_timestamp_utc=request_timestamp,
        pool_address=pool.pool_address,
        network=network,
        dex=pool.dex,
        base_token_address=pool.base_token_address,
        base_token_symbol=pool.base_token_symbol,
        quote_token_address=pool.quote_token_address,
        quote_token_symbol=pool.quote_token_symbol,
        pool_created_at=pool.pool_created_at,
        price_usd=pool.price_usd,
        fdv_usd=pool.fdv_usd,
        market_cap_usd=pool.market_cap_usd,
        reserve_usd=pool.reserve_usd,
        volume_usd_m5=pool.volume_usd_m5,
        volume_usd_h1=pool.volume_usd_h1,
        volume_usd_h24=pool.volume_usd_h24,
        buys_m5=pool.buys_m5,
        sells_m5=pool.sells_m5,
        buyers_m5=pool.buyers_m5,
        sellers_m5=pool.sellers_m5,
        buys_h1=pool.buys_h1,
        sells_h1=pool.sells_h1,
        buyers_h1=pool.buyers_h1,
        sellers_h1=pool.sellers_h1,
        buys_h24=pool.buys_h24,
        sells_h24=pool.sells_h24,
        buyers_h24=pool.buyers_h24,
        sellers_h24=pool.sellers_h24,
        source=source,
        ohlcv_timeframe=None,
        ohlcv_open=None,
        ohlcv_high=None,
        ohlcv_low=None,
        ohlcv_close=None,
        ohlcv_volume=None,
        raw_cache_path=raw_cache_path,
    )


def run_collect(config: CollectionConfig) -> RunLog:
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    logger.info("Run %s démarré à %s", run_id, started_at.isoformat())

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

    snapshot_writer = AppendOnlyCsvWriter(config.snapshots_dir, PoolSnapshot.fieldnames())
    registry_writer = AppendOnlyCsvWriter(config.registry_dir, PoolDiscovery.fieldnames())
    status_writer = AppendOnlyCsvWriter(config.status_dir, PoolStatusEvent.fieldnames())

    exit_reason = "completed"
    pools_discovered_trending = 0
    pools_discovered_random = 0
    pools_snapshotted = 0
    pools_backfilled = 0
    pools_status_events = 0

    try:
        universe = load_universe(config.registry_dir, config.status_dir)
        already_tracked = set(universe.keys())

        trending_raw = client.get_trending_pools(page=1)
        trending_pools = parse_pool_list(trending_raw)

        new_pools_all: list[ParsedPool] = []
        for page in range(1, config.univers.new_pools_page_count + 1):
            new_pools_raw = client.get_new_pools(page=page)
            new_pools_all.extend(parse_pool_list(new_pools_raw))

        discoveries = select_new_discoveries(
            trending_pools=trending_pools[: config.univers.trending_pool_count],
            new_pools=new_pools_all,
            already_tracked=already_tracked,
            network=config.api.network,
            sampling_seed=config.univers.sampling_seed,
            sample_probability=config.univers.new_pools_sample_probability,
            tracking_duration_hours=config.univers.tracking_duration_hours,
            now=started_at,
        )
        if discoveries:
            registry_writer.append_rows([d.as_row() for d in discoveries], dt=started_at)
        pools_discovered_trending = sum(1 for d in discoveries if d.group == "trending")
        pools_discovered_random = sum(1 for d in discoveries if d.group == "random_new")

        # Reconstruit l'univers actif après ajout des découvertes de ce run.
        universe = load_universe(config.registry_dir, config.status_dir)

        active_addresses = [addr for addr, state in universe.items() if state.is_active(started_at)]
        newly_ended = [addr for addr, state in universe.items() if not state.stopped and started_at >= state.tracking_until]

        status_events = [
            PoolStatusEvent(
                pool_address=addr,
                event_at_utc=started_at.isoformat(),
                event_type="tracking_window_ended",
                detail="Fenêtre de suivi (tracking_duration_hours) dépassée.",
            )
            for addr in newly_ended
        ]

        # -- Instantanés des pools actifs, par lots de 30 --------------------
        batch_size = 30
        request_timestamp = utc_now_iso()
        for i in range(0, len(active_addresses), batch_size):
            batch = active_addresses[i : i + batch_size]
            try:
                multi_raw = client.get_multi_pools(batch)
            except CallBudgetExceeded:
                exit_reason = "budget_exhausted"
                break
            except GeckoTerminalApiError as exc:
                logger.warning("Échec instantané pour un lot de pools : %s", exc)
                continue

            returned_pools = parse_pool_list(multi_raw)
            returned_addresses = {p.pool_address for p in returned_pools}
            cache_path = str(client.call_records[-1].cached_at) if client.call_records else None

            snapshot_rows = [
                _snapshot_from_parsed(p, config.api.network, request_timestamp, raw_cache_path=cache_path).as_row()
                for p in returned_pools
            ]
            pools_snapshotted += snapshot_writer.append_rows(snapshot_rows, dt=started_at)

            for addr in batch:
                if addr not in returned_addresses:
                    status_events.append(
                        PoolStatusEvent(
                            pool_address=addr,
                            event_at_utc=request_timestamp,
                            event_type="missing_from_multi_pool_response",
                            detail="Adresse demandée absente de la réponse multi-pools.",
                        )
                    )

        # -- Backfill OHLCV pour les pools découverts ce run -----------------
        if exit_reason != "budget_exhausted":
            for d in discoveries:
                try:
                    ohlcv_raw = client.get_pool_ohlcv(
                        pool_address=d.pool_address,
                        timeframe=config.ohlcv_backfill.timeframe,
                        aggregate=config.ohlcv_backfill.aggregate,
                        limit=config.ohlcv_backfill.max_candles_per_pool,
                    )
                except CallBudgetExceeded:
                    exit_reason = "budget_exhausted"
                    break
                except GeckoTerminalApiError as exc:
                    logger.warning("Échec backfill OHLCV pour %s : %s", d.pool_address, exc)
                    continue

                candles = parse_ohlcv_candles(ohlcv_raw)
                cache_path = str(client.call_records[-1].cached_at) if client.call_records else None
                backfill_rows = []
                for ts, o, h, l, c, v in candles:
                    candle_time = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    row = PoolSnapshot(
                        request_timestamp_utc=candle_time,
                        pool_address=d.pool_address,
                        network=config.api.network,
                        dex=d.dex,
                        base_token_address=None,
                        base_token_symbol=None,
                        quote_token_address=None,
                        quote_token_symbol=None,
                        pool_created_at=d.pool_created_at,
                        price_usd=c,
                        fdv_usd=None,
                        market_cap_usd=None,
                        reserve_usd=None,
                        volume_usd_m5=None,
                        volume_usd_h1=None,
                        volume_usd_h24=None,
                        buys_m5=None,
                        sells_m5=None,
                        buyers_m5=None,
                        sellers_m5=None,
                        buys_h1=None,
                        sells_h1=None,
                        buyers_h1=None,
                        sellers_h1=None,
                        buys_h24=None,
                        sells_h24=None,
                        buyers_h24=None,
                        sellers_h24=None,
                        source="ohlcv_backfill",
                        ohlcv_timeframe=config.ohlcv_backfill.timeframe,
                        ohlcv_open=o,
                        ohlcv_high=h,
                        ohlcv_low=l,
                        ohlcv_close=c,
                        ohlcv_volume=v,
                        raw_cache_path=cache_path,
                    ).as_row()
                    backfill_rows.append(row)
                if backfill_rows:
                    snapshot_writer.append_rows(backfill_rows, dt=started_at)
                    pools_backfilled += 1

        if status_events:
            status_writer.append_rows([e.as_row() for e in status_events], dt=started_at)
        pools_status_events = len(status_events)

    except Exception as exc:  # noqa: BLE001 - on journalise puis on relance
        logger.exception("Run %s en échec", run_id)
        exit_reason = "error"
        client.errors.append(f"exception non gérée : {exc}")

    finished_at = datetime.now(timezone.utc)
    run_log = RunLog(
        run_id=run_id,
        started_at_utc=started_at.isoformat(),
        finished_at_utc=finished_at.isoformat(),
        network=config.api.network,
        calls_made=client.calls_made,
        calls_budget=config.api.max_calls_per_run,
        errors_count=len(client.errors),
        errors_json=json.dumps(client.errors, ensure_ascii=False),
        pools_discovered_trending=pools_discovered_trending,
        pools_discovered_random=pools_discovered_random,
        pools_snapshotted=pools_snapshotted,
        pools_backfilled=pools_backfilled,
        pools_status_events=pools_status_events,
        exit_reason=exit_reason,
    )
    run_log_writer = AppendOnlyCsvWriter(config.runs_log_dir, RunLog.fieldnames())
    run_log_writer.append_rows([run_log.as_row()], dt=started_at)
    logger.info("Run %s terminé : %s", run_id, run_log.as_row())
    return run_log
