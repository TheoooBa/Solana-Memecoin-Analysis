"""Trading papier : validation d'un plan, puis simulation prudente sur les bougies réelles.

Règles de la simulation (toutes volontairement conservatrices) :
- Seules les bougies qui commencent APRÈS la décision comptent (aucune triche sur la bougie en cours).
- Ordre au marché si l'entrée est à moins de 0,1 % du prix courant, sinon ordre limite (achat sous le prix)
  ou ordre stop (achat au-dessus), valable `valid_hours`.
- Si stop et cible sont touchés dans la même bougie, le stop passe en premier.
- Gap à l'ouverture : on sort ou on entre au prix d'ouverture, pas au niveau théorique.
- Frais + slippage : `cost_bps` à l'entrée et à la sortie. Le résultat est exprimé en R (1R = risque initial).
"""
from __future__ import annotations

HORIZONS = (1, 24, 168)  # heures après le remplissage


def validate_proposal(p: dict, price: float, atr_abs, cfg):
    """Contrôle mécanique d'un plan long. Retourne (ok, raison, métriques)."""
    try:
        entry, stop, target = float(p["entry"]), float(p["stop"]), float(p["target"])
    except (KeyError, TypeError, ValueError):
        return False, "entry, stop ou target manquant", {}
    if not (stop < entry < target):
        return False, "ordre stop < entrée < cible non respecté", {}
    if price <= 0 or not atr_abs or atr_abs <= 0:
        return False, "prix ou ATR invalide", {}
    dist = (entry - price) / price * 100.0
    if abs(dist) > cfg.max_entry_dist_pct:
        return False, f"entrée trop loin du prix ({dist:+.1f} %)", {}
    risk_pct = (entry - stop) / entry * 100.0
    if risk_pct < cfg.min_risk_pct or risk_pct > cfg.max_risk_pct:
        return False, f"distance du stop hors limites ({risk_pct:.2f} %)", {}
    stop_atr = (entry - stop) / atr_abs
    if stop_atr < cfg.min_stop_atr:
        return False, f"stop trop serré ({stop_atr:.2f} ATR)", {}
    rr = (target - entry) / (entry - stop)
    if rr < cfg.min_rr:
        return False, f"ratio gain/risque trop faible ({rr:.2f})", {}
    return True, "ok", {
        "risk_pct": round(risk_pct, 2),
        "rr": round(rr, 2),
        "entry_dist_pct": round(dist, 2),
        "stop_atr": round(stop_atr, 2),
        "order_type": "market" if abs(dist) <= 0.1 else ("limit" if dist < 0 else "stop"),
    }


def simulate(trade: dict, candles: list, now: float, cost_bps: float, horizon_days: int = 7) -> dict:
    """Rejoue un plan sur les bougies fournies (celles qui commencent à partir de la décision).

    Statuts : pending (ordre en attente), expired (jamais rempli), open, stop, target, time, invalid.
    `r_h[h]` : résultat en R `h` heures après le remplissage (réalisé si déjà sorti, sinon latent),
    None si l'horizon n'est pas encore atteint.
    """
    entry, stop, target = float(trade["entry"]), float(trade["stop"]), float(trade["target"])
    ts, price0 = int(trade["ts"]), float(trade["price"])
    valid_until = ts + float(trade.get("valid_hours") or 12) * 3600
    market = abs(entry - price0) / price0 <= 0.001
    eff_entry = price0 if market else entry
    risk = eff_entry - stop

    res = {"status": "pending", "fill_price": None, "fill_ts": None, "exit_price": None,
           "exit_ts": None, "r_final": None, "r_now": None, "r_h": {h: None for h in HORIZONS}}
    if risk <= 0:
        res["status"] = "invalid"
        return res

    def r_of(px: float, fill: float) -> float:
        cost = fill * cost_bps * 2.0 / 10000.0
        return (px - fill - cost) / risk

    filled = market
    fill_price = price0 if market else None
    fill_ts = ts if market else None
    closed, status, exit_price, exit_ts = False, None, None, None
    path = []  # (fin de bougie, prix) après remplissage

    for c in candles:
        end = c["t"] + 3600
        if end > now:
            break
        if not filled:
            if c["t"] >= valid_until:
                status = "expired"
                break
            if entry < price0:                  # ordre limite sous le prix
                if c["l"] <= entry:
                    filled, fill_price = True, min(entry, c["o"])
            else:                               # ordre stop au-dessus du prix
                if c["h"] >= entry:
                    filled, fill_price = True, max(entry, c["o"])
            if not filled:
                continue
            fill_ts = c["t"]
        done = None
        if c["l"] <= stop:
            exit_price, done = min(stop, c["o"]), "stop"
        elif c["h"] >= target:
            exit_price, done = max(target, c["o"]), "target"
        elif end >= fill_ts + horizon_days * 86400:
            exit_price, done = c["c"], "time"
        if done:
            closed, status, exit_ts = True, done, end
            path.append((end, exit_price))
            break
        path.append((end, c["c"]))

    if not filled:
        res["status"] = status or ("expired" if now > valid_until else "pending")
        return res

    res["fill_price"], res["fill_ts"] = fill_price, fill_ts
    if closed:
        res.update(status=status, exit_price=exit_price, exit_ts=exit_ts,
                   r_final=r_of(exit_price, fill_price))
        res["r_now"] = res["r_final"]
    else:
        res["status"] = "open"
        if path:
            res["r_now"] = r_of(path[-1][1], fill_price)
    for h in HORIZONS:
        t_h = fill_ts + h * 3600
        if closed and exit_ts <= t_h:
            res["r_h"][h] = res["r_final"]
        else:
            for end, px in path:
                if end >= t_h:
                    res["r_h"][h] = r_of(px, fill_price)
                    break
    return res
