"""Commande `build-db` : charge les fichiers CSV append-only dans SQLite.

La base SQLite est un artefact dérivé, jamais une source de vérité : elle est
entièrement reconstruite à chaque exécution (DROP puis CREATE) à partir des
fichiers CSV, qui restent la seule source de vérité versionnée.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .config import CollectionConfig
from .models import (
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
from .parsing import PERIODS
from .storage import read_all_rows

logger = logging.getLogger(__name__)

_SQLITE_TYPE_OVERRIDES = {
    # Colonnes numériques explicites ; tout le reste est TEXT (CSV n'a pas de types).
    "price_usd": "REAL", "fdv_usd": "REAL", "market_cap_usd": "REAL", "reserve_usd": "REAL",
    "quote_token_price_usd": "REAL", "locked_liquidity_pct": "REAL", "pool_fee_pct": "REAL",
    "ohlcv_open": "REAL", "ohlcv_high": "REAL", "ohlcv_low": "REAL", "ohlcv_close": "REAL",
    "ohlcv_volume": "REAL",
    "sampling_probability": "REAL",
    "calls_made": "INTEGER", "calls_budget": "INTEGER", "errors_count": "INTEGER",
    "pools_discovered_trending": "INTEGER", "pools_discovered_random": "INTEGER",
    "pools_snapshotted": "INTEGER", "pools_backfilled": "INTEGER", "pools_status_events": "INTEGER",
    "schema_version": "INTEGER", "rank": "INTEGER",
    "gt_score": "REAL", "holders_top10_pct": "REAL", "holders_11_20_pct": "REAL",
    "holders_21_40_pct": "REAL", "holders_rest_pct": "REAL", "developer_holding_percentage": "REAL",
    "score": "REAL", "score_normalised": "REAL", "risks_count": "INTEGER",
    "transfer_fee_pct": "REAL", "lp_locked_pct": "REAL", "graph_insiders_detected": "INTEGER",
    "top_holder_pct": "REAL", "insider_holders_count": "INTEGER", "insider_holders_pct_sum": "REAL",
    "creator_balance": "REAL",
    **{
        f"{prefix}_{period}": "REAL" if prefix in ("volume_usd", "price_change_pct") else "INTEGER"
        for prefix in ("volume_usd", "price_change_pct", "buys", "sells", "buyers", "sellers")
        for period in PERIODS
    },
}


def _create_table(conn: sqlite3.Connection, table: str, fieldnames: list[str]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    columns_sql = ", ".join(f'"{name}" {_SQLITE_TYPE_OVERRIDES.get(name, "TEXT")}' for name in fieldnames)
    conn.execute(f"CREATE TABLE {table} ({columns_sql})")


def _load_table(conn: sqlite3.Connection, table: str, directory: Path, fieldnames: list[str]) -> int:
    _create_table(conn, table, fieldnames)
    placeholders = ", ".join("?" for _ in fieldnames)
    columns_sql = ", ".join(f'"{name}"' for name in fieldnames)
    insert_sql = f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})"

    count = 0
    rows_buffer = []
    for row in read_all_rows(directory):
        rows_buffer.append(tuple(row.get(name, "") for name in fieldnames))
        count += 1
        if len(rows_buffer) >= 1000:
            conn.executemany(insert_sql, rows_buffer)
            rows_buffer.clear()
    if rows_buffer:
        conn.executemany(insert_sql, rows_buffer)
    return count


def build_database(config: CollectionConfig, db_path: Path) -> dict[str, int]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        counts = {
            "pool_snapshots": _load_table(conn, "pool_snapshots", config.snapshots_dir, PoolSnapshot.fieldnames()),
            "pool_registry": _load_table(conn, "pool_registry", config.registry_dir, PoolDiscovery.fieldnames()),
            "pool_status_events": _load_table(conn, "pool_status_events", config.status_dir, PoolStatusEvent.fieldnames()),
            "collector_runs": _load_table(conn, "collector_runs", config.runs_log_dir, RunLog.fieldnames()),
            "trending_ranks": _load_table(conn, "trending_ranks", config.trending_ranks_dir, ListRankObservation.fieldnames()),
            "new_pool_ranks": _load_table(conn, "new_pool_ranks", config.new_pool_ranks_dir, ListRankObservation.fieldnames()),
            "token_info": _load_table(conn, "token_info", config.token_info_dir, TokenInfoRecord.fieldnames()),
            "token_info_status": _load_table(conn, "token_info_status", config.token_info_status_dir, TokenInfoStatusEvent.fieldnames()),
            "rugcheck_info": _load_table(conn, "rugcheck_info", config.rugcheck_info_dir, RugcheckInfoRecord.fieldnames()),
            "rugcheck_status": _load_table(conn, "rugcheck_status", config.rugcheck_status_dir, RugcheckStatusEvent.fieldnames()),
        }
        conn.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_pool ON pool_snapshots("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_registry_pool ON pool_registry("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status_pool ON pool_status_events("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_trending_ranks_ts ON trending_ranks("request_timestamp_utc")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_new_pool_ranks_ts ON new_pool_ranks("request_timestamp_utc")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_info_pool ON token_info("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_rugcheck_info_pool ON rugcheck_info("pool_address")')
        conn.commit()
    finally:
        conn.close()
    logger.info("Base construite dans %s : %s", db_path, counts)
    return counts
