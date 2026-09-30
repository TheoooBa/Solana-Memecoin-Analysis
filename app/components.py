"""Éléments d'interface partagés entre les pages : CSS, en-tête, badges,
panneau de santé du collecteur dans la barre latérale.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from db import db_exists, ensure_fresh_database, query

CSS = """
<style>
.smc-card {
    background: #1B1D23;
    border: 1px solid #2B2E36;
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 14px;
}
.smc-badge {
    display: inline-block;
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 999px;
    font-weight: 600;
}
.smc-badge-green { background: #16321F; color: #4FAE72; }
.smc-badge-amber { background: #332B16; color: #E0B24F; }
.smc-badge-gray  { background: #242730; color: #9498A3; }
.smc-badge-red   { background: #331616; color: #E05C4F; }
.smc-muted { color: #9498A3; font-size: 13px; }
.smc-dot {
    display: inline-block;
    width: 9px; height: 9px;
    border-radius: 50%;
    margin-right: 8px;
}
.smc-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
[data-testid="stMetricValue"] { font-family: monospace; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.markdown(f'<div class="smc-muted">{subtitle}</div>', unsafe_allow_html=True)
    st.write("")


def badge(text: str, color: str = "gray") -> str:
    return f'<span class="smc-badge smc-badge-{color}">{text}</span>'


def not_a_trading_bot_notice() -> None:
    st.caption(
        "Outil de recherche, pas un bot de trading : aucun wallet, aucune clé, "
        "aucune transaction envoyée depuis cette app."
    )


def render_health_sidebar() -> None:
    """Dernier run, écart moyen sur les dernières 24h réelles, erreurs.

    Ancré sur l'heure réelle (pas la dernière donnée disponible) : c'est
    justement un indicateur de fraîcheur, il doit refléter un vrai retard s'il
    y en a un, pas le masquer.
    """
    ensure_fresh_database()
    st.sidebar.markdown("**Santé du collecteur**")
    if not db_exists():
        st.sidebar.markdown(badge("Base introuvable", "red"), unsafe_allow_html=True)
        st.sidebar.caption("Lance `python -m smc_collector.cli build-db`.")
        return

    runs = query(
        "SELECT started_at_utc, exit_reason, errors_count FROM collector_runs "
        "ORDER BY started_at_utc DESC LIMIT 300"
    )
    if runs.empty:
        st.sidebar.markdown(badge("Aucune collecte enregistrée", "amber"), unsafe_allow_html=True)
        return

    runs["started_at_utc"] = pd.to_datetime(runs["started_at_utc"], utc=True, format="ISO8601")
    last_run = runs["started_at_utc"].iloc[0]
    now = datetime.now(timezone.utc)
    age_minutes = (now - last_run).total_seconds() / 60

    if age_minutes < 20:
        color, label = "green", f"Dernier run il y a {age_minutes:.0f} min"
    elif age_minutes < 120:
        color, label = "amber", f"Dernier run il y a {age_minutes:.0f} min"
    else:
        color, label = "red", f"Dernier run il y a {age_minutes / 60:.1f} h"

    last_24h = runs[runs["started_at_utc"] >= now - pd.Timedelta(hours=24)]
    if len(last_24h) >= 2:
        gaps = last_24h["started_at_utc"].sort_values().diff().dt.total_seconds().dropna() / 60
        avg_gap = f"{gaps.mean():.0f} min"
    else:
        avg_gap = "pas assez de runs sur 24h"

    errors_24h = int(last_24h["errors_count"].sum()) if not last_24h.empty else 0
    dot_hex = {"green": "#4FAE72", "amber": "#E0B24F", "red": "#E05C4F"}[color]

    st.sidebar.markdown(
        f'<div class="smc-card">'
        f'<span class="smc-dot" style="background:{dot_hex}"></span>{label}<br>'
        f'<span class="smc-muted">Écart moyen 24h : {avg_gap}</span><br>'
        f'<span class="smc-muted">Erreurs 24h : {errors_24h}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div style="margin-top:-8px" class="smc-muted">Données : GeckoTerminal + RugCheck.xyz</div>',
        unsafe_allow_html=True,
    )
