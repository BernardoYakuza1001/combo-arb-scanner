"""Polymarket 'near-certain outcome' study.

Part A (history): for markets that closed in the last N days, look at the price K days
before the scheduled end date. If one side was >= 90c, record whether it won and how long
the money was tied up. Gives the realised return of buying favourites, by price bucket.

Part B (now): open markets ending within 14 days whose favourite costs 93-99.5c,
with the yield if it wins and the size available.
"""
import json, os, time, datetime as dt, statistics as st, requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"; CLOB = "https://clob.polymarket.com"
OUT = "data/edges"
DAYS = int(os.environ.get("BOND_DAYS", "75")); MAXM = int(os.environ.get("BOND_MAX", "6000"))
MIN_VOL = float(os.environ.get("BOND_MIN_VOL", "20000"))
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"

def get(url, **kw):
    for i in range(5):
        try:
            r = S.get(url, timeout=25, **kw)
            if r.status_code == 429: time.sleep(2 + 2 * i); continue
            if r.status_code >= 400: return None
            return r.json()
        except Exception:
            time.sleep(1 + i)
    return None

def ts(x):
    if not x: return None
    t = str(x).strip().replace(" ", "T").replace("Z", "+00:00")
    if t.endswith("+00"): t += ":00"
    try:
        d = dt.datetime.fromisoformat(t)
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    except Exception: return None

def closed_markets():
    now = dt.datetime.now(dt.timezone.utc); out = []
    lo = (now - dt.timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"); hi = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for off in range(0, 60000, 500):
        d = get(f"{GAMMA}/markets", params={"closed": "true", "end_date_min": lo, "end_date_max": hi, "limit": 500, "offset": off,
                                            "order": "volumeNum", "ascending": "false"})
        if not d: break
        out += d
        print("closed page", off, len(d), "min vol", round(float(d[-1].get("volumeNum") or 0)), flush=True)
        if len(d) < 500 or float(d[-1].get("volumeNum") or 0) < MIN_VOL or len(out) >= MAXM: break
    return [m for m in out if float(m.get("volumeNum") or 0) >= MIN_VOL][:MAXM]

def study(m):
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]"); res = json.loads(m.get("outcomePrices") or "[]")
        if len(toks) != 2 or len(res) != 2: return None
        r0 = float(res[0])
        if r0 not in (0.0, 1.0): return None                     # 50/50 or unresolved: skip
        end = ts(m.get("endDate")); closed = ts(m.get("closedTime")) or end
        if not end or not closed: return None
        h = get(f"{CLOB}/prices-history", params={"market": toks[0], "startTs": int(end - 8 * 86400), "endTs": int(max(end, closed)), "fidelity": 60})
        pts = [(float(x["t"]), float(x["p"])) for x in (h or {}).get("history", [])]
        if len(pts) < 5: return None
        out = []
        for k in (7, 3, 1):
            t_in = end - k * 86400
            if t_in >= closed - 3600: continue                    # already (nearly) closed at entry time
            before = [p for t, p in pts if t <= t_in]
            if not before: continue
            p = before[-1]
            if abs(t_in - max(t for t, _ in pts if t <= t_in)) > 6 * 3600: continue   # stale price
            fav = max(p, 1 - p)
            if fav < 0.90 or fav > 0.995: continue
            won = (r0 == 1.0) == (p >= 0.5)
            out.append(dict(k=k, fav=fav, won=won, hold_d=(closed - t_in) / 86400,
                            sport=bool(m.get("sportsMarketType") or m.get("gameStartTime")), vol=float(m.get("volumeNum") or 0),
                            q=(m.get("question") or "")[:80]))
        return out
    except Exception:
        return None

def part_a():
    mk = closed_markets(); print("closed markets with volume:", len(mk), flush=True)
    rows = []
    with ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(study, mk)):
            if r: rows += r
            if i % 500 == 0: print("studied", i, "rows", len(rows), flush=True)
    json.dump(rows, open(f"{OUT}/pm_bonds_hist.json", "w"))
    L = ["## A. What buying favourites actually paid (closed markets, last %d days, volume >= $%s)" % (DAYS, f"{MIN_VOL:,.0f}"), "",
         "Entry = price K days before the scheduled end date. Return per trade = 1/price - 1 if the favourite won, -100% if it lost. No fees assumed (most markets have none).", "",
         "| Days before end | Price bucket | Type | Trades | Won | Avg price | Avg return per trade | Avg days held | Annualised | Worst case seen |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k in (7, 3, 1):
        for lo, hi in ((0.90, 0.95), (0.95, 0.97), (0.97, 0.99), (0.99, 0.996)):
            for sport in (False, True):
                g = [r for r in rows if r["k"] == k and lo <= r["fav"] < hi and r["sport"] == sport]
                if len(g) < 15: continue
                rets = [(1 / r["fav"] - 1) if r["won"] else -1.0 for r in g]
                avg = st.mean(rets); hold = st.mean(r["hold_d"] for r in g)
                L.append(f"| {k} | {lo:.2f}-{hi:.3f} | {'sports' if sport else 'other'} | {len(g)} | {sum(r['won'] for r in g)/len(g):.1%} | {st.mean(r['fav'] for r in g):.3f} | "
                         f"{avg:+.2%} | {hold:.1f} | {avg/max(hold,0.2)*365:+.0%} | {sum(1 for r in g if not r['won'])} losses of -100% |")
    losers = sorted([r for r in rows if not r["won"] and r["fav"] >= 0.95], key=lambda r: -r["fav"])[:12]
    L += ["", "Favourites at 95c+ that lost (examples): " + "; ".join(f"{r['q']} ({r['fav']:.2f}, {r['k']}d)" for r in losers)]
    return L

def part_b():
    now = dt.datetime.now(dt.timezone.utc); rows = []
    hi = (now + dt.timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"); lo = (now + dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for off in range(0, 20000, 500):
        d = get(f"{GAMMA}/markets", params={"closed": "false", "active": "true", "end_date_min": lo, "end_date_max": hi, "limit": 500, "offset": off})
        if not d: break
        for m in d:
            try:
                bb = float(m.get("bestBid") or 0); ba = float(m.get("bestAsk") or 0)
                if not m.get("acceptingOrders", True) or bb <= 0 or ba <= 0: continue
                for side, cost in (("YES", ba), ("NO", 1 - bb)):
                    if 0.93 <= cost <= 0.995:
                        days = max(0.25, (ts(m.get("endDate")) - now.timestamp()) / 86400)
                        rows.append(dict(q=(m.get("question") or "")[:85], slug=m.get("slug"), side=side, cost=cost, days=days,
                                         y=1 / cost - 1, ann=(1 / cost - 1) / days * 365, liq=float(m.get("liquidityNum") or 0),
                                         vol24=float(m.get("volume24hr") or 0), sport=bool(m.get("sportsMarketType") or m.get("gameStartTime"))))
            except Exception: pass
        if len(d) < 500: break
    rows = [r for r in rows if r["liq"] >= 5000]
    rows.sort(key=lambda r: -r["ann"])
    json.dump(rows, open(f"{OUT}/pm_bonds_now.json", "w"), indent=1)
    L = ["", f"## B. Open now: favourites at 93-99.5c ending within 14 days (liquidity >= $5,000): {len(rows)}", "",
         "| Market | Buy | Cost | Gain if it wins | Days to end | Annualised | Liquidity $ | 24h vol $ | Type |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:40]:
        L.append(f"| [{r['q']}](https://polymarket.com/market/{r['slug']}) | {r['side']} | {r['cost']:.3f} | {r['y']:+.2%} | {r['days']:.1f} | {r['ann']:.0%} | {r['liq']:,.0f} | {r['vol24']:,.0f} | {'sports' if r['sport'] else 'other'} |")
    return L

def main():
    os.makedirs(OUT, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    L = [f"# Polymarket near-certain outcomes — {now:%Y-%m-%d %H:%M} UTC", ""] + part_a() + part_b()
    open(f"{OUT}/PM_BONDS.md", "w").write("\n".join(L) + "\n"); print("\n".join(L[:45]))

if __name__ == "__main__":
    main()
