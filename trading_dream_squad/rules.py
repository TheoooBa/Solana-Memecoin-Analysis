"""La « règle bête » : quatre agents sans LLM, déterministes et gratuits.

Sert à deux choses : tester toute la chaîne sans clé API (dry-run), et surtout servir de point de
comparaison. Si l'escouade Claude ne fait pas mieux qu'une règle de 20 lignes, elle ne sert à rien.
"""
from __future__ import annotations

from util import rp


def r_scout(p: dict) -> dict:
    pool = p.get("pool", [])[: p.get("max_picks", 3)]
    picks = [{"symbol": f["symbol"], "why": "meilleur score numérique"} for f in pool]
    return {"picks": picks, "message": "Je prends les plus gros mouvements anormaux du moment." if picks
            else "Rien d'anormal."}


def r_news(p: dict) -> dict:
    recent = [h for h in p.get("headlines", []) if h.get("age_h", 99) <= 12]
    if not recent:
        return {"cause": "aucun titre récent", "cause_type": "none_found", "timing": "unclear",
                "confidence": 0, "message": "Aucun titre récent n'explique le mouvement."}
    h = recent[0]
    return {"cause": h["title"], "cause_type": "catalyst", "timing": "unclear", "confidence": 30,
            "message": f"Titre le plus récent : {h['title']} (il y a {h['age_h']} h)."}


def r_chart(p: dict) -> dict:
    f = p["features"]
    price, atr = f["price"], f["atr"]
    if f["chg_4h"] > 0 and f["rsi14"] < 80:
        entry = price - 0.5 * atr          # on attend un petit repli plutôt que d'acheter le sommet
        stop = entry - 1.5 * atr
        target = entry + 3.0 * atr
        return {"action": "long", "entry": rp(entry), "stop": rp(stop), "target": rp(target),
                "valid_hours": 6, "rationale": "pullback d'un demi ATR après un mouvement haussier",
                "message": "Achat limite sur un demi ATR de repli, stop 1,5 ATR, cible 3 ATR."}
    return {"action": "no_trade", "entry": None, "stop": None, "target": None, "valid_hours": 0,
            "rationale": "pas de momentum haussier", "message": "Pas de plan : le mouvement n'est pas haussier."}


def r_skeptic(p: dict) -> dict:
    f = p["features"]
    if f["rsi14"] >= 75:
        verdict, conf, msg = "wait", 40, "Trop étendu (RSI élevé), j'attends un meilleur prix."
    elif f["vol_ratio"] >= 1.5:
        verdict, conf, msg = "buy", 50, "Volume confirmé et pas de surchauffe."
    else:
        verdict, conf, msg = "skip", 60, "Volume trop faible pour croire au mouvement."
    return {"verdict": verdict, "confidence": conf, "top_risks": [], "what_would_change_my_mind": "",
            "message": msg}


RULES = {"scout": r_scout, "news": r_news, "chart": r_chart, "skeptic": r_skeptic}
