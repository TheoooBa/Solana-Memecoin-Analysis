"""Connexion en lecture seule à data/smc.sqlite3.

L'app ne collecte jamais rien elle-même et n'appelle jamais GeckoTerminal ni
RugCheck. Sur un poste où `data/` a déjà été rempli à la main (voir README),
elle se contente de lire. Sur un déploiement (Streamlit Community Cloud ne
clone que la branche `main`, jamais `data`), `ensure_fresh_database` clone la
branche `data` (dépôt public, lecture seule, aucune authentification) dans un
dossier temporaire et reconstruit la base — seule façon de rendre l'app
utilisable sans que quiconque tape une commande en local à chaque visite.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # pas de packaging pour ce projet, voir tests/conftest.py

DB_PATH = ROOT / "data" / "smc.sqlite3"
REPO_URL = "https://github.com/TheoooBa/Solana-Memecoin-Analysis.git"
SKIP_SYNC = os.environ.get("SMC_SKIP_SYNC") == "1"  # échappatoire pour un dev hors-ligne avec des données déjà en place


def db_exists() -> bool:
    return DB_PATH.exists()


@st.cache_resource(ttl=900, show_spinner="Synchronisation des données de collecte…")
def sync_and_build_database() -> str | None:
    """Retourne un message d'erreur, ou None en cas de succès. Mise en cache
    15 min : assez frais pour un tableau de bord, assez rare pour ne pas
    cloner à chaque interaction d'un visiteur."""
    if SKIP_SYNC:
        return None

    from smc_collector.build_db import build_database
    from smc_collector.config import load_config

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["git", "clone", "--branch", "data", "--depth", "1", REPO_URL, tmp],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return f"Échec du clonage de la branche `data` : {result.stderr.strip()[:300]}"

        for sub in ("raw", "logs"):
            src, dst = Path(tmp) / sub, ROOT / "data" / sub
            if not src.exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)

    try:
        config = load_config(str(ROOT / "config" / "collection.yaml"), base_dir=str(ROOT))
        build_database(config, DB_PATH)
    except Exception as exc:  # noqa: BLE001 - ne jamais empêcher l'app de s'afficher
        return f"Échec de la reconstruction de la base : {exc}"
    return None


def ensure_fresh_database() -> None:
    error = sync_and_build_database()
    if error:
        st.sidebar.warning(f"Synchronisation des données impossible : {error}")


@st.cache_resource
def _connection() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)


@st.cache_data(ttl=300, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, _connection(), params=params)


@st.cache_data(ttl=300, show_spinner=False)
def latest_data_timestamp() -> pd.Timestamp | None:
    """Horodatage le plus récent présent dans les données collectées.

    Utilisé comme "maintenant" pour toutes les fenêtres relatives (ex: "24h
    glissantes") : la base peut être plus ou moins à jour selon la dernière
    fois que la synchronisation a tourné, et ancrer les fenêtres sur la
    donnée réelle évite des pages vides trompeuses ("aucune donnée récente")
    alors que la collecte elle-même se porte bien.
    """
    df = query("SELECT MAX(request_timestamp_utc) AS ts FROM pool_snapshots WHERE source='snapshot'")
    if df.empty or pd.isna(df["ts"].iloc[0]):
        return None
    return pd.to_datetime(df["ts"].iloc[0], utc=True, format="ISO8601")
