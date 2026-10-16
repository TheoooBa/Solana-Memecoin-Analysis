"""Construction du jeu de données d'évaluation à partir des CSV déjà
collectés. Lecture seule : ce module n'écrit jamais dans data/raw.

Un pool témoin (`random_new`) découvert après le gel, avec au moins un
instantané de prix disponible, donne une ligne évaluable : les champs de
règle (liquidité verrouillée, RugCheck...) viennent de son premier
instantané "snapshot" et de rugcheck_info/token_info ; le prix d'entrée et
les prix aux horizons viennent de la série de prix complète (tout `source`
confondu, pour profiter du backfill OHLCV quand il existe).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from smc_collector.storage import read_all_rows

# Tolérance autour d'un horizon cible : une recherche de prix à
# "découverte + 1h" accepte un instantané jusqu'à 25% de l'horizon avant ou
# après la cible plutôt que d'exiger une correspondance exacte, qui
# n'existerait presque jamais vu l'intervalle entre deux collectes.
_HORIZON_TOLERANCE_RATIO = 0.25


@dataclass
class PoolEvalRecord:
    pool_address: str
    discovered_at_utc: datetime
    rule_fields: dict[str, Any]  # champs dispo à la découverte (snapshot + rugcheck + token_info)
    entry_price: float | None
    price_series: list[tuple[datetime, float]]  # trié par horodatage


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nearest_price(series: list[tuple[datetime, float]], target: datetime, tolerance: timedelta) -> float | None:
    """Prix de l'instantané le plus proche de `target`, dans la tolérance.
    `series` doit déjà être triée par horodatage."""
    best_price: float | None = None
    best_delta: timedelta | None = None
    for ts, price in series:
        delta = abs(ts - target)
        if delta > tolerance:
            continue
        if best_delta is None or delta < best_delta:
            best_delta, best_price = delta, price
    return best_price


def load_witness_pool_records(
    registry_dir: Path, snapshots_dir: Path, rugcheck_info_dir: Path, token_info_dir: Path,
    frozen_at_utc: datetime,
) -> list[PoolEvalRecord]:
    """Pools témoins (`random_new`) découverts après le gel, avec leurs
    champs de règle et leur série de prix. Un pool sans aucun prix
    disponible est exclu d'emblée (inutilisable pour tout horizon)."""
    discoveries: dict[str, datetime] = {}
    for row in read_all_rows(registry_dir):
        if row.get("group") != "random_new":
            continue
        discovered_at = _parse_dt(row["discovered_at_utc"])
        if discovered_at < frozen_at_utc:
            continue
        pool_address = row["pool_address"]
        # FIFO naturel : ne garder que la première découverte si dupliquée
        # (ne devrait pas arriver, mais jamais écraser silencieusement).
        if pool_address not in discoveries:
            discoveries[pool_address] = discovered_at

    if not discoveries:
        return []

    first_snapshot_fields: dict[str, dict[str, Any]] = {}
    price_series: dict[str, list[tuple[datetime, float]]] = {addr: [] for addr in discoveries}
    for row in read_all_rows(snapshots_dir):
        pool_address = row.get("pool_address")
        if pool_address not in discoveries:
            continue
        price = _to_float_or_none(row.get("price_usd"))
        if price is not None:
            price_series[pool_address].append((_parse_dt(row["request_timestamp_utc"]), price))
        if row.get("source") == "snapshot" and pool_address not in first_snapshot_fields:
            first_snapshot_fields[pool_address] = row

    rugcheck_fields: dict[str, dict[str, Any]] = {}
    for row in read_all_rows(rugcheck_info_dir):
        pool_address = row.get("pool_address")
        if pool_address in discoveries and pool_address not in rugcheck_fields:
            rugcheck_fields[pool_address] = row

    token_info_fields: dict[str, dict[str, Any]] = {}
    for row in read_all_rows(token_info_dir):
        pool_address = row.get("pool_address")
        if pool_address in discoveries and pool_address not in token_info_fields:
            token_info_fields[pool_address] = row

    records: list[PoolEvalRecord] = []
    for pool_address, discovered_at in discoveries.items():
        series = sorted(price_series.get(pool_address, []))
        if not series:
            continue

        rule_fields: dict[str, Any] = {}
        rule_fields.update(first_snapshot_fields.get(pool_address, {}))
        rule_fields.update(rugcheck_fields.get(pool_address, {}))
        rule_fields.update(token_info_fields.get(pool_address, {}))

        entry_tolerance = timedelta(hours=1)
        entry_price = _nearest_price(series, discovered_at, entry_tolerance)

        records.append(
            PoolEvalRecord(
                pool_address=pool_address,
                discovered_at_utc=discovered_at,
                rule_fields=rule_fields,
                entry_price=entry_price,
                price_series=series,
            )
        )
    return records


def price_at_horizon(record: PoolEvalRecord, horizon_hours: int) -> float | None:
    target = record.discovered_at_utc + timedelta(hours=horizon_hours)
    tolerance = timedelta(hours=horizon_hours * _HORIZON_TOLERANCE_RATIO)
    return _nearest_price(record.price_series, target, tolerance)


def net_return(entry_price: float, horizon_price: float, cost_round_trip_pct: float) -> float:
    return (horizon_price / entry_price - 1) - cost_round_trip_pct / 100
