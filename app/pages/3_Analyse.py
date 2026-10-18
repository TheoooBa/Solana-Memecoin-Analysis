"""Page Analyse : résultats réels une fois le gel effectué, honnêtement
vide tant qu'il ne l'est pas. Jamais de règle fabriquée."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from components import badge, inject_css, page_header, render_health_sidebar

st.set_page_config(page_title="Memecoins Solana — Analyse", page_icon="🧪", layout="wide")
inject_css()
render_health_sidebar()
page_header("Analyse", "Résultats et gel de configuration.")

ROOT = Path(__file__).resolve().parent.parent.parent
FREEZE_PATH = ROOT / "config" / "analysis_freeze.json"
REGISTRY_PATH = ROOT / "analysis" / "registre_regles.csv"
MIN_DISTINCT_DAYS = 10  # doit rester cohérent avec config/analysis.yaml: minimum_sample.min_distinct_days

VERDICT_LABELS = {
    "donnees_insuffisantes": ("Données insuffisantes", "amber"),
    "ne_bat_pas_le_hasard": ("Ne bat pas le hasard", "gray"),
    "bat_le_hasard": ("Bat le hasard — validé", "green"),
}

if not FREEZE_PATH.exists():
    st.markdown(
        '<div class="smc-card" style="display:flex;gap:14px;align-items:flex-start;">'
        f'{badge("Aucun avantage démontré", "gray")}'
        '<div class="smc-muted">C\'est le résultat par défaut de ce projet, pas une case à cocher : '
        "aucune règle n'a encore été testée sur des données gelées."
        "</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="smc-card">'
        f'{badge("Non gelée", "gray")} '
        '<span class="smc-muted">config/analysis.yaml n\'a pas encore été gelé — '
        "<code>python -m smc_collector.cli analyze-freeze</code>.</span></div>",
        unsafe_allow_html=True,
    )
    st.stop()

freeze = json.loads(FREEZE_PATH.read_text())
frozen_at = pd.to_datetime(freeze["frozen_at_utc"], utc=True, format="ISO8601")
days_since_freeze = (pd.Timestamp.now(tz="UTC") - frozen_at).total_seconds() / 86400

st.markdown(
    '<div class="smc-card">'
    f'{badge("Gelée", "green")} '
    f'<span class="smc-muted">config/analysis.yaml gelé le {frozen_at:%Y-%m-%d %H:%M} UTC '
    f"({days_since_freeze:.1f} jour(s) écoulé(s)). Seules les données collectées après cette date "
    "comptent pour un verdict — voir "
    "<code>reports/analysis_design_2026-10-14.md</code>.</span></div>",
    unsafe_allow_html=True,
)

if days_since_freeze < MIN_DISTINCT_DAYS:
    st.info(
        f"Au moins {MIN_DISTINCT_DAYS} jours distincts de données post-gel sont requis avant qu'une "
        f"règle puisse sortir de « données insuffisantes » — encore "
        f"{MIN_DISTINCT_DAYS - days_since_freeze:.1f} jour(s) au minimum avant un premier verdict possible."
    )

if not REGISTRY_PATH.exists():
    st.warning("Aucune évaluation n'a encore tourné (`analyze-run`).")
    st.stop()

registry = pd.read_csv(REGISTRY_PATH)
registry["evaluated_at_utc"] = pd.to_datetime(registry["evaluated_at_utc"], utc=True, format="ISO8601")

# Une règle peut avoir été évaluée plusieurs fois (exécutions répétées
# d'analyze-run) : on ne garde que la plus récente par (règle, horizon)
# pour l'affichage courant — le CSV lui-même garde tout, jamais tronqué.
latest = (
    registry.sort_values("evaluated_at_utc")
    .groupby(["rule_id", "horizon_hours"], as_index=False)
    .last()
)

n_validated = (latest["verdict"] == "bat_le_hasard").sum()
st.markdown(
    f'<div class="smc-card"><div class="smc-title">Registre des règles testées</div>'
    f'<div class="smc-muted">{len(latest)} couples règle/horizon évalués — '
    f'{n_validated} présenté(s) comme avantage validé</div></div>',
    unsafe_allow_html=True,
)

display = latest.copy()
display["Résultat"] = display["verdict"].map(lambda v: VERDICT_LABELS.get(v, (v, "gray"))[0])
display["Horizon"] = display["horizon_hours"].astype(str) + "h"
display["Événements (témoin / non-témoin)"] = (
    display["n_events_a"].astype(str) + " / " + display["n_events_b"].astype(str)
)
display["Jours distincts"] = display["n_distinct_days"]
display["IC95%"] = display.apply(
    lambda r: f"{r['ci_low']:+.1%} à {r['ci_high']:+.1%}" if pd.notna(r["ci_low"]) else "—", axis=1
)
display["p"] = display["p_value"].map(lambda p: f"{p:.4f}" if pd.notna(p) else "—")

st.dataframe(
    display[["rule_id", "Horizon", "Événements (témoin / non-témoin)", "Jours distincts", "Résultat", "p", "IC95%"]]
    .rename(columns={"rule_id": "Règle"}),
    hide_index=True, use_container_width=True,
)

st.caption(
    "Chaque règle est comparée à un tirage aléatoire apparié dans le temps (test de permutation par "
    "bloc de jour) avant de pouvoir sortir de « données insuffisantes ». Aucun libellé « rentable » ou "
    "« gagnant » n'apparaît hors d'un résultat validé sur données gelées. Détail complet : "
    "`analysis/registre_regles.csv` (jamais tronqué, toutes les exécutions passées y restent)."
)
