"""Accueil du tableau de bord. Lancer avec :
    PYTHONPATH=src streamlit run app/Home.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import inject_css, not_a_trading_bot_notice, page_header, render_health_sidebar
from db import db_exists, latest_data_timestamp, query

st.set_page_config(page_title="Memecoins Solana — Accueil", page_icon="📊", layout="wide")
inject_css()
render_health_sidebar()

page_header(
    "Analyse des memecoins Solana",
    "Un outil de recherche qui cherche à réfuter, pas à confirmer, l'idée qu'il existe un avantage exploitable.",
)
not_a_trading_bot_notice()

if not db_exists():
    st.warning(
        "Aucune base locale trouvée. Lance `python -m smc_collector.cli build-db` "
        "après avoir récupéré les CSV de la branche `data`."
    )
    st.stop()

now = latest_data_timestamp()

col1, col2, col3, col4 = st.columns(4)

pools = query('SELECT "group", COUNT(*) AS n FROM pool_registry GROUP BY "group"')
n_trending = int(pools.loc[pools["group"] == "trending", "n"].sum()) if not pools.empty else 0
n_temoin = int(pools.loc[pools["group"] == "random_new", "n"].sum()) if not pools.empty else 0

runs = query("SELECT COUNT(*) AS n, MIN(started_at_utc) AS first_run FROM collector_runs")
n_runs = int(runs["n"].iloc[0]) if not runs.empty else 0
snapshots = query("SELECT COUNT(*) AS n FROM pool_snapshots WHERE source='snapshot'")
n_snapshots = int(snapshots["n"].iloc[0]) if not snapshots.empty else 0

col1.metric("Pools tendance", n_trending)
col2.metric("Pools témoin (aléatoire)", n_temoin)
col3.metric("Instantanés collectés", f"{n_snapshots:,}".replace(",", " "), help="Hors historique OHLCV reconstitué à la découverte d'un pool.")
col4.metric("Exécutions de collecte", n_runs)

st.markdown(
    '<div class="smc-card">'
    '<div class="smc-title">Deux groupes, une seule raison d\'être</div>'
    '<div class="smc-muted">'
    "« tendance » : mis en avant par GeckoTerminal (boîte noire, présuppose déjà un succès). "
    "« témoin » : échantillon aléatoire de nouveaux pools, tiré avec une graine fixe — "
    "sans lui, on ne verrait que des pools qui ont déjà réussi, et toute statistique serait biaisée. "
    "C'est la comparaison entre les deux, pas la fiche d'un token isolé, qui peut un jour révéler un avantage."
    "</div></div>",
    unsafe_allow_html=True,
)

st.markdown("### Top variations (fenêtre de données la plus récente)")
if now is None:
    st.info("Pas encore de données de prix.")
else:
    window_start = now - pd.Timedelta(hours=24)
    moves = query(
        """
        SELECT pool_address, base_token_symbol, COUNT(*) AS n
        FROM pool_snapshots
        WHERE source='snapshot' AND price_usd IS NOT NULL AND price_usd != ''
          AND request_timestamp_utc >= ?
        GROUP BY pool_address
        HAVING COUNT(*) >= 2
        """,
        (window_start.isoformat(),),
    )
    if moves.empty:
        st.info("Pas assez d'observations sur la fenêtre la plus récente pour calculer une variation.")
    else:
        first_prices = query(
            """
            SELECT s.pool_address, s.price_usd AS p_first
            FROM pool_snapshots s
            JOIN (
                SELECT pool_address, MIN(request_timestamp_utc) AS t_first
                FROM pool_snapshots WHERE source='snapshot' AND request_timestamp_utc >= ?
                GROUP BY pool_address
            ) t ON t.pool_address = s.pool_address AND t.t_first = s.request_timestamp_utc
            """,
            (window_start.isoformat(),),
        )
        last_prices = query(
            """
            SELECT s.pool_address, s.price_usd AS p_last
            FROM pool_snapshots s
            JOIN (
                SELECT pool_address, MAX(request_timestamp_utc) AS t_last
                FROM pool_snapshots WHERE source='snapshot' AND request_timestamp_utc >= ?
                GROUP BY pool_address
            ) t ON t.pool_address = s.pool_address AND t.t_last = s.request_timestamp_utc
            """,
            (window_start.isoformat(),),
        )
        merged = moves.merge(first_prices, on="pool_address").merge(last_prices, on="pool_address")
        merged["p_first"] = pd.to_numeric(merged["p_first"], errors="coerce")
        merged["p_last"] = pd.to_numeric(merged["p_last"], errors="coerce")
        merged = merged.dropna(subset=["p_first", "p_last"])
        merged = merged[merged["p_first"] > 0]
        merged["variation_pct"] = (merged["p_last"] - merged["p_first"]) / merged["p_first"] * 100
        merged["base_token_symbol"] = merged["base_token_symbol"].fillna("?")

        top_up = merged.sort_values("variation_pct", ascending=False).head(5)
        top_down = merged.sort_values("variation_pct", ascending=True).head(5)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="smc-muted">Plus fortes hausses</div>', unsafe_allow_html=True)
            st.dataframe(
                top_up[["base_token_symbol", "variation_pct"]].rename(
                    columns={"base_token_symbol": "Token", "variation_pct": "Variation %"}
                ).style.format({"Variation %": "{:+.1f}"}),
                hide_index=True, use_container_width=True,
            )
        with c2:
            st.markdown('<div class="smc-muted">Plus fortes baisses</div>', unsafe_allow_html=True)
            st.dataframe(
                top_down[["base_token_symbol", "variation_pct"]].rename(
                    columns={"base_token_symbol": "Token", "variation_pct": "Variation %"}
                ).style.format({"Variation %": "{:+.1f}"}),
                hide_index=True, use_container_width=True,
            )
        st.caption(
            "Variation entre la première et la dernière observation de chaque pool sur la fenêtre — "
            "pas un signal de trading, un simple aperçu de l'activité déjà collectée."
        )
