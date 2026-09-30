"""Page Analyse : résultat par défaut honnête tant que l'étape 3 n'existe pas."""

from __future__ import annotations

import streamlit as st

from components import badge, inject_css, page_header, render_health_sidebar
from db import db_exists, query

st.set_page_config(page_title="Memecoins Solana — Analyse", page_icon="🧪", layout="wide")
inject_css()
render_health_sidebar()
page_header("Analyse", "Résultats et gel de configuration.")

st.markdown(
    '<div class="smc-card" style="display:flex;gap:14px;align-items:flex-start;">'
    f'{badge("Aucun avantage démontré", "gray")}'
    '<div class="smc-muted">C\'est le résultat par défaut de ce projet, pas une case à cocher : '
    "aucune règle n'a encore été testée sur des données gelées. Un résultat positif ne sera "
    "présenté ici que s'il bat un tirage aléatoire apparié dans le temps (test de permutation), "
    "sur un nombre d'événements suffisant, mesuré après frais et slippage."
    "</div></div>",
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="smc-card">'
    f'{badge("Non gelée", "gray")} '
    '<span class="smc-muted">Configuration d\'analyse — l\'étape 3 (module d\'analyse, simulateur, '
    "gel de configuration) n'a pas encore commencé. Elle démarrera une fois plusieurs semaines de "
    "données réunies (voir README, « Prochaines étapes »)."
    "</span></div>",
    unsafe_allow_html=True,
)

if db_exists():
    runs = query("SELECT MIN(started_at_utc) AS first, MAX(started_at_utc) AS last, COUNT(*) AS n FROM collector_runs")
    if not runs.empty and runs["n"].iloc[0] > 0:
        st.caption(
            f"Données disponibles aujourd'hui : {int(runs['n'].iloc[0])} exécutions, "
            f"de {runs['first'].iloc[0]} à {runs['last'].iloc[0]}."
        )

st.markdown(
    "Rien de plus n'est affiché ici volontairement : pas de tableau de règles ni de score tant "
    "qu'aucune n'a réellement été testée. Voir la maquette de conception pour un aperçu de la forme "
    "que prendra cette page une fois l'étape 3 commencée."
)
