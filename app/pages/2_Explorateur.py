"""Page Explorateur : tous les pools suivis, comparaison tendance vs témoin,
et détail d'un pool (prix, sécurité RugCheck/token_info si disponible).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import badge, inject_css, page_header, render_health_sidebar
from db import db_exists, query

st.set_page_config(page_title="Memecoins Solana — Explorateur", page_icon="🔎", layout="wide")
inject_css()
render_health_sidebar()
page_header(
    "Explorateur de pools",
    "Comparer un pool à notre propre jeu de données — pas une redite de GeckoTerminal ou RugCheck, "
    "qui ont déjà chacun leur propre fiche token en direct.",
)

if not db_exists():
    st.warning("Aucune base locale trouvée. Lance `python -m smc_collector.cli build-db`.")
    st.stop()

registry = query(
    'SELECT pool_address, network, dex, "group", discovered_at_utc, base_token_address '
    'FROM pool_registry ORDER BY discovered_at_utc DESC'
)
if registry.empty:
    st.info("Aucun pool suivi pour l'instant.")
    st.stop()

last_snapshot = query(
    """
    SELECT s.pool_address, s.base_token_symbol, s.price_usd, s.reserve_usd, s.request_timestamp_utc
    FROM pool_snapshots s
    JOIN (
        SELECT pool_address, MAX(request_timestamp_utc) AS t_last
        FROM pool_snapshots WHERE source='snapshot'
        GROUP BY pool_address
    ) t ON t.pool_address = s.pool_address AND t.t_last = s.request_timestamp_utc
    """
)
table = registry.merge(last_snapshot, on="pool_address", how="left")

status = query('SELECT DISTINCT pool_address FROM pool_status_events WHERE event_type="tracking_window_ended"')
ended = set(status["pool_address"]) if not status.empty else set()
table["Statut"] = table["pool_address"].apply(lambda a: "Suivi terminé" if a in ended else "Actif")

group_label = {"trending": "Tendance", "random_new": "Témoin (aléatoire)"}
table["Groupe"] = table["group"].map(group_label).fillna(table["group"])

filter_col1, filter_col2 = st.columns([1, 3])
with filter_col1:
    choice = st.radio("Groupe", ["Tous", "Tendance", "Témoin (aléatoire)"], horizontal=False)
filtered = table if choice == "Tous" else table[table["Groupe"] == choice]

st.caption(f"{len(filtered)} pool(s) — {(table['Groupe'] == 'Tendance').sum()} tendance, "
           f"{(table['Groupe'] == 'Témoin (aléatoire)').sum()} témoin au total.")

display_cols = {
    "base_token_symbol": "Token", "dex": "DEX", "Groupe": "Groupe", "Statut": "Statut",
    "price_usd": "Dernier prix ($)", "reserve_usd": "Réserve ($)", "discovered_at_utc": "Découvert le",
}
shown = filtered.rename(columns=display_cols)[list(display_cols.values()) + ["pool_address"]]
st.dataframe(
    shown.drop(columns=["pool_address"]),
    hide_index=True, use_container_width=True, height=350,
)

st.markdown("### Détail d'un pool")
options = filtered.apply(
    lambda r: f"{r['base_token_symbol'] or '?'} — {r['pool_address'][:10]}…", axis=1
).tolist()
if not options:
    st.info("Aucun pool dans ce groupe.")
    st.stop()

selected_label = st.selectbox("Choisir un pool", options)
selected_address = filtered.iloc[options.index(selected_label)]["pool_address"]
selected_row = filtered.iloc[options.index(selected_label)]

c1, c2, c3 = st.columns(3)
c1.metric("Groupe", selected_row["Groupe"])
c2.metric("Statut", selected_row["Statut"])
c3.metric("DEX", selected_row["dex"] or "?")

history = query(
    "SELECT request_timestamp_utc, price_usd, reserve_usd FROM pool_snapshots "
    "WHERE pool_address = ? AND source IN ('snapshot', 'ohlcv_backfill') ORDER BY request_timestamp_utc",
    (selected_address,),
)
if not history.empty:
    history["request_timestamp_utc"] = pd.to_datetime(history["request_timestamp_utc"], utc=True, format="ISO8601")
    history["price_usd"] = pd.to_numeric(history["price_usd"], errors="coerce")
    st.line_chart(history.set_index("request_timestamp_utc")[["price_usd"]], height=280)
else:
    st.info("Pas d'historique de prix pour ce pool.")

st.markdown("#### Sécurité")
sec1, sec2 = st.columns(2)
with sec1:
    st.markdown('<div class="smc-title">GeckoTerminal (token_info)</div>', unsafe_allow_html=True)
    info = query(
        "SELECT gt_score, holders_top10_pct, mint_authority, freeze_authority, "
        "developer_holding_percentage, is_honeypot FROM token_info WHERE pool_address = ?",
        (selected_address,),
    )
    if info.empty:
        st.markdown(badge("Pas encore interrogé", "gray"), unsafe_allow_html=True)
    else:
        row = info.iloc[0]
        st.write(f"Score de confiance : {row['gt_score']}")
        st.write(f"Détenteurs — top 10 : {row['holders_top10_pct']}%")
        st.write(f"Autorité mint : {row['mint_authority']} · Autorité freeze : {row['freeze_authority']}")
        st.write(f"Indicateur honeypot (brut, non interprété) : {row['is_honeypot']}")

with sec2:
    st.markdown('<div class="smc-title">RugCheck.xyz</div>', unsafe_allow_html=True)
    rc = query(
        "SELECT score_normalised, rugged, permanent_delegate_present, transfer_hook_present, "
        "lp_locked_pct, insider_holders_count, risks_count FROM rugcheck_info WHERE pool_address = ?",
        (selected_address,),
    )
    if rc.empty:
        st.markdown(badge("Pas encore interrogé", "gray"), unsafe_allow_html=True)
    else:
        row = rc.iloc[0]
        rugged_color = "red" if str(row["rugged"]) == "True" else "green"
        st.markdown(badge(f"Rugged : {row['rugged']}", rugged_color), unsafe_allow_html=True)
        st.write(f"Score normalisé : {row['score_normalised']} · Risques détectés : {row['risks_count']}")
        st.write(f"Permanent delegate : {row['permanent_delegate_present']} · Transfer hook : {row['transfer_hook_present']}")
        st.write(f"LP verrouillée : {row['lp_locked_pct']}% · Détenteurs insiders : {row['insider_holders_count']}")

st.caption(
    "token_info et rugcheck_info se remplissent progressivement (un appel par pool, à sa découverte) "
    "à partir du prochain déploiement de la collecte — vide pour les pools déjà suivis avant ce déploiement."
)
