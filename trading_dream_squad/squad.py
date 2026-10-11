"""Orchestration d'un cycle : scout -> news -> chart -> skeptic -> desk.

Le desk n'est PAS un LLM : ce sont des règles dures en code (positions max, plan valide, doublons).
Un LLM ne doit jamais avoir le dernier mot sur le risque.

Deux variantes peuvent tourner sur exactement les mêmes données : "claude" (agents LLM) et "rules"
(règle bête sans LLM). C'est la comparaison entre les deux qui dit si le LLM apporte quelque chose.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import agents
import paper
from indicators import features
from util import fmt_ts


@dataclass
class Variant:
    name: str      # "claude" ou "rules"
    fast: object   # scout, news, chart
    skeptic: object


class Squad:
    def __init__(self, provider, cfg, journal, variants, lang: str = "fr", say=print):
        self.provider, self.cfg, self.journal = provider, cfg, journal
        self.variants, self.lang, self.say = variants, lang, say
        self.candles = {}
        self.errors = []
        self._closed = {}  # (variant, trade_id) -> simulation finale

    def _usage(self):
        llms = {}
        for v in self.variants:
            llms[id(v.fast)], llms[id(v.skeptic)] = v.fast, v.skeptic
        g = lambda a: sum(getattr(l, a, 0) for l in llms.values())
        return g("in_tokens"), g("out_tokens"), g("calls")

    def scan(self, now: int):
        syms = self.provider.universe(now)
        self.provider.prefetch(syms, now)
        ranked, self.candles = [], {}
        for s in syms:
            try:
                cs = self.provider.candles(s, now, 200)
            except Exception as e:
                self.say(f"  ! données {s}: {e}")
                continue
            f = features(cs)
            if not f:
                continue
            f["symbol"] = s
            f["score"] = round(abs(f["z4"]) + 0.5 * max(0.0, f["vol_ratio"] - 1.0), 2)
            ranked.append(f)
            self.candles[s] = cs
        ranked.sort(key=lambda f: f["score"], reverse=True)
        return len(syms), ranked

    def open_positions(self, variant: str, now: int) -> list:
        since = now - (self.cfg.horizon_days + 2) * 86400
        out = []
        for t in self.journal.buy_trades(variant, since):
            key = (variant, t["id"])
            sim = self._closed.get(key)
            if sim is None:
                try:
                    cs = self.provider.candles_between(t["symbol"], t["ts"], now)
                except Exception as e:
                    self.say(f"  ! position {t['symbol']}: {e}")
                    continue
                sim = paper.simulate(t, cs, now, self.cfg.cost_bps, self.cfg.horizon_days)
                if sim["status"] in ("stop", "target", "time", "expired", "invalid"):
                    self._closed[key] = sim
            if sim["status"] in ("open", "pending"):
                out.append({"symbol": t["symbol"], "entry": t["entry"], "stop": t["stop"],
                            "target": t["target"], "status": sim["status"],
                            "r_now": None if sim["r_now"] is None else round(sim["r_now"], 2)})
        return out

    def run_cycle(self, now: int) -> dict:
        cyc = self.journal.new_cycle(now, self.cfg.name)
        u0 = self._usage()
        self.say(f"\n=== cycle {fmt_ts(now)} | {self.cfg.name} ===")
        n_uni, ranked = self.scan(now)
        top = [f for f in ranked if f["score"] >= self.cfg.min_score]
        self.say(f"{n_uni} actifs scannés, {len(top)} au-dessus du seuil ({self.cfg.min_score})")
        made = 0
        self.errors = []
        for v in self.variants:
            made += self.run_variant(v, now, top, cyc)
        u1 = self._usage()
        self.journal.end_cycle(cyc, n_uni, len(top), u1[0] - u0[0], u1[1] - u0[1], u1[2] - u0[2],
                               note="; ".join(self.errors))
        return {"cycle": cyc, "scanned": n_uni, "above_threshold": len(top), "plans": made}

    def run_variant(self, v: Variant, now: int, top: list, cyc: int) -> int:
        open_pos = self.open_positions(v.name, now)
        busy = {p["symbol"] for p in open_pos}
        cool = self.journal.recent_symbols(v.name, now - self.cfg.cooldown_hours * 3600)
        pool = [f for f in top if f["symbol"] not in busy and f["symbol"] not in cool][: self.cfg.pool_size]
        self.say(f"\n--- {v.name} | {len(open_pos)} position(s) ouverte(s), {len(pool)} candidat(s) ---")
        if not pool:
            return 0
        sc = agents.scout(v.fast, pool, open_pos, self.cfg, self.lang, now)
        self.say(f"[SCOUT] {sc['message']}")
        by_sym = {f["symbol"]: f for f in pool}
        n = 0
        for pick in sc["picks"]:
            try:
                self.run_candidate(v, by_sym[pick["symbol"]], pick["why"], open_pos, now, cyc)
                n += 1
            except Exception as e:  # une panne sur un actif ne doit pas arrêter la boucle 24/7
                self.say(f"  ! {pick['symbol']}: {type(e).__name__}: {e}")
                self.errors.append(f"{v.name}/{pick['symbol']}: {type(e).__name__}")
        return n

    def run_candidate(self, v: Variant, f: dict, why: str, open_pos: list, now: int, cyc: int):
        sym, cfg = f["symbol"], self.cfg
        self.say(f"\n# {sym}  {f['price']}")
        chat = agents.Chat(sym, self.say)
        chat.post("SCOUT", f"{f['chg_4h']:+.1f} % sur 4h, z={f['z4']}, volume x{f['vol_ratio']}. {why}")

        try:
            heads = [{"i": i, "title": h["title"], "source": h["source"], "age_h": round((now - h["ts"]) / 3600, 1)}
                     for i, h in enumerate(self.provider.news(sym, now))]
        except Exception as e:
            heads = []
            self.say(f"  ! news {sym}: {e}")
        nw = agents.news(v.fast, f, heads, chat, cfg, self.lang, now)
        chat.post("NEWS", nw["message"])

        plan = agents.chart(v.fast, f, self.candles[sym], nw, chat, cfg, self.lang, now)
        chat.post("CHART", plan["message"])

        rec = dict(cycle_id=cyc, ts=now, market=cfg.name, variant=v.name, symbol=sym, price=f["price"],
                   news_cause=nw["cause"], news_type=nw["cause_type"], news_timing=nw["timing"],
                   news_conf=nw["confidence"], chart_action=plan["action"], chart_rationale=plan["rationale"],
                   features=json.dumps(f), final_action="no")

        def finish(reason: str):
            chat.post("DESK", reason)
            rec["final_reason"], rec["transcript"] = reason, chat.text()
            self.journal.add_trade(**rec)

        if plan["action"] != "long":
            return finish("Pas de plan, rien à trader.")
        ok, why_not, derived = paper.validate_proposal(plan, f["price"], f["atr"], cfg)
        if not ok:
            return finish(f"Plan rejeté par les contrôles : {why_not}.")
        rec.update(entry=plan["entry"], stop=plan["stop"], target=plan["target"],
                   valid_hours=plan["valid_hours"], risk_pct=derived["risk_pct"], rr=derived["rr"])

        sk = agents.skeptic(v.skeptic, f, plan, derived, nw, open_pos, chat, cfg, self.lang, now)
        chat.post("SKEPTIC", f"{sk['verdict'].upper()} ({sk['confidence']}) {sk['message']}")
        rec.update(skeptic_verdict=sk["verdict"], skeptic_conf=sk["confidence"],
                   skeptic_risks=json.dumps(sk["top_risks"], ensure_ascii=False), skeptic_msg=sk["message"])

        if sk["verdict"] != "buy":
            return finish(f"Pas d'achat (skeptic : {sk['verdict']}). Plan gardé en contrefactuel pour l'évaluation.")
        if len(open_pos) >= cfg.max_open:
            return finish(f"Portefeuille plein ({cfg.max_open} positions).")
        rec["final_action"] = "buy"
        size = cfg.equity * cfg.risk_pct / (plan["entry"] - plan["stop"]) * plan["entry"]
        open_pos.append({"symbol": sym, "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
                         "status": "pending", "r_now": None})
        finish(f"ACHAT papier : entrée {plan['entry']}, stop {plan['stop']}, cible {plan['target']} "
               f"(R:R {derived['rr']}, risque {cfg.risk_pct:.0%} du capital, taille ~{size:,.0f} $).")
