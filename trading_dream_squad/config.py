"""Réglages par marché. Tout ce qui se règle est ici."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

STOCK_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "GS", "BAC", "XOM", "COIN", "PLTR", "UBER", "SHOP", "DIS", "SMCI",
]


@dataclass
class MarketConfig:
    name: str
    cycle_minutes: int = 15
    universe_size: int = 30          # crypto: nombre de paires USDT les plus liquides
    min_quote_volume: float = 30e6   # crypto: volume 24h minimum en USDT
    watchlist: list = field(default_factory=list)
    max_candidates: int = 3          # nombre d'actifs que le scout peut envoyer au groupe par cycle
    pool_size: int = 8               # nombre d'actifs montrés au scout après tri numérique
    min_score: float = 2.0           # seuil du tri numérique (|z4| + bonus volume)
    cost_bps: float = 10.0           # frais + slippage par côté, en points de base
    valid_hours: float = 12.0        # durée de validité d'un ordre limite non rempli
    max_open: int = 5                # positions papier simultanées maximum
    cooldown_hours: float = 6.0      # on ne ré-analyse pas le même actif avant ce délai
    min_rr: float = 1.5              # ratio gain/risque minimum exigé du plan
    risk_pct: float = 0.01           # risque par trade, en part du capital virtuel
    equity: float = 10_000.0         # capital virtuel (affichage de la taille de position)
    horizon_days: int = 7            # sortie forcée au bout de ce délai
    max_entry_dist_pct: float = 4.0  # l'entrée doit rester à moins de 4 % du prix courant
    min_risk_pct: float = 0.3        # stop au moins à 0,3 % de l'entrée
    max_risk_pct: float = 12.0       # stop au plus à 12 % de l'entrée
    min_stop_atr: float = 0.9        # stop au moins à 0,9 ATR de l'entrée


PRESETS = {
    "crypto": MarketConfig(name="crypto", cycle_minutes=15, universe_size=30, min_quote_volume=30e6,
                           min_score=2.0, cost_bps=10.0, valid_hours=12.0, cooldown_hours=6.0),
    "stocks": MarketConfig(name="stocks", cycle_minutes=15, watchlist=STOCK_WATCHLIST,
                           min_score=1.8, cost_bps=3.0, valid_hours=8.0, cooldown_hours=8.0,
                           max_entry_dist_pct=3.0, max_risk_pct=8.0),
    "mock": MarketConfig(name="mock", cycle_minutes=60, universe_size=12, min_score=1.8,
                         cost_bps=5.0, valid_hours=12.0, cooldown_hours=6.0),
}


def get_config(market: str) -> MarketConfig:
    if market not in PRESETS:
        raise SystemExit(f"marché inconnu: {market} (choix: {', '.join(PRESETS)})")
    return replace(PRESETS[market])
