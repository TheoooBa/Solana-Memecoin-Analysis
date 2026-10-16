"""Gel de config/analysis.yaml : voir reports/analysis_design_2026-10-14.md,
section 4, pour la justification complète.

Geler = enregistrer un hash du fichier de config tel qu'il est à cet
instant, plus l'horodatage du gel. Évaluer = recalculer ce hash et refuser
de tourner s'il ne correspond plus à ce qui a été gelé — empêche de
retoucher discrètement un seuil après avoir vu des résultats et de
prétendre que c'était la définition d'origine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class FreezeMismatchError(Exception):
    """Levée quand analysis.yaml a changé depuis le dernier gel, ou n'a
    jamais été gelé."""


@dataclass(frozen=True)
class FreezeRecord:
    config_hash: str
    frozen_at_utc: str


def compute_config_hash(config_path: str | Path) -> str:
    data = Path(config_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def freeze(config_path: str | Path, freeze_path: str | Path) -> FreezeRecord:
    record = FreezeRecord(
        config_hash=compute_config_hash(config_path),
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    freeze_path = Path(freeze_path)
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    with freeze_path.open("w", encoding="utf-8") as f:
        json.dump({"hash": record.config_hash, "frozen_at_utc": record.frozen_at_utc}, f, indent=2)
        f.write("\n")
    return record


def load_freeze(freeze_path: str | Path) -> FreezeRecord | None:
    freeze_path = Path(freeze_path)
    if not freeze_path.exists():
        return None
    with freeze_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return FreezeRecord(config_hash=raw["hash"], frozen_at_utc=raw["frozen_at_utc"])


def verify_freeze(config_path: str | Path, freeze_path: str | Path) -> FreezeRecord:
    """Retourne le FreezeRecord si analysis.yaml correspond bien au gel
    enregistré. Lève FreezeMismatchError sinon (jamais gelé, ou modifié
    depuis) — jamais un calcul silencieux contre une config retouchée."""
    record = load_freeze(freeze_path)
    if record is None:
        raise FreezeMismatchError(
            f"Aucun gel trouvé ({freeze_path}). Lance `analyze-freeze` avant `analyze-run`."
        )
    current_hash = compute_config_hash(config_path)
    if current_hash != record.config_hash:
        raise FreezeMismatchError(
            f"{config_path} a changé depuis le gel du {record.frozen_at_utc} "
            f"(hash gelé {record.config_hash[:12]}…, hash actuel {current_hash[:12]}…). "
            "Regèle explicitement (`analyze-freeze`) avant de réévaluer."
        )
    return record
