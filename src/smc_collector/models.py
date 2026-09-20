"""Structures de lignes append-only écrites sur disque.

Chaque dataclass correspond à un fichier CSV. `fieldnames()` fixe l'ordre des
colonnes une bonne fois pour toutes : ne jamais réordonner une classe existante
sans migration, sous peine de corrompre les fichiers déjà écrits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class PoolSnapshot:
    """Une ligne = un pool observé à un instant donné (source="snapshot")
    ou une bougie OHLCV reconstituée avant la première observation
    (source="ohlcv_backfill"). Les deux partagent le même fichier car elles
    décrivent la même chronologie de pool ; seules les colonnes pertinentes
    à chaque source sont renseignées.
    """

    # Pour source="snapshot" : horodatage réel de NOTRE requête (jamais une heure
    # fournie par la source, comme l'heure d'entrée dans une liste "tendance").
    # Pour source="ohlcv_backfill" : horodatage de LA BOUGIE elle-même (nécessaire
    # pour reconstituer une chronologie ; notre heure de requête n'aurait aucun
    # sens ici, toutes les bougies d'un même appel la partageraient).
    request_timestamp_utc: str
    pool_address: str
    network: str
    dex: str | None
    base_token_address: str | None
    base_token_symbol: str | None
    quote_token_address: str | None
    quote_token_symbol: str | None
    pool_created_at: str | None
    price_usd: float | None
    fdv_usd: float | None
    market_cap_usd: float | None
    reserve_usd: float | None
    volume_usd_m5: float | None
    volume_usd_h1: float | None
    volume_usd_h24: float | None
    buys_m5: int | None
    sells_m5: int | None
    buyers_m5: int | None
    sellers_m5: int | None
    buys_h1: int | None
    sells_h1: int | None
    buyers_h1: int | None
    sellers_h1: int | None
    buys_h24: int | None
    sells_h24: int | None
    buyers_h24: int | None
    sellers_h24: int | None
    source: str  # "snapshot" | "ohlcv_backfill"
    ohlcv_timeframe: str | None  # renseigné seulement si source="ohlcv_backfill"
    ohlcv_open: float | None
    ohlcv_high: float | None
    ohlcv_low: float | None
    ohlcv_close: float | None
    ohlcv_volume: float | None
    raw_cache_path: str | None  # traçabilité vers la réponse brute archivée

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(PoolSnapshot)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class PoolDiscovery:
    """Une ligne = la première fois où NOUS décidons de suivre un pool.
    Jamais réécrite ni supprimée : c'est la source de vérité de l'univers suivi.
    """

    pool_address: str
    network: str
    dex: str | None
    group: str  # "trending" | "random_new"
    discovered_at_utc: str  # notre horloge, jamais l'heure d'apparition dans une liste externe
    pool_created_at: str | None
    reason: str
    sampling_probability: float  # 1.0 pour le groupe "trending" (pas de tirage)
    tracking_until_utc: str

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(PoolDiscovery)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class PoolStatusEvent:
    """Une ligne = un événement de cycle de vie de suivi. Un pool mort n'est
    jamais supprimé : on ajoute un événement qui le documente.
    """

    pool_address: str
    event_at_utc: str
    event_type: str  # "tracking_window_ended" | "missing_from_multi_pool_response" | ...
    detail: str

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(PoolStatusEvent)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class RunLog:
    """Une ligne = une exécution de `collect`. Permet de repérer les trous."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    network: str
    calls_made: int
    calls_budget: int
    errors_count: int
    errors_json: str
    pools_discovered_trending: int
    pools_discovered_random: int
    pools_snapshotted: int
    pools_backfilled: int
    pools_status_events: int
    exit_reason: str  # "completed" | "budget_exhausted" | "error"

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(RunLog)]

    def as_row(self) -> dict:
        return asdict(self)
