"""Tests du coeur : simulation papier, indicateurs, parsing, LLM (faux client), et un cycle complet hors ligne.

  python test_core.py
"""
from __future__ import annotations

import json
import os
import tempfile

import paper
from config import get_config
from indicators import atr, features, rsi
from journal import Journal
from llm import LLM, extract_json
from providers import MockProvider, parse_klines, parse_news_rss
from squad import Squad, Variant
from llm import StubLLM

H = 3600


def c(t, o, h, l, cl, v=100.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": cl, "v": v}


def trade(entry=100.0, stop=95.0, target=110.0, price=100.0, ts=0, valid_hours=12):
    return {"entry": entry, "stop": stop, "target": target, "price": price, "ts": ts, "valid_hours": valid_hours}


def approx(a, b, tol=1e-9):
    assert abs(a - b) <= tol, f"{a} != {b}"


# ---------------------------------------------------------------- indicateurs
def test_rsi_atr():
    assert rsi([float(i) for i in range(1, 40)]) == 100.0
    approx(rsi([5.0] * 40), 50.0)
    flat = [c(i * H, 10, 11, 9, 10) for i in range(40)]
    approx(atr(flat), 2.0)
    assert features(flat[:30]) is None


# ---------------------------------------------------------------- simulation
def test_market_target():
    # marché (entrée = prix) : la cible est touchée à la 2e bougie, sans frais -> +2R
    cs = [c(H, 100, 103, 99, 102), c(2 * H, 102, 111, 101, 110)]
    r = paper.simulate(trade(), cs, 10 * H, 0)
    assert r["status"] == "target"
    approx(r["exit_price"], 110.0)
    approx(r["r_final"], 2.0)


def test_stop_first_when_both_touched():
    cs = [c(H, 100, 111, 94, 100)]  # stop ET cible touchés dans la même bougie
    r = paper.simulate(trade(), cs, 10 * H, 0)
    assert r["status"] == "stop"
    approx(r["r_final"], -1.0)


def test_costs_in_r():
    cs = [c(H, 100, 111, 99, 110)]
    r = paper.simulate(trade(), cs, 10 * H, 10)  # 10 bps par côté = 0,2 % aller-retour = 0,2 sur un risque de 5
    approx(r["r_final"], (110 - 100 - 0.2) / 5.0)


def test_limit_fill_then_target():
    t = trade(entry=98.0, stop=94.0, target=106.0, price=100.0)
    cs = [c(H, 100, 101, 99, 100),        # pas rempli (low 99 > 98)
          c(2 * H, 100, 100, 97.5, 99),   # rempli à 98, ni stop ni cible
          c(3 * H, 99, 107, 98, 106)]     # cible
    r = paper.simulate(t, cs, 10 * H, 0)
    assert r["status"] == "target"
    approx(r["fill_price"], 98.0)
    approx(r["r_final"], (106 - 98) / 4.0)
    approx(r["fill_ts"], 2 * H)


def test_limit_expired():
    t = trade(entry=90.0, stop=85.0, target=105.0, price=100.0, valid_hours=2)
    cs = [c(H, 100, 101, 99, 100), c(2 * H, 100, 101, 99, 100), c(3 * H, 100, 101, 99, 100)]
    r = paper.simulate(t, cs, 10 * H, 0)
    assert r["status"] == "expired" and r["fill_price"] is None


def test_pending_when_not_expired_yet():
    t = trade(entry=90.0, stop=85.0, target=105.0, price=100.0, valid_hours=12)
    r = paper.simulate(t, [c(H, 100, 101, 99, 100)], 2 * H, 0)
    assert r["status"] == "pending"


def test_gap_through_limit_and_stop():
    t = trade(entry=98.0, stop=94.0, target=106.0, price=100.0)
    cs = [c(H, 92, 93, 90, 91)]  # ouvre sous l'ordre limite ET sous le stop : rempli à 92, sorti à 92
    r = paper.simulate(t, cs, 10 * H, 0)
    assert r["status"] == "stop"
    approx(r["fill_price"], 92.0)
    approx(r["exit_price"], 92.0)
    approx(r["r_final"], 0.0)  # entré et sorti au même prix : le risque initial (4) ne bouge pas, le gap est subi à l'entrée


def test_horizons_and_open():
    cs = [c((i + 1) * H, 100, 101, 99, 100.5 + i * 0.1) for i in range(30)]  # reste ouvert
    r = paper.simulate(trade(stop=90.0, target=130.0), cs, 40 * H, 0)
    assert r["status"] == "open"
    assert r["r_h"][1] is not None and r["r_h"][24] is not None
    assert r["r_h"][168] is None  # pas encore atteint
    assert r["r_h"][24] > r["r_h"][1]


def test_time_exit():
    cs = [c((i + 1) * H, 100, 101, 99, 100) for i in range(24 * 8)]
    r = paper.simulate(trade(stop=90.0, target=130.0), cs, 10 ** 7, 0, horizon_days=7)
    assert r["status"] == "time"
    approx(r["r_final"], 0.0)


def test_no_future_candle():
    cs = [c(H, 100, 111, 99, 110)]
    r = paper.simulate(trade(), cs, H + 1800, 0)  # la bougie n'est pas encore fermée
    assert r["status"] == "open" and r["r_now"] is None


# ---------------------------------------------------------------- validation
def test_validation():
    cfg = get_config("crypto")
    ok, why, d = paper.validate_proposal({"entry": 100, "stop": 97, "target": 107}, 100, 1.5, cfg)
    assert ok, why
    approx(d["rr"], 2.33, 0.01)
    assert not paper.validate_proposal({"entry": 100, "stop": 101, "target": 107}, 100, 1.5, cfg)[0]
    assert not paper.validate_proposal({"entry": 100, "stop": 97, "target": 103}, 100, 1.5, cfg)[0]    # R:R 1
    assert not paper.validate_proposal({"entry": 100, "stop": 99.9, "target": 107}, 100, 1.5, cfg)[0]  # stop trop serré
    assert not paper.validate_proposal({"entry": 108, "stop": 104, "target": 120}, 100, 1.5, cfg)[0]   # entrée trop loin
    assert not paper.validate_proposal({"entry": None, "stop": 1, "target": 2}, 100, 1.5, cfg)[0]


# ---------------------------------------------------------------- LLM
def test_extract_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Voici: {"a": {"b": 2}} fin') == {"a": {"b": 2}}
    assert extract_json("pas du json") is None
    assert extract_json("[1, 2]") is None


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens, output_tokens = 10, 5


class _Resp:
    def __init__(self, text):
        self.content, self.usage = [_Block(text)], _Usage()


class FakeClient:
    def __init__(self, replies):
        self.replies, self.messages = list(replies), self
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return _Resp(self.replies.pop(0))


def test_llm_retry_and_usage():
    fc = FakeClient(["désolé, voici du texte", '{"verdict": "buy"}'])
    llm = LLM("fake-model", client=fc)
    assert llm.ask_json("skeptic", "sys", "user") == {"verdict": "buy"}
    assert llm.calls == 2 and llm.in_tokens == 20 and llm.out_tokens == 10
    assert "valid JSON" in fc.calls[1]["messages"][0]["content"]
    assert "temperature" not in fc.calls[0]


# ---------------------------------------------------------------- parsing réseau (fixtures)
def test_parse_klines():
    rows = [[0, "1", "2", "0.5", "1.5", "10", 3599999, "15.0", 1, "0", "0", "0"],
            [3600000, "1.5", "2", "1", "1.8", "10", 7199999, "18.0", 1, "0", "0", "0"]]
    out = parse_klines(rows, now=3600 + 1800)  # la 2e bougie n'est pas fermée
    assert len(out) == 1 and out[0]["t"] == 0 and out[0]["v"] == 15.0 and out[0]["c"] == 1.5


def test_parse_news():
    xml = b"""<rss><channel>
    <item><title>Solana jumps on ETF hopes - Reuters</title><source>Reuters</source><pubDate>Sat, 11 Oct 2025 10:00:00 GMT</pubDate></item>
    <item><title>Old story - Blog</title><source>Blog</source><pubDate>Sat, 01 Oct 2025 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    now = 1760180400  # 2025-10-11 11:00 UTC
    out = parse_news_rss(xml, now)
    assert len(out) == 1 and out[0]["title"] == "Solana jumps on ETF hopes" and out[0]["source"] == "Reuters"


# ---------------------------------------------------------------- mock et cycle complet
def test_mock_no_lookahead_and_replay():
    cfg = get_config("mock")
    p = MockProvider(cfg)
    now = p.t0 + 300 * H
    for s in p.universe(now):
        cs = p.candles(s, now)
        assert cs and all(x["t"] + H <= now for x in cs)
        assert all(h["ts"] <= now for h in p.news(s, now))

    path = os.path.join(tempfile.mkdtemp(), "j.sqlite")
    j = Journal(path)
    sq = Squad(p, cfg, j, [Variant("rules", StubLLM(), StubLLM())], say=lambda *_: None)
    for k in range(40):
        sq.run_cycle(p.t0 + 250 * H + k * 3 * H)
    trades = j.trades("mock")
    assert trades, "le rejeu doit produire des plans"
    bought = [t for t in trades if t["final_action"] == "buy"]
    assert all(t["skeptic_verdict"] == "buy" for t in bought)
    # le portefeuille ne dépasse jamais max_open, et un actif n'est pas ré-analysé pendant le cooldown
    last = {}
    for t in trades:
        if t["symbol"] in last:
            assert t["ts"] - last[t["symbol"]] >= cfg.cooldown_hours * H
        last[t["symbol"]] = t["ts"]
    # chaque plan retenu est cohérent
    for t in trades:
        if t["entry"] is not None:
            assert t["stop"] < t["entry"] < t["target"]


class RoleClient:
    """Faux client Anthropic qui répond selon le rôle lu dans le prompt système, pour tester le chemin Claude hors ligne."""

    def __init__(self, skeptic_verdict="buy", garbage_role=None):
        self.messages, self.v, self.garbage_role, self.seen = self, skeptic_verdict, garbage_role, []

    def create(self, **kw):
        sysmsg, user = kw["system"], kw["messages"][0]["content"]
        role = next(r for r in ("SCOUT", "NEWS", "CHART", "SKEPTIC") if f"ROLE: {r}" in sysmsg)
        self.seen.append(role)
        if role == self.garbage_role:
            return _Resp("je ne sais pas")
        data = json.loads(user.split("DATA:\n", 1)[1].split("\n\nGROUP CHAT", 1)[0])
        if role == "SCOUT":
            out = {"picks": [{"symbol": data["pool"][0]["symbol"], "why": "gros mouvement"},
                             {"symbol": "INCONNU", "why": "doit être ignoré"}], "message": "ok"}
        elif role == "NEWS":
            out = {"cause": "x", "cause_type": "bidon", "timing": "after_move", "confidence": 250, "message": "n"}
        elif role == "CHART":
            p, a = data["price"], data["features"]["atr"]
            out = {"action": "long", "entry": p, "stop": p - 1.5 * a, "target": p + 3.5 * a,
                   "valid_hours": 99, "rationale": "r", "message": "m"}
        else:
            out = {"verdict": self.v, "confidence": 70, "top_risks": ["a", "b", "c", "d"], "message": "m"}
        return _Resp("```json\n" + json.dumps(out) + "\n```")


def _run_claude(client, cycles=40):
    cfg = get_config("mock")
    p = MockProvider(cfg)
    j = Journal(os.path.join(tempfile.mkdtemp(), "j.sqlite"))
    llm = LLM("fake", client=client)
    sq = Squad(p, cfg, j, [Variant("claude", llm, llm)], say=lambda *_: None)
    for k in range(cycles):
        sq.run_cycle(p.t0 + 250 * H + k * 3 * H)
    return j, llm, cfg


def test_claude_path_normalises_outputs():
    client = RoleClient("buy")
    j, llm, cfg = _run_claude(client)
    trades = [t for t in j.trades("mock", "claude") if t["entry"] is not None]
    assert trades, "au moins un plan attendu"
    assert all(t["news_type"] == "none_found" and t["news_conf"] == 100 for t in trades)  # valeurs bidon normalisées
    assert all(t["valid_hours"] <= cfg.valid_hours for t in trades)                        # 99 h ramené au plafond
    assert all(len(json.loads(t["skeptic_risks"])) <= 3 for t in trades)
    assert llm.calls > 0 and llm.in_tokens == llm.calls * 10
    assert j.usage_totals()["calls"] == llm.calls  # le coût est bien journalisé par cycle


def test_claude_garbage_skeptic_fails_closed():
    j, llm, cfg = _run_claude(RoleClient("buy", garbage_role="SKEPTIC"))
    trades = [t for t in j.trades("mock", "claude") if t["entry"] is not None]
    assert trades and all(t["skeptic_verdict"] == "skip" and t["final_action"] == "no" for t in trades)


def test_claude_garbage_scout_falls_back_to_numeric_top():
    j, llm, cfg = _run_claude(RoleClient("buy", garbage_role="SCOUT"))
    assert j.trades("mock", "claude"), "le repli sur le tri numérique doit quand même produire des candidats"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passent")
    raise SystemExit(1 if failed else 0)
