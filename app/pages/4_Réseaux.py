"""Page Réseaux : mentions et attention sociale. Table prête (schéma
décidé) mais jamais alimentée — aucun scraping X/Telegram par conception.
"""

from __future__ import annotations

import streamlit as st

from components import inject_css, page_header, render_health_sidebar

st.set_page_config(page_title="Memecoins Solana — Réseaux", page_icon="📣", layout="wide")
inject_css()
render_health_sidebar()
page_header("Réseaux", "Mentions et attention sociale — non alimenté pour l'instant.")

st.markdown(
    '<div class="smc-card">'
    '<div class="smc-title">Deux rôles, pas un seul</div>'
    '<div class="smc-muted">'
    "<strong style='color:#EDEDEE'>Renseignement</strong> : afficher le nombre de mentions d'un token, "
    "indépendamment de tout test statistique — une info utile à voir, même sans valeur prédictive prouvée.<br>"
    "<strong style='color:#EDEDEE'>Hypothèse testée</strong> : croisée avec les données techniques dans le "
    "registre de règles de la page Analyse, passée au même tamis (témoin, permutation) que tout le reste. "
    "Un résultat positif n'est jamais garanti à l'avance."
    "</div></div>",
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="smc-card">'
    '<div class="smc-title">Table des mentions</div>'
    '<div style="padding:36px 0;text-align:center;border:1px dashed #2B2E36;border-radius:10px;">'
    '<div class="smc-muted">Aucune donnée pour l\'instant</div>'
    '<div class="smc-muted" style="font-size:12px;margin-top:6px;">'
    "Pas de scraping automatique (X, Telegram exclus par conception) — à brancher plus tard via une "
    "source explicitement choisie, jamais en devinant depuis cette page."
    "</div></div></div>",
    unsafe_allow_html=True,
)
