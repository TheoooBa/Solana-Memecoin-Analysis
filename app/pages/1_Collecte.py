"""Page Collecte : historique des runs, cadence, erreurs."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import inject_css, page_header, render_health_sidebar
from db import db_exists, query

st.set_page_config(page_title="Memecoins Solana — Collecte", page_icon="📡", layout="wide")
inject_css()
render_health_sidebar()
page_header("Collecte", "Cadence des exécutions et incidents — pas un signal de trading.")

if not db_exists():
    st.warning("Aucune base locale trouvée. Lance `python -m smc_collector.cli build-db`.")
    st.stop()

runs = query(
    "SELECT started_at_utc, finished_at_utc, exit_reason, calls_made, calls_budget, "
    "errors_count, pools_discovered_trending, pools_discovered_random, pools_snapshotted "
    "FROM collector_runs ORDER BY started_at_utc"
)

if runs.empty:
    st.info("Aucune exécution enregistrée pour l'instant.")
    st.stop()

runs["started_at_utc"] = pd.to_datetime(runs["started_at_utc"], utc=True, format="ISO8601")
runs["gap_minutes"] = runs["started_at_utc"].diff().dt.total_seconds() / 60

col1, col2, col3, col4 = st.columns(4)
col1.metric("Exécutions", len(runs))
col2.metric("Écart médian entre runs", f"{runs['gap_minutes'].median():.0f} min" if len(runs) > 1 else "—")
col3.metric("Écart moyen entre runs", f"{runs['gap_minutes'].mean():.0f} min" if len(runs) > 1 else "—")
col4.metric(
    "Erreurs cumulées", int(pd.to_numeric(runs["errors_count"], errors="coerce").sum()),
    help="Inclut les tentatives HTTP 429/5xx récupérées par une reprise — pas forcément des runs en échec.",
)

st.markdown(
    '<div class="smc-card"><div class="smc-title">Pourquoi la cadence a varié</div>'
    '<div class="smc-muted">Le cron interne de GitHub Actions (<code>schedule:</code>) ne tient pas '
    "l'intervalle configuré sous charge — un minuteur externe (voir "
    "<code>docs/guide_cron_externe.md</code>) contourne ce problème depuis le 2026-09-29. "
    "L'écart moyen ci-dessus reflète l'historique complet, avant et après ce correctif."
    "</div></div>",
    unsafe_allow_html=True,
)

st.markdown("### Écart entre exécutions consécutives")
gap_chart = runs.dropna(subset=["gap_minutes"]).set_index("started_at_utc")[["gap_minutes"]]
if not gap_chart.empty:
    st.line_chart(gap_chart, height=280)
else:
    st.info("Pas assez d'exécutions pour tracer un écart.")

st.markdown("### Détail des exécutions les plus récentes")
display = runs.sort_values("started_at_utc", ascending=False).head(50).copy()
display["started_at_utc"] = display["started_at_utc"].dt.strftime("%Y-%m-%d %H:%M:%S")
display = display.rename(columns={
    "started_at_utc": "Début", "exit_reason": "Sortie", "calls_made": "Appels",
    "calls_budget": "Budget", "errors_count": "Erreurs",
    "pools_discovered_trending": "Découverts (tendance)",
    "pools_discovered_random": "Découverts (témoin)",
    "pools_snapshotted": "Pools capturés", "gap_minutes": "Écart (min)",
})
st.dataframe(
    display[["Début", "Sortie", "Appels", "Budget", "Erreurs",
             "Découverts (tendance)", "Découverts (témoin)", "Pools capturés", "Écart (min)"]],
    hide_index=True, use_container_width=True,
)
