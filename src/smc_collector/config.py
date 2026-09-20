"""Chargement et validation de config/collection.yaml.

Aucune valeur par défaut « magique » ici : tout vient du fichier YAML, pour que
la configuration réellement utilisée soit toujours visible et versionnée.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    network: str
    api_key_env_var: str
    api_key_header: str
    calls_per_minute: float
    max_calls_per_run: int
    request_timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float

    @property
    def api_key(self) -> str | None:
        """Lit la clé depuis l'environnement. Jamais stockée dans la config elle-même."""
        return os.environ.get(self.api_key_env_var) or None


@dataclass(frozen=True)
class UniversConfig:
    trending_pool_count: int
    new_pools_sample_probability: float
    new_pools_page_count: int
    sampling_seed: int
    tracking_duration_hours: float
    min_coverage_ratio: float


@dataclass(frozen=True)
class OhlcvBackfillConfig:
    timeframe: str
    aggregate: int
    max_candles_per_pool: int


@dataclass(frozen=True)
class StorageConfig:
    raw_dir: Path
    cache_dir: Path
    log_dir: Path


@dataclass(frozen=True)
class CollectionConfig:
    api: ApiConfig
    univers: UniversConfig
    ohlcv_backfill: OhlcvBackfillConfig
    storage: StorageConfig
    source_path: Path

    @property
    def snapshots_dir(self) -> Path:
        return self.storage.raw_dir / "snapshots"

    @property
    def registry_dir(self) -> Path:
        return self.storage.raw_dir / "pool_registry"

    @property
    def status_dir(self) -> Path:
        return self.storage.raw_dir / "pool_status"

    @property
    def runs_log_dir(self) -> Path:
        return self.storage.log_dir / "runs"


def load_config(path: str | Path, base_dir: str | Path | None = None) -> CollectionConfig:
    """Charge et valide la configuration de collecte.

    `base_dir` sert de racine pour résoudre les chemins relatifs de stockage
    (par défaut : le répertoire courant du process).
    """
    path = Path(path)
    base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Fichier de config invalide (pas un mapping) : {path}")

    try:
        api_raw = raw["api"]
        univers_raw = raw["univers"]
        ohlcv_raw = raw["ohlcv_backfill"]
        storage_raw = raw["storage"]
    except KeyError as exc:
        raise ValueError(f"Section manquante dans {path} : {exc}") from exc

    api = ApiConfig(
        base_url=api_raw["base_url"].rstrip("/"),
        network=api_raw["network"],
        api_key_env_var=api_raw["api_key_env_var"],
        api_key_header=api_raw["api_key_header"],
        calls_per_minute=float(api_raw["calls_per_minute"]),
        max_calls_per_run=int(api_raw["max_calls_per_run"]),
        request_timeout_seconds=float(api_raw["request_timeout_seconds"]),
        max_retries=int(api_raw["max_retries"]),
        backoff_base_seconds=float(api_raw["backoff_base_seconds"]),
        backoff_max_seconds=float(api_raw["backoff_max_seconds"]),
    )

    univers = UniversConfig(
        trending_pool_count=int(univers_raw["trending_pool_count"]),
        new_pools_sample_probability=float(univers_raw["new_pools_sample_probability"]),
        new_pools_page_count=int(univers_raw["new_pools_page_count"]),
        sampling_seed=int(univers_raw["sampling_seed"]),
        tracking_duration_hours=float(univers_raw["tracking_duration_hours"]),
        min_coverage_ratio=float(univers_raw["min_coverage_ratio"]),
    )
    if not (0.0 <= univers.new_pools_sample_probability <= 1.0):
        raise ValueError("univers.new_pools_sample_probability doit être dans [0, 1]")
    if not (0.0 <= univers.min_coverage_ratio <= 1.0):
        raise ValueError("univers.min_coverage_ratio doit être dans [0, 1]")

    ohlcv_backfill = OhlcvBackfillConfig(
        timeframe=ohlcv_raw["timeframe"],
        aggregate=int(ohlcv_raw["aggregate"]),
        max_candles_per_pool=int(ohlcv_raw["max_candles_per_pool"]),
    )

    storage = StorageConfig(
        raw_dir=base_dir / storage_raw["raw_dir"],
        cache_dir=base_dir / storage_raw["cache_dir"],
        log_dir=base_dir / storage_raw["log_dir"],
    )

    return CollectionConfig(
        api=api,
        univers=univers,
        ohlcv_backfill=ohlcv_backfill,
        storage=storage,
        source_path=path,
    )
