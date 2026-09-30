"""Écriture et lecture des fichiers CSV append-only, un par jour.

Format CSV choisi plutôt que Parquet : les exécutions ajoutent quelques lignes
toutes les 5 minutes, et l'append CSV est trivial et sûr (ouverture en mode
"a", une écriture), alors que Parquet imposerait de réécrire le fichier à
chaque run. `build-db` convertit ensuite tout en SQLite pour l'analyse.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_path(directory: Path, dt: datetime | None = None) -> Path:
    dt = dt or datetime.now(timezone.utc)
    return directory / f"{dt.strftime('%Y-%m-%d')}.csv"


def _read_header(path: Path) -> list[str] | None:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return next(reader, None)


class AppendOnlyCsvWriter:
    """Ajoute des lignes à un CSV journalier, écrit l'en-tête une seule fois.

    Jamais de réécriture ni de suppression : c'est la garantie centrale de
    traçabilité du projet (un pool mort reste dans les fichiers). Corollaire
    important : si le fichier du jour existe déjà avec un en-tête différent
    (schéma changé en cours de journée, ce qui arrive justement le jour d'une
    migration comme v1→v2), on n'y ajoute JAMAIS de lignes dont les colonnes
    ne correspondraient plus à cet en-tête — cela désaligne silencieusement
    les données déjà écrites avec les nouvelles. On écrit à la place dans un
    fichier suffixé (`AAAA-MM-JJ_v2.csv`, `_v3.csv`, ...) portant le nouvel
    en-tête, jamais retouché non plus une fois créé. Le suffixe utilise "_"
    (et non ".") pour que le tri lexicographique des noms de fichiers dans
    `read_all_rows` place bien le fichier de base avant ses suffixes : "."
    (0x2E) trie avant "_" (0x5F) en ASCII.
    """

    def __init__(self, directory: Path, fieldnames: list[str]) -> None:
        self.directory = Path(directory)
        self.fieldnames = fieldnames
        self.directory.mkdir(parents=True, exist_ok=True)

    def _target_path(self, dt: datetime | None) -> Path:
        base = day_path(self.directory, dt)
        if not base.exists():
            return base
        if _read_header(base) == self.fieldnames:
            return base
        suffix = 2
        while True:
            candidate = base.with_name(f"{base.stem}_v{suffix}{base.suffix}")
            if not candidate.exists():
                return candidate
            if _read_header(candidate) == self.fieldnames:
                return candidate
            suffix += 1

    def append_rows(self, rows: Iterable[dict], dt: datetime | None = None) -> int:
        rows = list(rows)
        if not rows:
            return 0
        path = self._target_path(dt)
        write_header = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return len(rows)


def read_all_rows(directory: Path) -> Iterator[dict]:
    """Relit toutes les lignes de tous les fichiers journaliers d'un répertoire,
    triés par nom de fichier (donc par date). Utilisé pour reconstruire l'état
    de l'univers suivi : aucun état local séparé n'est nécessaire.
    """
    directory = Path(directory)
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            yield from reader
