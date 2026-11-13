"""Cross-venue perp/perp funding spreads on venues usable from Angola.

Long perp on the low-funding venue, short perp on the high-funding venue:
price-neutral, collects the funding difference. Ranks live spreads net of
taker fees, then checks the last 7 days of history for the top candidates.
"""
import json, os, time, datetime as dt, statistics as st, requests
from concurrent.futures import ThreadPoolExecutor

OUT = "data/edges"
FEE = {"mexc": 0.0002, "gate": 0.0005, "bitget": 0.0006, "kucoin": 0.0006, "hl": 0.00045}  # taker per side
HOLD_DAYS = 7
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"

def j(url, method="GET", **kw):
    for i in range(4):
        try:
            r = S.request(method, url, timeout=30, **kw)
            if r.status_code == 429: time.sleep(2 + 2 * i); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            if i == 3: print("ERR", url[:90], e); return None
            time.sleep(1 + i)

# each returns {BASE: (rate_per_hour, price, interval_h, venue_symbol)}
def mexc():
    d = j("https://contract.mexc.com/api/v1/contract/funding_rate") or {}
    px = {t["symbol"]: float(t.get("lastPrice") or 0) for t in (j("https://contract.mexc.com/api/v1/contract/ticker") or {}).get("data", [])}
    out = {}
    for x in d.get("data", []):
        s = x["symbol"]
        if not s.endswith("_USDT"): continue
        h = float(x.get("collectCycle") or 8)
        out[s[:-5]] = (float(x["fundingRate"]) / h, px.get(s, 0), h, s)
    return out

def gate():
    d = j("https://api.gateio.ws/api/v4/futures/usdt/contracts") or []
    out = {}
    for x in d:
        if x.get("in_delisting"): continue
        h = float(x.get("funding_interval") or 28800) / 3600
        out[x["name"][:-5]] = (float(x["funding_rate"]) / h, float(x.get("last_price") or 0), h, x["name"])
    return out

def bitget():
    t = (j("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"}) or {}).get("data", [])
    iv = {}
    cr = (j("https://api.bitget.com/api/v2/mix/market/current-fund-rate", params={"productType": "USDT-FUTURES"}) or {}).get("data", [])
    for x in cr or []:
        try: iv[x["symbol"]] = float(x.get("fundingRateInterval") or 8)
        except Exception: pass
    out = {}
    for x in t:
        s = x["symbol"]
        if not s.endswith("USDT"): continue
        h = iv.get(s, 8.0)
        out[s[:-4]] = (float(x.get("fundingRate") or 0) / h, float(x.get("lastPr") or 0), h, s)
    return out

def kucoin():
    d = (j("https://api-futures.kucoin.com/api/v1/contracts/active") or {}).get("data", [])
    out = {}
    for x in d:
        if x.get("quoteCurrency") != "USDT" or x.get("fundingFeeRate") is None: continue
        h = float(x.get("fundingRateGranularity") or 28800000) / 3.6e6
        base = x["baseCurrency"].replace("XBT", "BTC")
        out[base] = (float(x["fundingFeeRate"]) / h, float(x.get("lastTradePrice") or 0), h, x["symbol"])
    return out

def hl():
    d = j("https://api.hyperliquid.xyz/info", "POST", json={"type": "metaAndAssetCtxs"})
    out = {}
    if not d: return out
    for u, c in zip(d[0]["universe"], d[1]):
        if u.get("isDelisted"): continue
        out[u["name"]] = (float(c["funding"]), float(c.get("markPx") or 0), 1.0, u["name"])
    return out

# history: list of hourly-normalised rates over last 7 days
def hist(venue, sym, h):
    since = time.time() - 7 * 86400
    r = []
    try:
        if venue == "mexc":
            d = j("https://contract.mexc.com/api/v1/contract/funding_rate/history", params={"symbol": sym, "page_size": 200, "page_num": 1})
            r = [(x["settleTime"] / 1000, float(x["fundingRate"])) for x in d["data"]["resultList"]]
        elif venue == "gate":
            d = j("https://api.gateio.ws/api/v4/futures/usdt/funding_rate", params={"contract": sym, "limit": 200})
            r = [(x["t"], float(x["r"])) for x in d]
        elif venue == "bitget":
            d = j("https://api.bitget.com/api/v2/mix/market/history-fund-rate", params={"symbol": sym, "productType": "USDT-FUTURES", "pageSize": 100})
            r = [(int(x["fundingTime"]) / 1000, float(x["fundingRate"])) for x in d["data"]]
        elif venue == "kucoin":
            d = j("https://api-futures.kucoin.com/api/v1/contract/funding-rates", params={"symbol": sym, "from": int(since * 1000), "to": int(time.time() * 1000)})
            r = [(x["timepoint"] / 1000, float(x["fundingRate"])) for x in d["data"]]
        elif venue == "hl":
            d = j("https://api.hyperliquid.xyz/info", "POST", json={"type": "fundingHistory", "coin": sym, "startTime": int(since * 1000)})
            r = [(x["time"] / 1000, float(x["fundingRate"])) for x in d]
    except Exception as e:
        print("hist err", venue, sym, e); return None
    r = [x for x in r if x[0] >= since]
    if not r: return None
    return sum(x[1] for x in r) / (7 * 24)      # mean hourly rate over 7d (sum of payments / hours)

def main():
    os.makedirs(OUT, exist_ok=True)
    fns = {"mexc": mexc, "gate": gate, "bitget": bitget, "kucoin": kucoin, "hl": hl}
    with ThreadPoolExecutor(5) as ex: data = dict(zip(fns, ex.map(lambda f: f(), fns.values())))
    for v, d in data.items(): print(v, len(d))
    bases = {}
    for v, d in data.items():
        for b, x in d.items(): bases.setdefault(b, {})[v] = x
    cands = []
    for b, vs in bases.items():
        if len(vs) < 2: continue
        names = list(vs)
        for a in names:
            for c in names:
                if a == c: continue
                ra, pa = vs[a][0], vs[a][1]; rc, pc = vs[c][0], vs[c][1]
                if pa <= 0 or pc <= 0: continue
                gap = pa / pc - 1
                if abs(gap) > 0.03: continue           # likely different token / broken
                spread_h = ra - rc                     # short a (receives ra), long c (pays rc)
                if spread_h <= 0: continue
                cost = 2 * (FEE[a] + FEE[c])
                net7 = spread_h * 24 * HOLD_DAYS - cost
                cands.append(dict(base=b, short=a, long=c, apr=spread_h * 24 * 365 * 100,
                                  net7=net7, gap=gap, ia=vs[a][2], ic=vs[c][2], sa=vs[a][3], sc=vs[c][3]))
    cands.sort(key=lambda x: -x["net7"])
    best = {}
    for x in cands:
        if x["base"] not in best: best[x["base"]] = x
    top = sorted(best.values(), key=lambda x: -x["net7"])[:30]
    def h7(x):
        hs = hist(x["short"], x["sa"], x["ia"]); hc = hist(x["long"], x["sc"], x["ic"])
        x["apr7"] = None if hs is None or hc is None else (hs - hc) * 24 * 365 * 100
        x["net7_hist"] = None if x["apr7"] is None else x["apr7"] / 100 / 365 * HOLD_DAYS - 2 * (FEE[x["short"]] + FEE[x["long"]])
        return x
    with ThreadPoolExecutor(6) as ex: top = list(ex.map(h7, top))
    now = dt.datetime.now(dt.timezone.utc)
    json.dump(top, open(f"{OUT}/funding_xv.json", "w"), indent=1, default=str)
    L = [f"# Cross-venue perp funding — {now:%Y-%m-%d %H:%M} UTC", "",
         "Short the perp on the high-funding venue, long the same coin's perp on the low one. Price-neutral; earns the funding difference.",
         f"Net = funding over {HOLD_DAYS} days minus taker fees for open+close on both legs. 'Past 7d APR' = what the same pair actually paid over the last week.", "",
         "| Coin | Short on | Long on | Live APR | Past 7d APR | Net 7d (live) | Net 7d (past) | Price gap | Intervals h |",
         "|---|---|---|---|---|---|---|---|---|"]
    for x in top:
        a7 = "n/a" if x["apr7"] is None else f"{x['apr7']:.0f}%"
        n7 = "n/a" if x["net7_hist"] is None else f"{x['net7_hist']:.2%}"
        L.append(f"| {x['base']} | {x['short']} | {x['long']} | {x['apr']:.0f}% | {a7} | {x['net7']:.2%} | {n7} | {x['gap']:+.2%} | {x['ia']:g}/{x['ic']:g} |")
    keep = [x for x in top if x["net7_hist"] and x["net7_hist"] > 0.005 and x["net7"] > 0.005]
    L += ["", f"Both live and past-week net > 0.5%/week: {len(keep)} pairs: " + ", ".join(f"{x['base']} ({x['short']}/{x['long']})" for x in keep)]
    open(f"{OUT}/FUNDING_XV.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))

if __name__ == "__main__":
    main()
