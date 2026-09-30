"""Connexion en lecture seule à data/smc.sqlite3.

L'app ne collecte jamais rien elle-même et n'appelle aucune API : elle lit
uniquement la base déjà construite par `python -m smc_collector.cli build-db`
à partir des CSV append-only (source de vérité). Si la base n'existe pas
encore, chaque page l'indique clairement plutôt que de planter.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "smc.sqlite3"


def db_exists() -> bool:
    return DB_PATH.exists()


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
    fois que `build-db` a tourné, et les données elles-mêmes peuvent dater de
    plusieurs jours en développement local. Ancrer les fenêtres sur la donnée
    réelle évite des pages vides trompeuses ("aucune donnée récente") alors
    que la collecte elle-même se porte bien.
    """
    df = query("SELECT MAX(request_timestamp_utc) AS ts FROM pool_snapshots WHERE source='snapshot'")
    if df.empty or pd.isna(df["ts"].iloc[0]):
        return None
    return pd.to_datetime(df["ts"].iloc[0], utc=True, format="ISO8601")
