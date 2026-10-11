"""Lance l'escouade : une fois, en boucle 24/7, ou en rejeu sur le marché synthétique.

Exemples :
  python run.py --market mock --dry-run --replay 40 --step-hours 3 --fresh   # démo sans clé ni réseau
  python run.py --market crypto --once                                      # un cycle réel avec Claude
  python run.py --market stocks                                             # boucle (heures de bourse US)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

from config import get_config
from journal import Journal
from llm import LLM, StubLLM
from providers import make_provider
from squad import Squad, Variant


def build_variants(args):
    if args.dry_run:
        return [Variant("rules", StubLLM(), StubLLM())]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY absente. Exporte-la, ou utilise --dry-run pour tester sans LLM.")
    fast = LLM(os.environ.get("SQUAD_MODEL", "claude-haiku-5-5"))
    skeptic = LLM(os.environ.get("SQUAD_SKEPTIC_MODEL", "claude-sonnet-5-5"))
    variants = [Variant("claude", fast, skeptic)]
    if not args.no_baseline:
        variants.append(Variant("rules", StubLLM(), StubLLM()))
    return variants


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["crypto", "stocks", "mock"], required=True)
    ap.add_argument("--once", action="store_true", help="un seul cycle puis stop")
    ap.add_argument("--dry-run", action="store_true", help="aucun appel LLM : seule la règle bête tourne")
    ap.add_argument("--no-baseline", action="store_true", help="ne pas faire tourner la règle bête à côté de Claude")
    ap.add_argument("--replay", type=int, default=0, help="mock uniquement : nombre de cycles rejoués")
    ap.add_argument("--step-hours", type=int, default=1, help="mock uniquement : heures entre deux cycles rejoués")
    ap.add_argument("--fresh", action="store_true", help="repart d'un journal vide")
    ap.add_argument("--lang", choices=["fr", "en"], default="fr")
    ap.add_argument("--db", default=None, help="chemin du journal SQLite")
    ap.add_argument("--max-calls-day", type=int, default=int(os.environ.get("SQUAD_MAX_CALLS_DAY", "1500")),
                    help="plafond d'appels LLM par 24 h (garde-fou de coût)")
    ap.add_argument("--ignore-hours", action="store_true", help="actions : tourne même marché fermé")
    args = ap.parse_args()

    cfg = get_config(args.market)
    db_path = args.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"journal_{args.market}.sqlite")
    if args.fresh and os.path.exists(db_path):
        os.remove(db_path)
    journal = Journal(db_path)
    provider = make_provider(args.market, cfg)
    squad = Squad(provider, cfg, journal, build_variants(args), lang=args.lang)

    if args.replay:
        if args.market != "mock":
            sys.exit("--replay n'existe que pour --market mock (l'historique des news réelles n'est pas rejouable).")
        start = provider.t0 + 250 * 3600
        end = start + args.replay * args.step_hours * 3600
        if end > provider.eval_now() - 10 * 24 * 3600:
            sys.exit("Rejeu trop long : il faut laisser ~10 jours de bougies futures pour l'évaluation.")
        for k in range(args.replay):
            squad.run_cycle(start + k * args.step_hours * 3600)
        print(f"\nRejeu terminé. Journal : {db_path}\nÉvaluation : python evaluate.py --market mock")
        return

    interval = cfg.cycle_minutes * 60
    while True:
        now = int(time.time())
        if not args.ignore_hours and not provider.is_open(now):
            print(f"[{time.strftime('%H:%M:%S')}] marché fermé, je dors 10 minutes.")
            time.sleep(600)
            continue
        if journal.calls_since(now - 86400) >= args.max_calls_day:
            print(f"Plafond de {args.max_calls_day} appels LLM / 24 h atteint, pause d'une heure.")
            time.sleep(3600)
            continue
        try:
            squad.run_cycle(now)
        except Exception:
            traceback.print_exc()  # on journalise et on continue : la boucle doit survivre aux pannes réseau
        if args.once:
            return
        time.sleep(max(30, interval - (int(time.time()) % interval)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
