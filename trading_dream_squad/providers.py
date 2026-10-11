"""Sources de données interchangeables : crypto (Binance, sans clé), actions (yfinance), mock (synthétique).

Contrat commun : toutes les bougies sont horaires, fermées, au format {t, o, h, l, c, v} avec t = ouverture
en secondes epoch UTC. Aucune méthode ne renvoie de bougie qui n'est pas encore fermée à l'instant `now`.
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.parse
import urllib.request

STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "USDE", "PYUSD", "XUSD", "AEUR", "USD1", "USDT"}
COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB", "XRP": "XRP Ripple",
              "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche", "LINK": "Chainlink", "TON": "Toncoin",
              "SUI": "Sui", "PEPE": "Pepe", "SHIB": "Shiba Inu", "TRX": "Tron", "DOT": "Polkadot",
              "NEAR": "NEAR Protocol", "LTC": "Litecoin", "UNI": "Uniswap", "AAVE": "Aave", "WIF": "dogwifhat"}


def http_get(url: str, params=None, timeout: int = 15, retries: int = 3) -> bytes:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trading-squad demo)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # réseau, 429, 418...
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def parse_news_rss(xml_bytes: bytes, now: float, max_items: int = 10, max_age_h: int = 48) -> list:
    """Flux RSS Google News -> [{title, source, ts}], plus récent d'abord, sans doublons."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    out, seen = [], set()
    for it in ET.fromstring(xml_bytes).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            continue
        if ts > now or now - ts > max_age_h * 3600:
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if title and title not in seen:
            seen.add(title)
            out.append({"title": title, "source": src, "ts": ts})
    out.sort(key=lambda x: -x["ts"])
    return out[:max_items]


def google_news(query: str, now: float, max_items: int = 10) -> list:
    raw = http_get("https://news.google.com/rss/search",
                   {"q": f"{query} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return parse_news_rss(raw, now, max_items)


def parse_klines(rows: list, now: float) -> list:
    """Format Binance : [openTime, o, h, l, c, volume, closeTime, quoteVolume, ...]. Volume en USDT."""
    out = []
    for r in rows:
        t = int(r[0]) // 1000
        if t + 3600 > now:
            continue
        out.append({"t": t, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                    "v": float(r[7])})
    return out


class Provider:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def universe(self, now: int) -> list:
        raise NotImplementedError

    def prefetch(self, symbols: list, now: int) -> None:
        pass

    def candles(self, symbol: str, now: int, limit: int = 200) -> list:
        raise NotImplementedError

    def candles_between(self, symbol: str, start: int, now: int) -> list:
        """Bougies fermées qui commencent à partir de `start` (inclus) et finissent avant `now`."""
        raise NotImplementedError

    def news(self, symbol: str, now: int, max_items: int = 10) -> list:
        return []

    def is_open(self, now: int) -> bool:
        return True

    def eval_now(self) -> int:
        return int(time.time())


class CryptoProvider(Provider):
    """Binance, endpoints publics de données de marché (aucune clé, aucun compte)."""

    name = "crypto"
    BASES = ["https://data-api.binance.vision", "https://api.binance.com"]

    def __init__(self, cfg):
        super().__init__(cfg)
        self._uni, self._uni_ts = None, 0

    def _get(self, path: str, params=None):
        last = None
        for base in self.BASES:
            try:
                return json.loads(http_get(base + path, params))
            except Exception as e:
                last = e
        raise last

    def universe(self, now: int) -> list:
        if self._uni and now - self._uni_ts < 3600:
            return self._uni
        rows = []
        for d in self._get("/api/v3/ticker/24hr"):
            sym = d["symbol"]
            if not sym.endswith("USDT") or sym[:-4] in STABLES:
                continue
            qv = float(d.get("quoteVolume") or 0)
            if qv >= self.cfg.min_quote_volume:
                rows.append((qv, sym))
        rows.sort(reverse=True)
        self._uni = [s for _, s in rows[: self.cfg.universe_size]]
        self._uni_ts = now
        return self._uni

    def candles(self, symbol, now, limit=200):
        rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": "1h",
                                            "limit": min(limit + 2, 1000), "endTime": int(now * 1000)})
        return parse_klines(rows, now)[-limit:]

    def candles_between(self, symbol, start, now):
        out, cur = [], int(start)
        while cur < now:
            rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": "1h", "startTime": cur * 1000,
                                                "endTime": int(now * 1000), "limit": 1000})
            if not rows:
                break
            out.extend(parse_klines(rows, now))
            if len(rows) < 1000:
                break
            cur = int(rows[-1][0]) // 1000 + 3600
        return out

    def news(self, symbol, now, max_items=10):
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        return google_news(f"{COIN_NAMES.get(base, base)} crypto", now, max_items)


def frame_to_candles(df) -> list:
    out = []
    for idx, row in df.iterrows():
        try:
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
            v = float(row["Volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(x != x for x in (o, h, l, c)):  # NaN
            continue
        out.append({"t": int(idx.timestamp()), "o": o, "h": h, "l": l, "c": c, "v": v})
    return out


class StockProvider(Provider):
    """Actions US via yfinance (non officiel, gratuit, parfois capricieux). Heures de bourse NY, jours fériés ignorés."""

    name = "stocks"
    TTL = 240

    def __init__(self, cfg):
        super().__init__(cfg)
        self._cache = {}

    def universe(self, now):
        return list(self.cfg.watchlist)

    def _closed(self, t: int, now: float) -> bool:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = datetime.fromtimestamp(t, ZoneInfo("America/New_York"))
        last_bar = ny.hour == 15 and ny.minute == 30  # dernière bougie de séance : 30 minutes seulement
        return now >= t + (1800 if last_bar else 3600)

    def prefetch(self, symbols, now):
        import yfinance as yf
        try:
            df = yf.download(" ".join(symbols), period="60d", interval="1h", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception:
            return
        for s in symbols:
            try:
                cs = frame_to_candles(df[s].dropna(how="all"))
            except Exception:
                continue
            if cs:
                self._cache[s] = (time.time(), cs)

    def _load(self, symbol):
        hit = self._cache.get(symbol)
        if hit and time.time() - hit[0] < self.TTL:
            return hit[1]
        import yfinance as yf
        cs = frame_to_candles(yf.Ticker(symbol).history(period="60d", interval="1h", auto_adjust=False))
        self._cache[symbol] = (time.time(), cs)
        return cs

    def candles(self, symbol, now, limit=200):
        return [c for c in self._load(symbol) if self._closed(c["t"], now)][-limit:]

    def candles_between(self, symbol, start, now):
        cs = self._load(symbol)
        if not cs or cs[0]["t"] > start:  # historique plus ancien que le cache : requête dédiée
            import yfinance as yf
            from datetime import datetime, timezone
            day = datetime.fromtimestamp(start - 86400, tz=timezone.utc).strftime("%Y-%m-%d")
            cs = frame_to_candles(yf.Ticker(symbol).history(start=day, interval="1h", auto_adjust=False))
        return [c for c in cs if c["t"] >= start and self._closed(c["t"], now)]

    def news(self, symbol, now, max_items=10):
        return google_news(f"{symbol} stock", now, max_items)

    def is_open(self, now):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = datetime.fromtimestamp(now, ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return False
        minutes = ny.hour * 60 + ny.minute
        return 9 * 60 + 30 <= minutes < 16 * 60


class MockProvider(Provider):
    """Marché synthétique reproductible, pour tester toute la chaîne sans réseau et rejouer l'historique.

    Contient des chocs de prix avec des titres associés (avant ou après le choc) et un régime qui suit :
    momentum, retour à la moyenne ou neutre. Aucun de ces résultats ne dit quoi que ce soit sur un vrai marché.
    """

    name = "mock"
    UP = ["{s} signs major partnership", "{s} beats expectations and raises guidance",
          "Analysts upgrade {s} after strong quarter", "{s} announces share buyback"]
    DOWN = ["{s} faces regulatory probe", "{s} cuts outlook on weak demand",
            "Short seller targets {s}", "{s} executive departs unexpectedly"]
    NOISE = ["Sector rotation hits growth names", "Markets await central bank decision"]
    SOURCES = ["Reuters", "Bloomberg", "CoinDesk", "MarketWatch"]

    def __init__(self, cfg, seed: int = 7, hours: int = 24 * 40):
        super().__init__(cfg)
        rng = random.Random(seed)
        self.t0 = 1_760_000_000 // 3600 * 3600
        self.hours = hours
        self.symbols = [f"MCK{chr(65 + i)}" for i in range(cfg.universe_size)]
        self.series, self.headlines = {}, {}
        for s in self.symbols:
            p = rng.uniform(5, 400)
            sigma = rng.uniform(0.003, 0.010)
            base_v = rng.uniform(1e3, 1e5)
            drift, regime_until = 0.0, -1
            cands, heads = [], []
            for i in range(hours):
                ret = rng.gauss(drift, sigma)
                volmult = 1.0
                if i > 100 and rng.random() < 1 / 160:
                    j = rng.choice([-1, 1]) * rng.uniform(0.04, 0.12)
                    ret += j
                    volmult = rng.uniform(3, 6)
                    kind = rng.random()
                    sign = 1 if j > 0 else -1
                    drift = 0.0012 * sign if kind < 0.45 else (-0.0012 * sign if kind < 0.8 else 0.0)
                    regime_until = i + 24
                    if rng.random() < 0.65:
                        lag = rng.uniform(-3, 4)
                        text = rng.choice(self.UP if j > 0 else self.DOWN).format(s=s)
                        heads.append({"title": text, "source": rng.choice(self.SOURCES),
                                      "ts": int(self.t0 + i * 3600 + lag * 3600)})
                elif i >= regime_until:
                    drift = 0.0
                if rng.random() < 1 / 60:
                    heads.append({"title": rng.choice(self.NOISE), "source": rng.choice(self.SOURCES),
                                  "ts": int(self.t0 + i * 3600 + rng.uniform(0, 3600))})
                o = p
                c = o * (1 + ret)
                h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 2)))
                l = min(o, c) * (1 - abs(rng.gauss(0, sigma / 2)))
                v = base_v * volmult * math.exp(rng.gauss(0, 0.3)) * (1 + 5 * abs(ret))
                cands.append({"t": self.t0 + i * 3600, "o": o, "h": h, "l": l, "c": c, "v": v})
                p = c
            self.series[s], self.headlines[s] = cands, heads

    def universe(self, now):
        return list(self.symbols)

    def candles(self, symbol, now, limit=200):
        return [c for c in self.series[symbol] if c["t"] + 3600 <= now][-limit:]

    def candles_between(self, symbol, start, now):
        return [c for c in self.series[symbol] if c["t"] >= start and c["t"] + 3600 <= now]

    def news(self, symbol, now, max_items=10):
        hs = [h for h in self.headlines[symbol] if h["ts"] <= now and now - h["ts"] <= 48 * 3600]
        hs.sort(key=lambda h: -h["ts"])
        return hs[:max_items]

    def eval_now(self):
        return self.t0 + self.hours * 3600


def make_provider(market: str, cfg) -> Provider:
    if market == "crypto":
        return CryptoProvider(cfg)
    if market == "stocks":
        return StockProvider(cfg)
    if market == "mock":
        return MockProvider(cfg)
    raise SystemExit(f"marché inconnu: {market}")
