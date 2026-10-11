"""Journal SQLite : chaque cycle, chaque plan proposé, chaque verdict. C'est la mémoire qui permet de juger l'escouade."""
from __future__ import annotations

import os
import sqlite3

TRADE_COLS = [
    "cycle_id", "ts", "market", "variant", "symbol", "price", "entry", "stop", "target", "valid_hours",
    "risk_pct", "rr", "news_cause", "news_type", "news_timing", "news_conf", "chart_action",
    "chart_rationale", "skeptic_verdict", "skeptic_conf", "skeptic_risks", "skeptic_msg",
    "final_action", "final_reason", "transcript", "features",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles(
  id INTEGER PRIMARY KEY, ts INTEGER, market TEXT, n_universe INTEGER, n_ranked INTEGER,
  tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, calls INTEGER DEFAULT 0, note TEXT);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY, cycle_id INTEGER, ts INTEGER, market TEXT, variant TEXT, symbol TEXT,
  price REAL, entry REAL, stop REAL, target REAL, valid_hours REAL, risk_pct REAL, rr REAL,
  news_cause TEXT, news_type TEXT, news_timing TEXT, news_conf INTEGER,
  chart_action TEXT, chart_rationale TEXT,
  skeptic_verdict TEXT, skeptic_conf INTEGER, skeptic_risks TEXT, skeptic_msg TEXT,
  final_action TEXT, final_reason TEXT, transcript TEXT, features TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_variant_ts ON trades(variant, ts);
"""


class Journal:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def new_cycle(self, ts: int, market: str) -> int:
        cur = self.db.execute("INSERT INTO cycles(ts, market) VALUES (?, ?)", (ts, market))
        self.db.commit()
        return cur.lastrowid

    def end_cycle(self, cycle_id, n_universe, n_ranked, tokens_in, tokens_out, calls, note=""):
        self.db.execute(
            "UPDATE cycles SET n_universe=?, n_ranked=?, tokens_in=?, tokens_out=?, calls=?, note=? WHERE id=?",
            (n_universe, n_ranked, tokens_in, tokens_out, calls, note, cycle_id))
        self.db.commit()

    def add_trade(self, **kw) -> int:
        cols = [c for c in TRADE_COLS if c in kw]
        q = f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        cur = self.db.execute(q, [kw[c] for c in cols])
        self.db.commit()
        return cur.lastrowid

    def trades(self, market=None, variant=None, proposals_only=False):
        q, args = "SELECT * FROM trades WHERE 1=1", []
        if market:
            q += " AND market=?"
            args.append(market)
        if variant:
            q += " AND variant=?"
            args.append(variant)
        if proposals_only:
            q += " AND entry IS NOT NULL"
        q += " ORDER BY ts, id"
        return [dict(r) for r in self.db.execute(q, args)]

    def buy_trades(self, variant: str, since: float):
        rows = self.db.execute(
            "SELECT * FROM trades WHERE variant=? AND final_action='buy' AND ts>=? ORDER BY ts, id",
            (variant, since))
        return [dict(r) for r in rows]

    def recent_symbols(self, variant: str, since: float) -> set:
        rows = self.db.execute(
            "SELECT DISTINCT symbol FROM trades WHERE variant=? AND ts>=?", (variant, since))
        return {r["symbol"] for r in rows}

    def note_cycle(self, cycle_id: int, note: str):
        self.db.execute("UPDATE cycles SET note=? WHERE id=?", (note[:500], cycle_id))
        self.db.commit()

    def last_cycles(self, n: int = 5):
        return [dict(r) for r in self.db.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (n,))]

    def calls_since(self, since: float) -> int:
        r = self.db.execute("SELECT COALESCE(SUM(calls), 0) AS n FROM cycles WHERE ts>=?", (since,)).fetchone()
        return int(r["n"])

    def usage_totals(self):
        r = self.db.execute(
            "SELECT COUNT(*) AS cycles, COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout, "
            "COALESCE(SUM(calls),0) AS calls FROM cycles").fetchone()
        return dict(r)
