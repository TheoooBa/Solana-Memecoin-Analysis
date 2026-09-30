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
from datetime import datetime, timedelta, timezone

from .config import CollectionConfig
from .http_client import CallBudgetExceeded, GeckoTerminalApiError, GeckoTerminalClient
from .models import (
    SCHEMA_VERSION,
    ListRankObservation,
    PoolDiscovery,
    PoolSnapshot,
    PoolStatusEvent,
    RugcheckInfoRecord,
    RugcheckStatusEvent,
    RunLog,
    TokenInfoRecord,
    TokenInfoStatusEvent,
)
from .parsing import PERIODS, ParsedPool, parse_ohlcv_candles, parse_pool_list
from .registry import load_universe, select_new_discoveries
from .rugcheck import run_rugcheck_phase, select_pools_pending_rugcheck
from .rugcheck_client import RugCheckClient
from .storage import AppendOnlyCsvWriter, utc_now_iso
from .token_info import run_token_info_phase, select_pools_pending_info

logger = logging.getLogger(__name__)


def _snapshot_from_parsed(
    pool: ParsedPool,
    network: str,
    request_timestamp: str,
    source: str = "snapshot",
    raw_cache_path: str | None = None,
) -> PoolSnapshot:
    period_fields = {}
    for period in PERIODS:
        period_fields[f"volume_usd_{period}"] = getattr(pool, f"volume_usd_{period}")
        period_fields[f"price_change_pct_{period}"] = getattr(pool, f"price_change_pct_{period}")
        period_fields[f"buys_{period}"] = getattr(pool, f"buys_{period}")
        period_fields[f"sells_{period}"] = getattr(pool, f"sells_{period}")
        period_fields[f"buyers_{period}"] = getattr(pool, f"buyers_{period}")
        period_fields[f"sellers_{period}"] = getattr(pool, f"sellers_{period}")

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
        quote_token_price_usd=pool.quote_token_price_usd,
        locked_liquidity_pct=pool.locked_liquidity_pct,
        pool_fee_pct=pool.pool_fee_pct,
        source=source,
        ohlcv_timeframe=None,
        ohlcv_open=None,
        ohlcv_high=None,
        ohlcv_low=None,
        ohlcv_close=None,
        ohlcv_volume=None,
        raw_cache_path=raw_cache_path,
        schema_version=SCHEMA_VERSION,
        **period_fields,
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
    trending_ranks_writer = AppendOnlyCsvWriter(config.trending_ranks_dir, ListRankObservation.fieldnames())
    new_pool_ranks_writer = AppendOnlyCsvWriter(config.new_pool_ranks_dir, ListRankObservation.fieldnames())
    token_info_writer = AppendOnlyCsvWriter(config.token_info_dir, TokenInfoRecord.fieldnames())
    token_info_status_writer = AppendOnlyCsvWriter(config.token_info_status_dir, TokenInfoStatusEvent.fieldnames())
    rugcheck_info_writer = AppendOnlyCsvWriter(config.rugcheck_info_dir, RugcheckInfoRecord.fieldnames())
    rugcheck_status_writer = AppendOnlyCsvWriter(config.rugcheck_status_dir, RugcheckStatusEvent.fieldnames())

    exit_reason = "completed"
    pools_discovered_trending = 0
    pools_discovered_random = 0
    pools_snapshotted = 0
    pools_backfilled = 0
    pools_status_events = 0

    try:
        universe = load_universe(config.registry_dir, config.status_dir)
        already_tracked = set(universe.keys())

        list_request_timestamp = utc_now_iso()
        trending_raw = client.get_trending_pools(page=1)
        trending_pools = parse_pool_list(trending_raw)

        new_pools_all: list[ParsedPool] = []
        for page in range(1, config.univers.new_pools_page_count + 1):
            new_pools_raw = client.get_new_pools(page=page)
            new_pools_all.extend(parse_pool_list(new_pools_raw))

        # Rang de chaque pool dans ces deux listes, à CET instant précis : zéro
        # appel supplémentaire (déjà récupérées ci-dessus), mais irrécupérable
        # plus tard si on ne le fait pas maintenant (boîte noire sans historique).
        if trending_pools:
            trending_ranks_writer.append_rows(
                [
                    ListRankObservation(
                        request_timestamp_utc=list_request_timestamp,
                        pool_address=p.pool_address,
                        network=config.api.network,
                        rank=rank,
                        schema_version=SCHEMA_VERSION,
                    ).as_row()
                    for rank, p in enumerate(trending_pools, start=1)
                ],
                dt=started_at,
            )
        if new_pools_all:
            new_pool_ranks_writer.append_rows(
                [
                    ListRankObservation(
                        request_timestamp_utc=list_request_timestamp,
                        pool_address=p.pool_address,
                        network=config.api.network,
                        rank=rank,
                        schema_version=SCHEMA_VERSION,
                    ).as_row()
                    for rank, p in enumerate(new_pools_all, start=1)
                ],
                dt=started_at,
            )

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
                schema_version=SCHEMA_VERSION,
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
                            schema_version=SCHEMA_VERSION,
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
                # Aucun de ces champs n'existe dans une réponse OHLCV (bougies
                # pures) : None est le comportement correct, pas une perte.
                empty_period_fields = {
                    f"{prefix}_{period}": None
                    for prefix in ("volume_usd", "price_change_pct", "buys", "sells", "buyers", "sellers")
                    for period in PERIODS
                }
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
                        quote_token_price_usd=None,
                        locked_liquidity_pct=None,
                        pool_fee_pct=None,
                        source="ohlcv_backfill",
                        ohlcv_timeframe=config.ohlcv_backfill.timeframe,
                        ohlcv_open=o,
                        ohlcv_high=h,
                        ohlcv_low=l,
                        ohlcv_close=c,
                        ohlcv_volume=v,
                        raw_cache_path=cache_path,
                        schema_version=SCHEMA_VERSION,
                        **empty_period_fields,
                    ).as_row()
                    backfill_rows.append(row)
                if backfill_rows:
                    snapshot_writer.append_rows(backfill_rows, dt=started_at)
                    pools_backfilled += 1

        # -- Info token (score, détenteurs, sécurité), best-effort --------
        # Isolée dans son propre try/except : un problème ici (y compris la
        # fermeture surprise de cet endpoint, gratuit en pratique mais annoncé
        # payant ailleurs) ne doit jamais faire échouer la collecte de base
        # (prix/liquidité/volume) déjà écrite ci-dessus.
        #
        # Budget de temps mesuré depuis le début DE CETTE PHASE (pas depuis le
        # début du run) : mesuré empiriquement le 2026-09-30 sur l'univers
        # suivi actuel, les phases précédentes (instantanés + backfill OHLCV)
        # consomment à elles seules 300-340s à débit réel, ce qui aurait
        # laissé une marge quasi nulle à cette phase si son budget était
        # compté depuis le début du run.
        if exit_reason != "budget_exhausted":
            token_info_phase_started_at = datetime.now(timezone.utc)
            try:
                pending_info = select_pools_pending_info(
                    config.registry_dir,
                    config.token_info_dir,
                    config.token_info_status_dir,
                    max_count=config.token_info.max_calls_per_run,
                )
                phase_result = run_token_info_phase(
                    client=client,
                    pending=pending_info,
                    network=config.api.network,
                    run_started_at=token_info_phase_started_at,
                    max_total_elapsed=timedelta(seconds=config.token_info.max_total_run_seconds),
                    max_failures_before_unavailable=config.token_info.max_failures_before_unavailable,
                    circuit_breaker_consecutive_failures=config.token_info.max_failures_before_unavailable,
                    max_raw_json_bytes=config.token_info.max_raw_json_bytes,
                )
                if phase_result.info_rows:
                    token_info_writer.append_rows(phase_result.info_rows, dt=started_at)
                if phase_result.status_rows:
                    token_info_status_writer.append_rows(phase_result.status_rows, dt=started_at)
                if phase_result.stopped_reason == "call_budget":
                    exit_reason = "budget_exhausted"
                if phase_result.stopped_reason:
                    logger.info(
                        "Phase info-token arrêtée (%s) après %d appel(s)",
                        phase_result.stopped_reason, phase_result.calls_attempted,
                    )
            except Exception as exc:  # noqa: BLE001 - jamais laisser cette phase faire échouer le run
                logger.exception("Phase info-token en échec, collecte de base non affectée")
                client.errors.append(f"phase info-token : {exc}")

        # -- RugCheck (détection anti-honeypot réelle), best-effort --------
        # Hôte, client et budget d'appels totalement indépendants de
        # GeckoTerminal ci-dessus : cette phase tourne même si le budget
        # d'appels GeckoTerminal est déjà épuisé pour ce run. Isolée dans son
        # propre try/except pour la même raison que la phase info-token.
        #
        # Contrairement à la phase info-token (dont le budget de temps est
        # mesuré depuis le tout début du run), celui de RugCheck est mesuré
        # depuis le début DE CETTE PHASE : mesuré empiriquement le 2026-09-30,
        # les phases GeckoTerminal qui précèdent (instantanés + backfill
        # OHLCV) consomment à elles seules 300-340s à débit réel sur
        # l'univers suivi actuel, ce qui aurait laissé 0s de marge réelle à
        # RugCheck si son budget était compté depuis le début du run comme
        # pour info-token — la fonctionnalité ne se serait quasiment jamais
        # déclenchée en production. Reprise garantie au run suivant (FIFO sur
        # fichiers) si cette phase est elle-même coupée par son propre budget.
        rugcheck_phase_started_at = datetime.now(timezone.utc)
        try:
            pending_rugcheck = select_pools_pending_rugcheck(
                config.registry_dir,
                config.rugcheck_info_dir,
                config.rugcheck_status_dir,
                max_count=config.rugcheck.max_calls_per_run,
            )
            rugcheck_client = RugCheckClient(
                base_url=config.rugcheck.base_url,
                calls_per_minute=config.rugcheck.calls_per_minute,
                max_calls_per_run=config.rugcheck.max_calls_per_run,
                request_timeout_seconds=config.rugcheck.request_timeout_seconds,
                max_retries=config.rugcheck.max_retries,
                backoff_base_seconds=config.rugcheck.backoff_base_seconds,
                backoff_max_seconds=config.rugcheck.backoff_max_seconds,
            )
            rugcheck_result = run_rugcheck_phase(
                client=rugcheck_client,
                pending=pending_rugcheck,
                network=config.api.network,
                run_started_at=rugcheck_phase_started_at,
                max_total_elapsed=timedelta(seconds=config.rugcheck.max_total_run_seconds),
                max_failures_before_unavailable=config.rugcheck.max_failures_before_unavailable,
                circuit_breaker_consecutive_failures=config.rugcheck.max_failures_before_unavailable,
            )
            if rugcheck_result.info_rows:
                rugcheck_info_writer.append_rows(rugcheck_result.info_rows, dt=started_at)
            if rugcheck_result.status_rows:
                rugcheck_status_writer.append_rows(rugcheck_result.status_rows, dt=started_at)
            if rugcheck_result.stopped_reason:
                logger.info(
                    "Phase rugcheck arrêtée (%s) après %d appel(s)",
                    rugcheck_result.stopped_reason, rugcheck_result.calls_attempted,
                )
            if rugcheck_client.errors:
                client.errors.extend(f"rugcheck: {e}" for e in rugcheck_client.errors)
        except Exception as exc:  # noqa: BLE001 - jamais laisser cette phase faire échouer le run
            logger.exception("Phase rugcheck en échec, collecte de base non affectée")
            client.errors.append(f"phase rugcheck : {exc}")

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
        schema_version=SCHEMA_VERSION,
    )
    run_log_writer = AppendOnlyCsvWriter(config.runs_log_dir, RunLog.fieldnames())
    run_log_writer.append_rows([run_log.as_row()], dt=started_at)
    logger.info("Run %s terminé : %s", run_id, run_log.as_row())
    return run_log
