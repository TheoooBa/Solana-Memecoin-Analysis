"""Évalue le journal : que sont devenus les plans ? Le skeptic sert-il à quelque chose ? Claude bat-il la règle bête ?

  python evaluate.py --market crypto --horizon 24
  python evaluate.py --market mock --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from math import sqrt
from statistics import mean, stdev

import paper
from config import get_config
from journal import Journal
from providers import make_provider

FILLED = ("open", "stop", "target", "time")
MIN_N = 30  # en dessous, on ne conclut rien


def stats(rs: list) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0}
    sd = stdev(rs) if n > 1 else 0.0
    return {"n": n, "win": sum(r > 0 for r in rs) / n, "avg": mean(rs), "sd": sd,
            "se": sd / sqrt(n) if n > 1 else 0.0, "tot": sum(rs)}


def welch_t(a: dict, b: dict):
    if a["n"] < 2 or b["n"] < 2:
        return None
    den = sqrt(a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"])
    return (a["avg"] - b["avg"]) / den if den > 0 else None


def max_drawdown(rs_in_order: list) -> float:
    peak = cum = dd = 0.0
    for r in rs_in_order:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def simulate_all(provider, cfg, trades: list, now: int) -> None:
    by_sym = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)
    for sym, ts in by_sym.items():
        start = min(t["ts"] for t in ts)
        try:
            cs = provider.candles_between(sym, start, now)
        except Exception as e:
            print(f"  ! données {sym}: {e}")
            cs = []
        for t in ts:
            t["sim"] = paper.simulate(t, [c for c in cs if c["t"] >= t["ts"]], now, cfg.cost_bps, cfg.horizon_days)


def group_rows(trades: list, horizon: int) -> dict:
    """Pour un groupe de plans : taux de remplissage et R à l'horizon, uniquement sur les plans remplis."""
    decided = [t for t in trades if t["sim"]["status"] in FILLED + ("expired",)]
    filled = [t for t in decided if t["sim"]["status"] in FILLED]
    resolved = [t for t in filled if t["sim"]["r_h"].get(horizon) is not None]
    resolved.sort(key=lambda t: t["sim"]["fill_ts"])
    rs = [t["sim"]["r_h"][horizon] for t in resolved]
    s = stats(rs)
    s["fill"] = len(filled) / len(decided) if decided else None
    s["total"] = len(trades)
    s["dd"] = max_drawdown(rs)
    s["rs"] = rs
    return s


def fmt(s: dict) -> str:
    if s["n"] == 0:
        fill = "  -  " if s.get("fill") is None else f"{s['fill']:4.0%}"
        return f"{s['total']:5d} {0:4d} {fill:>6}      -       -       -       -       -"
    fill = "  -  " if s["fill"] is None else f"{s['fill']:4.0%}"
    return (f"{s['total']:5d} {s['n']:4d} {fill:>6} {s['win']:6.0%} {s['avg']:+7.2f} {s['se']:7.2f} "
            f"{s['tot']:+7.1f} {s['dd']:7.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["crypto", "stocks", "mock"], required=True)
    ap.add_argument("--horizon", type=int, choices=list(paper.HORIZONS), default=24, help="heures après le remplissage")
    ap.add_argument("--db", default=None)
    ap.add_argument("--csv", default=None, help="exporte chaque plan et son résultat")
    args = ap.parse_args()

    cfg = get_config(args.market)
    db_path = args.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"journal_{args.market}.sqlite")
    if not os.path.exists(db_path):
        raise SystemExit(f"Pas de journal à {db_path}. Lance d'abord run.py.")
    journal = Journal(db_path)
    provider = make_provider(args.market, cfg)
    now = provider.eval_now()

    trades = journal.trades(args.market, proposals_only=True)
    if not trades:
        raise SystemExit("Aucun plan dans le journal pour l'instant.")
    simulate_all(provider, cfg, trades, now)
    H = args.horizon

    print(f"\nMarché : {args.market} | horizon : {H} h après remplissage | plans valides : {len(trades)}")
    print("R = gain en multiples du risque initial, frais inclus. n = plans remplis et résolus à l'horizon.")
    print(f"\n{'variante':8} {'groupe':26} {'plans':>5} {'n':>4} {'fill':>6} {'win':>6} {'R moy':>7} {'±SE':>7} {'R tot':>7} {'DD(R)':>7}")
    groups = {}
    for variant in sorted({t["variant"] for t in trades}):
        vt = [t for t in trades if t["variant"] == variant]
        defs = [("toutes (sans skeptic)", vt),
                ("skeptic = buy", [t for t in vt if t["skeptic_verdict"] == "buy"]),
                ("skeptic = wait/skip", [t for t in vt if t["skeptic_verdict"] != "buy"]),
                ("portefeuille (desk)", [t for t in vt if t["final_action"] == "buy"])]
        for label, ts in defs:
            g = group_rows(ts, H)
            groups[(variant, label)] = g
            print(f"{variant:8} {label:26} {fmt(g)}")
        print()

    print("Lecture")
    for variant in sorted({t["variant"] for t in trades}):
        a, b = groups[(variant, "skeptic = buy")], groups[(variant, "skeptic = wait/skip")]
        t = welch_t(a, b)
        if t is None:
            print(f"  [{variant}] skeptic : pas assez de données dans les deux groupes pour comparer.")
            continue
        enough = a["n"] >= MIN_N and b["n"] >= MIN_N
        verdict = ("écart notable (t > 2), à confirmer sur d'autres semaines" if abs(t) > 2 and enough and t > 0 else
                   "le skeptic refuse mieux qu'il n'accepte, ce qui est l'inverse de l'attendu" if abs(t) > 2 and enough else
                   "aucun écart distinguable du hasard")
        warn = "" if enough else f" (moins de {MIN_N} plans dans un groupe : bruit)"
        print(f"  [{variant}] skeptic buy vs refusés : {a['avg']:+.2f} R contre {b['avg']:+.2f} R, t = {t:+.2f} -> {verdict}{warn}")
    if ("claude", "portefeuille (desk)") in groups and ("rules", "portefeuille (desk)") in groups:
        a, b = groups[("claude", "portefeuille (desk)")], groups[("rules", "portefeuille (desk)")]
        t = welch_t(a, b)
        if t is not None:
            enough = a["n"] >= MIN_N and b["n"] >= MIN_N
            warn = "" if enough else f" (moins de {MIN_N} trades dans un portefeuille : bruit)"
            print(f"  Claude vs règle bête (portefeuilles) : {a['avg']:+.2f} R contre {b['avg']:+.2f} R, t = {t:+.2f}{warn}")
    print("  Repère : |t| < 2 veut dire que l'écart reste compatible avec le hasard. Plusieurs comparaisons = risque de faux positif.")

    u = journal.usage_totals()
    if u["calls"]:
        print(f"\nCoût LLM observé : {u['calls']} appels sur {u['cycles']} cycles, "
              f"{u['tin']:,} tokens en entrée, {u['tout']:,} en sortie (applique ta grille tarifaire).")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "variant", "symbol", "entry", "stop", "target", "rr", "skeptic", "final",
                        "status", "fill_price", "exit_price", f"R_{H}h", "R_final", "news_type", "news_timing"])
            for t in trades:
                s = t["sim"]
                w.writerow([t["ts"], t["variant"], t["symbol"], t["entry"], t["stop"], t["target"], t["rr"],
                            t["skeptic_verdict"], t["final_action"], s["status"], s["fill_price"], s["exit_price"],
                            s["r_h"].get(H), s["r_final"], t["news_type"], t["news_timing"]])
        print(f"\nExport : {args.csv}")


if __name__ == "__main__":
    main()
