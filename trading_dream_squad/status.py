"""État de santé en 5 secondes : dernier cycle, erreurs, positions, coût.

  python status.py            # crypto et stocks
  python status.py --market crypto
"""
from __future__ import annotations

import argparse
import os
import time

from journal import Journal
from util import fmt_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["crypto", "stocks"], default=None)
    args = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    for m in ([args.market] if args.market else ["crypto", "stocks"]):
        p = os.path.join(here, "data", f"journal_{m}.sqlite")
        if not os.path.exists(p):
            print(f"[{m}] pas de journal.")
            continue
        j = Journal(p)
        cyc = j.last_cycles(1)
        u = j.usage_totals()
        print(f"[{m}] {u['cycles']} cycles, {u['calls']} appels LLM ({u['tin']:,} tokens in / {u['tout']:,} out)")
        if cyc:
            age = (time.time() - cyc[0]["ts"]) / 60
            print(f"   dernier cycle : {fmt_ts(cyc[0]['ts'])} (il y a {age:.0f} min){'  <-- ANORMAL' if m == 'crypto' and age > 60 else ''}")
        errs = [c for c in j.last_cycles(50) if c["note"]]
        if errs:
            print(f"   {len(errs)} cycle(s) avec erreur sur les 50 derniers, dernier : {errs[0]['note'][:150]}")
        rows = j.db.execute("SELECT variant, COUNT(*) n, SUM(final_action='buy') b FROM trades GROUP BY variant").fetchall()
        for r in rows:
            print(f"   {r['variant']}: {r['n']} plans analysés, {r['b'] or 0} achats papier")


if __name__ == "__main__":
    main()
