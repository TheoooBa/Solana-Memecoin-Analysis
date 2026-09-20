"""Reconstruction de l'univers suivi à partir des fichiers append-only.

Aucun état local séparé : l'état "quels pools sont actuellement suivis" est
entièrement dérivé, à chaque exécution, de la relecture des fichiers de
découverte et de statut déjà écrits sur disque (ou sur la branche de données
en environnement GitHub Actions). C'est ce qui rend `collect` idempotent et
lançable indifféremment à la main ou par une exécution planifiée.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .storage import read_all_rows

# Seuls ces types d'événement mettent fin au suivi actif d'un pool. Les autres
# (ex: "missing_from_multi_pool_response") sont des annotations diagnostiques :
# elles ne suppriment jamais le pool du suivi tant que sa fenêtre n'est pas finie.
STOP_EVENT_TYPES = {"tracking_window_ended"}


def deterministic_sample_decision(seed: int, pool_address: str, probability: float) -> bool:
    """Décide si un pool appartient à l'échantillon témoin, de façon pure et
    reproductible : ne dépend que de (graine, adresse), jamais de l'ordre ou
    du moment d'exécution. Une même adresse donne toujours la même décision
    pour une graine donnée, même si des runs différents ne voient pas les
    mêmes pages de l'API.
    """
    if probability <= 0.0:
        return False
    if probability >= 1.0:
        return True
    digest = hashlib.sha256(f"{seed}:{pool_address}".encode()).hexdigest()
    # Les 8 premiers hex donnent un entier uniforme sur [0, 2**32).
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < probability


@dataclass
class PoolState:
    pool_address: str
    network: str
    dex: str | None
    group: str
    discovered_at: datetime
    pool_created_at: str | None
    reason: str
    sampling_probability: float
    tracking_until: datetime
    stopped: bool = False
    stop_reason: str | None = None

    def is_active(self, now: datetime) -> bool:
        return not self.stopped and now < self.tracking_until


def load_universe(registry_dir, status_dir) -> dict[str, PoolState]:
    universe: dict[str, PoolState] = {}
    for row in read_all_rows(registry_dir):
        addr = row["pool_address"]
        if addr in universe:
            # Une découverte n'arrive qu'une fois par pool ; une ligne en trop
            # signalerait un bug d'écriture, pas un cas normal à silencer.
            continue
        universe[addr] = PoolState(
            pool_address=addr,
            network=row["network"],
            dex=row["dex"] or None,
            group=row["group"],
            discovered_at=datetime.fromisoformat(row["discovered_at_utc"]),
            pool_created_at=row["pool_created_at"] or None,
            reason=row["reason"],
            sampling_probability=float(row["sampling_probability"]),
            tracking_until=datetime.fromisoformat(row["tracking_until_utc"]),
        )

    for row in read_all_rows(status_dir):
        addr = row["pool_address"]
        state = universe.get(addr)
        if state is None:
            continue
        if row["event_type"] in STOP_EVENT_TYPES:
            state.stopped = True
            state.stop_reason = row["event_type"]

    return universe


def select_new_discoveries(
    trending_pools,
    new_pools,
    already_tracked: set[str],
    network: str,
    sampling_seed: int,
    sample_probability: float,
    tracking_duration_hours: float,
    now: datetime,
):
    """Détermine les nouveaux pools à ajouter au suivi lors de cette exécution.

    - Tous les pools "en tendance" pas encore suivis sont ajoutés (groupe
      "trending", sampling_probability=1.0 car il n'y a pas de tirage : ils
      entrent parce que la source les met en avant, c'est justement le biais
      qu'on documente).
    - Parmi les nouveaux pools, seuls ceux tirés au sort par
      `deterministic_sample_decision` sont ajoutés (groupe "random_new"),
      avec la probabilité effectivement utilisée enregistrée pour pondération
      ultérieure.

    Retourne une liste de dicts prêts à devenir des `PoolDiscovery`.
    """
    from .models import PoolDiscovery

    tracking_until = (now + timedelta(hours=tracking_duration_hours)).isoformat()
    discoveries: list[PoolDiscovery] = []
    seen_this_run: set[str] = set()

    for rank, pool in enumerate(trending_pools, start=1):
        addr = pool.pool_address
        if addr in already_tracked or addr in seen_this_run:
            continue
        seen_this_run.add(addr)
        discoveries.append(
            PoolDiscovery(
                pool_address=addr,
                network=network,
                dex=pool.dex,
                group="trending",
                discovered_at_utc=now.isoformat(),
                pool_created_at=pool.pool_created_at,
                reason=f"trending_rank_{rank}",
                sampling_probability=1.0,
                tracking_until_utc=tracking_until,
            )
        )

    for pool in new_pools:
        addr = pool.pool_address
        if addr in already_tracked or addr in seen_this_run:
            continue
        if not deterministic_sample_decision(sampling_seed, addr, sample_probability):
            continue
        seen_this_run.add(addr)
        discoveries.append(
            PoolDiscovery(
                pool_address=addr,
                network=network,
                dex=pool.dex,
                group="random_new",
                discovered_at_utc=now.isoformat(),
                pool_created_at=pool.pool_created_at,
                reason=f"new_pools_sampled_p={sample_probability}",
                sampling_probability=sample_probability,
                tracking_until_utc=tracking_until,
            )
        )

    return discoveries
