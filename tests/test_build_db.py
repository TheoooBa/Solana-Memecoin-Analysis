import sqlite3
from datetime import datetime, timedelta, timezone

from smc_collector.build_db import build_database
from smc_collector.config import load_config
from smc_collector.models import PoolSnapshot, SCHEMA_VERSION
from smc_collector.storage import AppendOnlyCsvWriter

CONFIG_PATH = "config/collection.yaml"


def _make_v2_snapshot(**overrides) -> dict:
    defaults = dict(
        request_timestamp_utc="2026-09-21T12:00:00+00:00",
        pool_address="p_v2", network="solana", dex="pumpswap",
        base_token_address="TOKEN", base_token_symbol="SYM",
        quote_token_address="SOL", quote_token_symbol="SOL",
        pool_created_at="2026-09-20T00:00:00Z",
        price_usd=0.001, fdv_usd=1000.0, market_cap_usd=1000.0, reserve_usd=500.0,
        quote_token_price_usd=186.0, locked_liquidity_pct=100.0, pool_fee_pct=0.25,
        source="snapshot", ohlcv_timeframe=None, ohlcv_open=None, ohlcv_high=None,
        ohlcv_low=None, ohlcv_close=None, ohlcv_volume=None, raw_cache_path=None,
        schema_version=SCHEMA_VERSION,
    )
    for period in ("m5", "m15", "m30", "h1", "h6", "h24"):
        defaults[f"volume_usd_{period}"] = 1.0
        defaults[f"price_change_pct_{period}"] = 0.1
        for field in ("buys", "sells", "buyers", "sellers"):
            defaults[f"{field}_{period}"] = 1
    defaults.update(overrides)
    return PoolSnapshot(**defaults).as_row()


def test_build_db_loads_mixed_v1_and_v2_snapshot_files(tmp_path):
    """Un fichier journalier v1 (sans les colonnes ajoutées aujourd'hui) et un
    fichier v2 (avec) doivent tous les deux se charger sans erreur dans la
    même table, comme l'exige la reprise après un changement de schéma."""
    config = load_config(CONFIG_PATH, base_dir=tmp_path)

    # Fichier v1 : en-tête restreint aux colonnes d'origine (simule un fichier
    # déjà collecté avant ce changement, jamais réécrit).
    v1_fieldnames = [
        "request_timestamp_utc", "pool_address", "network", "dex",
        "base_token_address", "base_token_symbol", "quote_token_address", "quote_token_symbol",
        "pool_created_at", "price_usd", "fdv_usd", "market_cap_usd", "reserve_usd",
        "volume_usd_m5", "volume_usd_h1", "volume_usd_h24",
        "buys_m5", "sells_m5", "buyers_m5", "sellers_m5",
        "buys_h1", "sells_h1", "buyers_h1", "sellers_h1",
        "buys_h24", "sells_h24", "buyers_h24", "sellers_h24",
        "source", "ohlcv_timeframe", "ohlcv_open", "ohlcv_high", "ohlcv_low",
        "ohlcv_close", "ohlcv_volume", "raw_cache_path",
    ]
    v1_row = {
        "request_timestamp_utc": "2026-09-20T12:00:00+00:00",
        "pool_address": "p_v1", "network": "solana", "dex": "pumpswap",
        "base_token_address": "TOKEN_OLD", "base_token_symbol": "OLD",
        "quote_token_address": "SOL", "quote_token_symbol": "SOL",
        "pool_created_at": "2026-09-19T00:00:00Z",
        "price_usd": "0.002", "fdv_usd": "2000", "market_cap_usd": "2000", "reserve_usd": "300",
        "volume_usd_m5": "1", "volume_usd_h1": "2", "volume_usd_h24": "3",
        "buys_m5": "1", "sells_m5": "1", "buyers_m5": "1", "sellers_m5": "1",
        "buys_h1": "1", "sells_h1": "1", "buyers_h1": "1", "sellers_h1": "1",
        "buys_h24": "1", "sells_h24": "1", "buyers_h24": "1", "sellers_h24": "1",
        "source": "snapshot", "ohlcv_timeframe": "", "ohlcv_open": "", "ohlcv_high": "",
        "ohlcv_low": "", "ohlcv_close": "", "ohlcv_volume": "", "raw_cache_path": "",
    }
    AppendOnlyCsvWriter(config.snapshots_dir, v1_fieldnames).append_rows(
        [v1_row], dt=datetime(2026, 9, 20, tzinfo=timezone.utc)
    )

    # Fichier v2 : en-tête complet (toutes les colonnes actuelles de PoolSnapshot).
    v2_row = _make_v2_snapshot()
    AppendOnlyCsvWriter(config.snapshots_dir, PoolSnapshot.fieldnames()).append_rows(
        [v2_row], dt=datetime(2026, 9, 21, tzinfo=timezone.utc)
    )

    db_path = tmp_path / "test.sqlite3"
    counts = build_database(config, db_path)
    assert counts["pool_snapshots"] == 2

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT pool_address, schema_version, locked_liquidity_pct, quote_token_price_usd '
            "FROM pool_snapshots ORDER BY pool_address"
        ).fetchall()
    finally:
        conn.close()

    by_address = {r[0]: r for r in rows}

    # v1 : schema_version et les nouvelles colonnes sont vides, pas une erreur.
    assert by_address["p_v1"][1] == ""  # schema_version absent du fichier v1
    assert by_address["p_v1"][2] == ""  # locked_liquidity_pct absent du fichier v1

    # v2 : toutes les nouvelles colonnes sont bien renseignées.
    assert by_address["p_v2"][1] == SCHEMA_VERSION
    assert by_address["p_v2"][2] == 100.0
    assert by_address["p_v2"][3] == 186.0


def test_build_db_loads_new_tables(tmp_path):
    from smc_collector.models import ListRankObservation, TokenInfoRecord, TokenInfoStatusEvent

    config = load_config(CONFIG_PATH, base_dir=tmp_path)
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)

    rank = ListRankObservation(
        request_timestamp_utc=now.isoformat(), pool_address="p1", network="solana",
        rank=1, schema_version=SCHEMA_VERSION,
    )
    AppendOnlyCsvWriter(config.trending_ranks_dir, ListRankObservation.fieldnames()).append_rows([rank.as_row()], dt=now)

    info = TokenInfoRecord(
        pool_address="p1", base_token_address="T1", network="solana",
        requested_at_utc=now.isoformat(), gt_score=80.0, holders_top10_pct=50.0,
        holders_11_20_pct=None, holders_21_40_pct=None, holders_rest_pct=None,
        mint_authority="no", freeze_authority="no", developer_holding_percentage=None,
        is_honeypot="unknown", raw_response_json=None, schema_version=SCHEMA_VERSION,
    )
    AppendOnlyCsvWriter(config.token_info_dir, TokenInfoRecord.fieldnames()).append_rows([info.as_row()], dt=now)

    event = TokenInfoStatusEvent(
        pool_address="p2", event_at_utc=now.isoformat(), event_type="fetch_failed",
        detail="404", schema_version=SCHEMA_VERSION,
    )
    AppendOnlyCsvWriter(config.token_info_status_dir, TokenInfoStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

    counts = build_database(config, tmp_path / "test.sqlite3")
    assert counts["trending_ranks"] == 1
    assert counts["new_pool_ranks"] == 0
    assert counts["token_info"] == 1
    assert counts["token_info_status"] == 1


def test_build_db_loads_rugcheck_tables(tmp_path):
    from smc_collector.models import RugcheckInfoRecord, RugcheckStatusEvent
    from smc_collector.rugcheck import parse_rugcheck_report
    from test_rugcheck import SAMPLE_RUGCHECK_RESPONSE

    config = load_config(CONFIG_PATH, base_dir=tmp_path)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)

    parsed = parse_rugcheck_report(SAMPLE_RUGCHECK_RESPONSE)
    info = RugcheckInfoRecord(
        pool_address="p1", token_address="T1", network="solana",
        requested_at_utc=now.isoformat(), schema_version=SCHEMA_VERSION, **parsed,
    )
    AppendOnlyCsvWriter(config.rugcheck_info_dir, RugcheckInfoRecord.fieldnames()).append_rows([info.as_row()], dt=now)

    event = RugcheckStatusEvent(
        pool_address="p2", event_at_utc=now.isoformat(), event_type="not_yet_indexed",
        detail="404", schema_version=SCHEMA_VERSION,
    )
    AppendOnlyCsvWriter(config.rugcheck_status_dir, RugcheckStatusEvent.fieldnames()).append_rows([event.as_row()], dt=now)

    db_path = tmp_path / "test.sqlite3"
    counts = build_database(config, db_path)
    assert counts["rugcheck_info"] == 1
    assert counts["rugcheck_status"] == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            'SELECT score, permanent_delegate_present, insider_holders_count FROM rugcheck_info'
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 14500.0
    assert row[1] == "False"
    assert row[2] == 0
