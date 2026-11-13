"""Does MEXC same-exchange carry survive REAL trading costs, and which holding rule works best?

1. Measure today's round-trip cost per coin from live books: buy spot at ask / sell at bid,
   short perp at bid / cover at ask, for a $167 leg, plus taker fees (spot 0.10%, perp 0.055%).
2. Re-run 14 months of monthly decisions (same data as the MEXC study) charging that cost every
   time a coin enters and leaves the portfolio. Variants:
     f30-r30   top 3 by trailing-30d funding, rebuilt every 30 days (the paper-trade rule)
     f30-r60 / f30-r90   same, rebuilt every 60 / 90 days (funding still accrues monthly)
     f30-hold10  every 30 days keep a holding while it is still top-10, refill from the top
   Universe: liquidity-filtered (24h spot >= $100k, perp >= $1M) AND unfiltered, for comparison.
Caveat: today's spreads are applied to the past, and only currently listed coins are included.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mexc_paper as P  # noqa: E402
import study  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "data", "carry")
LEG = 1000 * 0.5 / 3
FEES_RT = 2 * (0.0010 + 0.00055)


def roundtrip_cost(args):
    api, sym, ssym, tk = args
    asks, bids = api.spot_book(ssym)
    q, buy = P.walk(asks, usd=LEG)
    _, sell = P.walk(bids, qty=q)
    t = tk.get(sym) or {}
    pb, pa = float(t.get("bid1") or 0), float(t.get("ask1") or 0)
    if not (q and buy and sell and pb and pa) or q * buy < LEG * 0.95:
        return sym, None
    mid = (buy + sell) / 2
    spread = (buy - sell) / mid + (pa - pb) / ((pa + pb) / 2)
    return sym, spread + FEES_RT


def simulate(series, cost, uni, rule, k=3):
    all_days = [d for s in series.values() for d in s]
    start, end = min(all_days) + 30, max(all_days) - 29
    dates = list(range(start, end + 1, 30))
    held, gross, costs, periods, last_rebuild = [], 0.0, 0.0, 0, None
    for t in dates:
        f30 = {}
        for s, d in series.items():
            if s in uni and s in cost and min(d) <= t - 30 and max(d) >= t + 29:
                f30[s] = sum(d.get(x, 0.0) for x in range(t - 30, t))
        if len(f30) < 10:
            continue
        rank = [s for s, v in sorted(f30.items(), key=lambda kv: -kv[1]) if v > 0]
        every = {"f30-r30": 30, "f30-r60": 60, "f30-r90": 90}.get(rule, 30)
        rebuild = last_rebuild is None or t - last_rebuild >= every
        if rule == "f30-hold10":
            keep = [s for s in held if s in rank[:10]]
            new = keep + [s for s in rank if s not in keep][: k - len(keep)]
        elif rebuild:
            new = rank[:k]
        else:
            new = [s for s in held if s in series and min(series[s]) <= t and max(series[s]) >= t + 29]
        if rebuild:
            last_rebuild = t
        for s in set(held) ^ set(new):
            costs += cost[s] / 2 / k        # half the round trip on entry, half on exit
        held = new
        if held:
            gross += sum(sum(series[s].get(x, 0.0) for x in range(t, t + 30)) for s in held) / k
        periods += 1
    net = gross - costs
    return {"periods": periods, "gross_apr": gross / periods * 365 / 30 if periods else None,
            "cost_apr": costs / periods * 365 / 30 if periods else None,
            "net_apr": net / periods * 365 / 30 if periods else None}


def main():
    os.makedirs(OUT, exist_ok=True)
    api = P.Api()
    perps = api.perps(); spot = api.spot_symbols(); vol = api.spot_24h(); tk = api.perp_tickers()
    pairs = [(p["symbol"], spot[p["baseCoin"]]) for p in perps if p.get("baseCoin") in spot]
    liquid = {s for s, ss in pairs if vol.get(ss, 0) >= P.CFG["MIN_SPOT_VOL"]
              and float((tk.get(s) or {}).get("amount24") or 0) >= P.CFG["MIN_PERP_TURNOVER"]}
    print(f"{len(pairs)} spot+perp pairs, {len(liquid)} liquid", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        cost = {s: c for s, c in ex.map(roundtrip_cost, [(api, s, ss, tk) for s, ss in pairs]) if c is not None}
    lc = sorted(cost[s] for s in cost if s in liquid); ac = sorted(cost.values())
    print(f"round-trip cost incl. fees: liquid median {lc[len(lc)//2]:.2%} (n={len(lc)}), all median {ac[len(ac)//2]:.2%} (n={len(ac)})", flush=True)
    cut = int(time.time() * 1000) - 420 * 86_400_000

    def fund(sym):
        return sym, [(t, r) for t, r in api.funding(sym, cut)]
    series = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sym, rows in ex.map(fund, [s for s, _ in pairs if s in cost]):
            if rows:
                series[sym] = study.daily_sums(rows)
    res = {"cost_median_liquid": lc[len(lc)//2], "cost_median_all": ac[len(ac)//2], "rules": {}}
    for uni_name, uni in (("liquid", liquid), ("all", set(cost))):
        for rule in ("f30-r30", "f30-r60", "f30-r90", "f30-hold10"):
            r = simulate(series, cost, uni, rule)
            res["rules"][f"{uni_name}/{rule}"] = r
            print(f"{uni_name:6} {rule:10} periods {r['periods']}  gross {r['gross_apr']:.1%}  costs {r['cost_apr']:.1%}  NET {r['net_apr']:.1%}  (on hedged notional)", flush=True)
    json.dump(res, open(os.path.join(OUT, "mexc_rules.json"), "w"), indent=1)
    print("RULES_DONE", flush=True)


if __name__ == "__main__":
    main()
