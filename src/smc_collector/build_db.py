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
from .models import PoolDiscovery, PoolSnapshot, PoolStatusEvent, RunLog
from .storage import read_all_rows

logger = logging.getLogger(__name__)

_SQLITE_TYPE_OVERRIDES = {
    # Colonnes numériques explicites ; tout le reste est TEXT (CSV n'a pas de types).
    "price_usd": "REAL", "fdv_usd": "REAL", "market_cap_usd": "REAL", "reserve_usd": "REAL",
    "volume_usd_m5": "REAL", "volume_usd_h1": "REAL", "volume_usd_h24": "REAL",
    "buys_m5": "INTEGER", "sells_m5": "INTEGER", "buyers_m5": "INTEGER", "sellers_m5": "INTEGER",
    "buys_h1": "INTEGER", "sells_h1": "INTEGER", "buyers_h1": "INTEGER", "sellers_h1": "INTEGER",
    "buys_h24": "INTEGER", "sells_h24": "INTEGER", "buyers_h24": "INTEGER", "sellers_h24": "INTEGER",
    "ohlcv_open": "REAL", "ohlcv_high": "REAL", "ohlcv_low": "REAL", "ohlcv_close": "REAL",
    "ohlcv_volume": "REAL",
    "sampling_probability": "REAL",
    "calls_made": "INTEGER", "calls_budget": "INTEGER", "errors_count": "INTEGER",
    "pools_discovered_trending": "INTEGER", "pools_discovered_random": "INTEGER",
    "pools_snapshotted": "INTEGER", "pools_backfilled": "INTEGER", "pools_status_events": "INTEGER",
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
        }
        conn.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_pool ON pool_snapshots("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_registry_pool ON pool_registry("pool_address")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status_pool ON pool_status_events("pool_address")')
        conn.commit()
    finally:
        conn.close()
    logger.info("Base construite dans %s : %s", db_path, counts)
    return counts
