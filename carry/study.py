"""Carry selection study (read-only, public Bybit data).

Question: when rotating a same-exchange spot+perp carry every 30 days, which selection
rule picks coins whose funding holds up over the NEXT 30 days?

Rules compared (all use only data before the rebalance date, no look-ahead):
  f30   trailing 30-day funding (the current paper-trade rule)
  f90   trailing 90-day funding, per 30 days
  min3  the weakest of the last three 30-day blocks (rewards persistence)
  stable  min3, but only coins positive on >= 80% of the last 90 days

Universe: Bybit USDT linear perps whose base coin also has a USDT spot pair.
Costs: spot taker 0.10% + perp taker 0.055%, paid on entry and exit of each changed coin.
Output: data/results.json and RESULTS.md. Funding pulls are cached in data/funding/.
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "funding")
B = "https://api.bybit.com"
DAYS_BACK = 420
K = 3
ROT = 30
FEE_RT = 2 * (0.0010 + 0.00055)  # entry + exit, both legs
DAY_MS = 86_400_000


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def get(session, path, params):
    for attempt in range(6):
        try:
            j = session.get(B + path, params=params, timeout=30).json()
            if j.get("retCode") == 0:
                return j["result"]
            if j.get("retCode") == 10006:
                time.sleep(3 * (attempt + 1))
                continue
            log("API error", path, params, j.get("retCode"), j.get("retMsg"))
            return None
        except Exception as e:  # network or JSON error
            log("retry", path, type(e).__name__)
            time.sleep(2 * (attempt + 1))
    return None


def instruments(session, category):
    out, cursor = [], None
    while True:
        p = {"category": category, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        r = get(session, "/v5/market/instruments-info", p) or {}
        out += r.get("list") or []
        cursor = r.get("nextPageCursor")
        if not cursor:
            return out


def funding_history(session, symbol, since_ms):
    path = os.path.join(CACHE, symbol + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    rows, end = {}, int(time.time() * 1000)
    while True:
        r = get(session, "/v5/market/funding/history",
                {"category": "linear", "symbol": symbol, "endTime": end, "limit": 200}) or {}
        lst = r.get("list") or []
        if not lst:
            break
        for x in lst:
            rows[int(x["fundingRateTimestamp"])] = float(x["fundingRate"])
        oldest = min(int(x["fundingRateTimestamp"]) for x in lst)
        if oldest <= since_ms or len(lst) < 200:
            break
        end = oldest - 1
        time.sleep(0.12)
    data = sorted(rows.items())
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def daily_sums(rows):
    d = defaultdict(float)
    for ts, rate in rows:
        d[ts // DAY_MS] += rate
    return d


def rank_corr(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    if len(xs) < 5:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def features(days, t):
    """days: {day_index: funding sum}. t: rebalance day. Needs 90 days before and 30 after."""
    first, last = min(days), max(days)
    if first > t - 90 or last < t + ROT - 1:
        return None
    blocks = [sum(days.get(d, 0.0) for d in range(t - 30 * (i + 1), t - 30 * i)) for i in range(3)]
    past90 = [days.get(d, 0.0) for d in range(t - 90, t)]
    return {
        "f30": blocks[0],
        "f90": sum(blocks) / 3,
        "min3": min(blocks),
        "pos90": sum(1 for v in past90 if v > 0) / 90,
        "fwd30": sum(days.get(d, 0.0) for d in range(t, t + ROT)),
    }


RULES = {
    "f30": lambda f: f["f30"],
    "f90": lambda f: f["f90"],
    "min3": lambda f: f["min3"],
    "stable": lambda f: f["min3"] if f["pos90"] >= 0.8 else None,
}


def analyse(series):
    """series: {symbol: {day: funding}}. Pure function, testable offline."""
    all_days = [d for s in series.values() for d in s]
    if not all_days:
        return {"periods": 0}
    start, end = min(all_days) + 90, max(all_days) - ROT + 1
    dates = list(range(start, end + 1, ROT))
    out = {r: {"periods": [], "net": 0.0, "gross": 0.0, "fees": 0.0, "pred": 0.0} for r in RULES}
    corr = {k: [] for k in ("f30", "f90", "min3", "pos90")}
    held = {r: set() for r in RULES}
    for t in dates:
        feats = {s: f for s, d in series.items() if (f := features(d, t))}
        if len(feats) < 10:
            continue
        fwd = [f["fwd30"] for f in feats.values()]
        for k in corr:
            c = rank_corr([f[k] for f in feats.values()], fwd)
            if c is not None:
                corr[k].append(c)
        for r, score in RULES.items():
            scored = [(score(f), s) for s, f in feats.items() if score(f) is not None]
            top = [s for v, s in sorted(scored, reverse=True)[:K] if v > 0]
            if not top:
                held[r] = set()
                out[r]["periods"].append({"date": t, "coins": [], "gross": 0.0, "fees": 0.0})
                continue
            gross = sum(feats[s]["fwd30"] for s in top) / K
            pred = sum(feats[s]["f30"] for s in top) / K
            changed = len(set(top) ^ held[r]) / 2
            fees = changed / K * FEE_RT
            held[r] = set(top)
            out[r]["gross"] += gross
            out[r]["fees"] += fees
            out[r]["pred"] += pred
            out[r]["periods"].append({"date": t, "coins": top, "gross": gross, "fees": fees, "pred_f30": pred})
    for r in out:
        n = len(out[r]["periods"])
        out[r]["net"] = out[r]["gross"] - out[r]["fees"]
        out[r]["n"] = n
        out[r]["net_apr"] = out[r]["net"] / n * 365 / ROT if n else None
        out[r]["gross_apr"] = out[r]["gross"] / n * 365 / ROT if n else None
        out[r]["realised_vs_trailing30"] = out[r]["gross"] / out[r]["pred"] if out[r]["pred"] else None
        out[r]["win_periods"] = sum(1 for p in out[r]["periods"] if p["gross"] - p["fees"] > 0)
    return {"dates": len(dates), "rules": out,
            "rank_corr_with_next30": {k: (sum(v) / len(v) if v else None) for k, v in corr.items()},
            "symbols": len(series)}


def report(res):
    day = lambda d: datetime.fromtimestamp(d * 86400, timezone.utc).strftime("%Y-%m-%d")
    L = ["# Carry selection study (Bybit, same-exchange spot + perp)", "",
         f"Symbols: {res['symbols']}. Rebalances: {res['dates']} (every {ROT} days, top {K}, equal weight).",
         "APR is on hedged notional, after taker fees on changed coins. No price, borrow or collateral effects.", "",
         "| Rule | Net APR | Gross APR | Fees (sum) | Realised / trailing-30 | Positive periods |",
         "|---|---|---|---|---|---|"]
    for r, v in res["rules"].items():
        fmt = lambda x: "n/a" if x is None else f"{x:.1%}"
        L.append(f"| {r} | {fmt(v['net_apr'])} | {fmt(v['gross_apr'])} | {v['fees']:.2%} | "
                 f"{fmt(v['realised_vs_trailing30'])} | {v['win_periods']}/{v['n']} |")
    L += ["", "Rank correlation of each feature with the next 30 days' funding (mean over rebalances):", ""]
    for k, v in res["rank_corr_with_next30"].items():
        L.append(f"- {k}: {'n/a' if v is None else f'{v:.2f}'}")
    L += ["", "## Picks per period", ""]
    for r, v in res["rules"].items():
        L.append(f"### {r}")
        for p in v["periods"]:
            L.append(f"- {day(p['date'])}: {', '.join(p['coins']) or '(none)'}  next-30d {p['gross']:.2%}, fees {p['fees']:.2%}")
        L.append("")
    L += ["## Limits", "",
          "- Survivorship: only currently listed perps are fetched; delisted coins (often the worst carry trades) are missing.",
          "- Collateral ratios are today's, not historical; the paper trade's collateral filter is not applied here.",
          "- Funding only: spot/perp basis moves, borrow costs and liquidation risk are not modelled."]
    return "\n".join(L) + "\n"


def main():
    import requests
    os.makedirs(CACHE, exist_ok=True)
    s = requests.Session()
    log("listing instruments")
    lin = [x for x in instruments(s, "linear")
           if x.get("quoteCoin") == "USDT" and x.get("contractType") == "LinearPerpetual" and x.get("status") == "Trading"]
    spot = {x["baseCoin"] for x in instruments(s, "spot") if x.get("quoteCoin") == "USDT"}
    universe = sorted(x["symbol"] for x in lin if x["baseCoin"] in spot)
    log(f"{len(lin)} USDT perps, {len(universe)} with a USDT spot pair")
    since = int(time.time() * 1000) - DAYS_BACK * DAY_MS
    series = {}
    for i, sym in enumerate(universe, 1):
        rows = funding_history(s, sym, since)
        series[sym] = daily_sums([(t, r) for t, r in rows if t >= since])
        if i % 25 == 0:
            log(f"funding {i}/{len(universe)}")
    res = analyse(series)
    with open(os.path.join(DATA, "results.json"), "w") as f:
        json.dump(res, f, indent=1)
    with open(os.path.join(HERE, "RESULTS.md"), "w", encoding="utf-8") as f:
        f.write(report(res))
    log("STUDY_DONE")


if __name__ == "__main__":
    sys.exit(main())
