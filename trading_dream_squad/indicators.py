"""Indicateurs techniques en Python pur, sur des bougies {t, o, h, l, c, v}."""
from __future__ import annotations

from statistics import pstdev

from util import rp


def pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0 if b else 0.0


def sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def rsi(closes, n: int = 14):
    """RSI de Wilder."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def atr(candles, n: int = 14):
    """Average True Range de Wilder, en unités de prix."""
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def move_zscore(closes, k: int = 4, lookback: int = 168) -> float:
    """Taille du mouvement des k dernières bougies, en écarts-types de l'historique de ce même actif."""
    if len(closes) < k + 30:
        return 0.0
    rets = [pct(closes[i], closes[i - k]) for i in range(k, len(closes) - 1)][-lookback:]
    sd = pstdev(rets) if len(rets) > 1 else 0.0
    last = pct(closes[-1], closes[-1 - k])
    return last / sd if sd > 0 else 0.0


def vol_ratio(candles, k: int = 3, base: int = 24) -> float:
    """Volume moyen des k dernières bougies divisé par celui des `base` bougies précédentes."""
    if len(candles) < k + base:
        return 1.0
    recent = sum(c["v"] for c in candles[-k:]) / k
    prev = [c["v"] for c in candles[-(k + base):-k]]
    avg = sum(prev) / len(prev)
    return recent / avg if avg > 0 else 1.0


def features(candles):
    """Résumé numérique d'un actif à partir de ses bougies horaires fermées. None si pas assez d'historique."""
    if len(candles) < 60:
        return None
    cl = [c["c"] for c in candles]
    price = cl[-1]
    a, r = atr(candles), rsi(cl)
    if a is None or r is None or price <= 0:
        return None
    last24, last168 = candles[-24:], candles[-168:]
    hi24, lo24 = max(c["h"] for c in last24), min(c["l"] for c in last24)
    hi7, lo7 = max(c["h"] for c in last168), min(c["l"] for c in last168)
    return {
        "price": rp(price),
        "chg_1h": round(pct(cl[-1], cl[-2]), 2),
        "chg_4h": round(pct(cl[-1], cl[-5]), 2),
        "chg_24h": round(pct(cl[-1], cl[-25]), 2) if len(cl) >= 25 else 0.0,
        "z4": round(move_zscore(cl, 4), 2),
        "vol_ratio": round(vol_ratio(candles), 2),
        "rsi14": round(r, 1),
        "atr": rp(a),
        "atr_pct": round(a / price * 100, 2),
        "hi24": rp(hi24), "lo24": rp(lo24), "hi7d": rp(hi7), "lo7d": rp(lo7),
        "from_hi24_pct": round(pct(price, hi24), 2),
        "from_lo24_pct": round(pct(price, lo24), 2),
        "sma20": rp(sma(cl, 20)), "sma50": rp(sma(cl, 50)),
    }
