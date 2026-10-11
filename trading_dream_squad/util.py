"""Petits utilitaires partagés."""
from __future__ import annotations

import time
from datetime import datetime, timezone


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def rp(x: float) -> float:
    """Arrondi à 6 chiffres significatifs (lisible dans un prompt, sans perte utile)."""
    return float(f"{x:.6g}")


def now_ts() -> int:
    return int(time.time())
