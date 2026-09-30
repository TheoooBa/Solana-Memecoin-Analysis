"""Structures de lignes append-only écrites sur disque.

Chaque dataclass correspond à un fichier CSV. `fieldnames()` fixe l'ordre des
colonnes une bonne fois pour toutes : ne jamais réordonner une classe existante
sans migration, sous peine de corrompre les fichiers déjà écrits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

# Journal des versions de schéma. Une ligne écrite AVANT l'introduction de ce
# champ n'a simplement pas la colonne `schema_version` dans son fichier CSV
# (jamais réécrite pour l'ajouter) ; `build_db.py` la charge quand même, avec
# une valeur vide pour cette colonne — c'est le signal qu'il s'agit de
# données antérieures à la version indiquée.
#
# v1 (2026-09-20/21) : version initiale (collecte, découverte, statut, run log).
# v2 (2026-09-21, jamais déployé avant le 2026-09-30) : ajout sur PoolSnapshot
#   de locked_liquidity_pct, pool_fee_pct, quote_token_price_usd, des périodes
#   m15/m30/h6 pour transactions et volume, et de price_change_pct (6
#   périodes) ; ajout de base_token_address sur PoolDiscovery ; nouvelles
#   tables ListRankObservation (rang tendance/nouveaux pools à chaque
#   exécution), TokenInfoRecord et TokenInfoStatusEvent (score de confiance,
#   concentration des détenteurs, autorités mint/freeze, part développeur,
#   honeypot brut). Complété le 2026-09-30, avant le premier déploiement, par
#   RugcheckInfoRecord et RugcheckStatusEvent (détection anti-honeypot réelle
#   via RugCheck.xyz : extensions Token-2022 dangereuses, taxe de transfert,
#   verrouillage LP, réseaux d'insiders — voir
#   reports/rugcheck_audit_2026-09-29.md).
SCHEMA_VERSION = 2


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
    # v2 : prix du SOL (ou autre quote token) en USD à l'instant du snapshot —
    # nécessaire pour stratifier l'analyse par niveau du prix du SOL (étape 3).
    quote_token_price_usd: float | None
    # v2 : indicateurs de sécurité disponibles gratuitement sur l'endpoint
    # multi-pools déjà appelé chaque run (absents de trending_pools/new_pools,
    # donc None pour les lignes issues de ces deux-là).
    locked_liquidity_pct: float | None
    pool_fee_pct: float | None
    volume_usd_m5: float | None
    volume_usd_m15: float | None
    volume_usd_m30: float | None
    volume_usd_h1: float | None
    volume_usd_h6: float | None
    volume_usd_h24: float | None
    buys_m5: int | None
    sells_m5: int | None
    buyers_m5: int | None
    sellers_m5: int | None
    buys_m15: int | None
    sells_m15: int | None
    buyers_m15: int | None
    sellers_m15: int | None
    buys_m30: int | None
    sells_m30: int | None
    buyers_m30: int | None
    sellers_m30: int | None
    buys_h1: int | None
    sells_h1: int | None
    buyers_h1: int | None
    sellers_h1: int | None
    buys_h6: int | None
    sells_h6: int | None
    buyers_h6: int | None
    sellers_h6: int | None
    buys_h24: int | None
    sells_h24: int | None
    buyers_h24: int | None
    sellers_h24: int | None
    price_change_pct_m5: float | None
    price_change_pct_m15: float | None
    price_change_pct_m30: float | None
    price_change_pct_h1: float | None
    price_change_pct_h6: float | None
    price_change_pct_h24: float | None
    source: str  # "snapshot" | "ohlcv_backfill"
    ohlcv_timeframe: str | None  # renseigné seulement si source="ohlcv_backfill"
    ohlcv_open: float | None
    ohlcv_high: float | None
    ohlcv_low: float | None
    ohlcv_close: float | None
    ohlcv_volume: float | None
    raw_cache_path: str | None  # traçabilité vers la réponse brute archivée
    schema_version: int | None  # v2 : absent (vide) sur les lignes écrites avant ce champ

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
    # v2 : nécessaire pour appeler /tokens/{address}/info. Vide sur les pools
    # découverts avant l'ajout de cette colonne — ceux-là ne pourront jamais
    # recevoir d'info token rétroactivement (voir README/rapport d'audit).
    base_token_address: str | None
    schema_version: int | None

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
    schema_version: int | None

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
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(RunLog)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class ListRankObservation:
    """Une ligne = la position d'un pool dans la liste "tendance" ou "nouveaux
    pools" à l'instant d'une exécution. Contrairement à `PoolDiscovery` (qui ne
    capture le rang qu'une seule fois, à la découverte), cette table capture le
    rang à CHAQUE exécution où le pool apparaît dans la liste — cette
    information est une boîte noire sans historique côté source : non captée
    maintenant, elle est perdue pour toujours.
    """

    request_timestamp_utc: str
    pool_address: str
    network: str
    rank: int
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(ListRankObservation)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class TokenInfoRecord:
    """Une ligne = un appel réussi à /tokens/{address}/info pour le token de
    base d'un pool, une seule fois par pool (à sa découverte), jamais
    réinterrogé. `is_honeypot` est stocké tel quel (chaîne), jamais interprété
    : la source peut renvoyer "unknown", "true" ou "false" selon les tokens.
    """

    pool_address: str
    base_token_address: str
    network: str
    requested_at_utc: str
    gt_score: float | None
    holders_top10_pct: float | None
    holders_11_20_pct: float | None
    holders_21_40_pct: float | None
    holders_rest_pct: float | None
    mint_authority: str | None
    freeze_authority: str | None
    developer_holding_percentage: float | None
    is_honeypot: str | None
    raw_response_json: str | None  # seulement si < token_info.max_raw_json_bytes
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(TokenInfoRecord)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class TokenInfoStatusEvent:
    """Une ligne = un échec d'appel /tokens/{address}/info, ou le passage
    définitif d'un pool en "indisponible" après trop d'échecs. Permet de
    reprendre la file d'attente d'une exécution à l'autre sans jamais bloquer
    indéfiniment sur un pool problématique.
    """

    pool_address: str
    event_at_utc: str
    event_type: str  # "fetch_failed" | "unavailable"
    detail: str
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(TokenInfoStatusEvent)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class RugcheckInfoRecord:
    """Une ligne = un appel réussi à RugCheck.xyz pour le token de base d'un
    pool, une seule fois par pool (à sa découverte), jamais réinterrogé une
    fois réussi. Aucune réponse brute conservée (contrairement à
    `TokenInfoRecord`) : ~12 Ko par réponse, plus de 2x le seuil déjà retenu
    pour token_info, pour une valeur ajoutée jugée insuffisante par l'audit
    du 2026-09-29 (voir reports/rugcheck_audit_2026-09-29.md). `rugged` est un
    verdict déjà calculé par RugCheck, enregistré tel quel, jamais réinterprété.
    """

    pool_address: str
    token_address: str
    network: str
    requested_at_utc: str
    score: float | None
    score_normalised: float | None
    rugged: bool | None
    risks_count: int | None
    risks_json: str | None
    permanent_delegate_present: bool | None
    transfer_hook_present: bool | None
    pausable_present: bool | None
    mint_close_authority_present: bool | None
    non_transferable: bool | None
    default_account_state: str | None
    transfer_fee_pct: float | None
    lp_locked_pct: float | None
    locker_scan_status: str | None
    graph_insiders_detected: int | None
    top_holder_pct: float | None
    insider_holders_count: int | None
    insider_holders_pct_sum: float | None
    creator: str | None
    creator_balance: float | None
    token_program: str | None
    metadata_mutable: bool | None
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(RugcheckInfoRecord)]

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class RugcheckStatusEvent:
    """Une ligne = un échec d'appel RugCheck, un pool pas encore indexé par
    RugCheck (event_type="not_yet_indexed", jamais compté comme un échec :
    voir rugcheck.py), ou le passage définitif d'un pool en "indisponible"
    après trop d'échecs réels."""

    pool_address: str
    event_at_utc: str
    event_type: str  # "fetch_failed" | "not_yet_indexed" | "unavailable"
    detail: str
    schema_version: int | None

    @staticmethod
    def fieldnames() -> list[str]:
        return [f.name for f in fields(RugcheckStatusEvent)]

    def as_row(self) -> dict:
        return asdict(self)
