"""Would copying Polymarket's best wallets have paid?

Wallets are picked by ALL-time profit (so the pick does not depend on the test period), then every
BUY they made between 3 and 21 days ago is 'copied' one cent worse and marked at today's or the
final price. Wallets that trade like market makers (too many trades) are reported separately.
"""
import json, os, time, datetime as dt, statistics as st, requests
from concurrent.futures import ThreadPoolExecutor
D = "https://data-api.polymarket.com/v2"; GAMMA = "https://gamma-api.polymarket.com"; OUT = "data/edges"
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"
def get(url, **p):
    for i in range(5):
        try:
            r = S.get(url, params=p, timeout=25)
            if r.status_code == 429: time.sleep(2 + 2 * i); continue
            return r.json() if r.ok else None
        except Exception: time.sleep(1 + i)
def leaders(period, n=40):
    j = get(f"{D}/leaderboard", time_period=period, order_by="PNL", limit=n) or {}
    return j.get("data", [])
def trades(w, since):
    out, cur = [], None
    for _ in range(30):
        p = dict(user=w, limit=500)
        if cur: p["cursor"] = cur
        j = get(f"{D}/trades", **p) or {}
        rows = j.get("data", []); out += rows
        pg = j.get("pagination", {}); cur = pg.get("next_cursor")
        if not rows or not pg.get("has_more") or not cur or float(rows[-1]["timestamp"]) < since: break
    return [t for t in out if float(t["timestamp"]) >= since]
def main():
    os.makedirs(OUT, exist_ok=True); now = time.time()
    lb = None
    for per in ("ALL", "ALL_TIME", "all", "YEAR", "MONTH"):
        lb = leaders(per)
        if lb: print("leaderboard period used:", per, len(lb)); break
    W = {r["user_id"]: r for r in lb}
    with ThreadPoolExecutor(6) as ex: T = dict(zip(W, ex.map(lambda w: trades(w, now - 21 * 86400), W)))
    conds = sorted({t["condition_id"] for ts in T.values() for t in ts if t.get("side") == "BUY"})
    print("wallets", len(W), "trades", sum(len(x) for x in T.values()), "markets", len(conds), flush=True)
    px = {}
    def lookup(chunk):
        g = get(f"{GAMMA}/markets", **{"condition_ids": chunk, "limit": 100}) or []
        for m in g:
            try:
                for tok, p in zip(json.loads(m["clobTokenIds"]), json.loads(m["outcomePrices"])): px[tok] = (float(p), bool(m.get("closed")))
            except Exception: pass
    with ThreadPoolExecutor(6) as ex: list(ex.map(lookup, [conds[i:i + 40] for i in range(0, len(conds), 40)]))
    print("priced tokens", len(px), flush=True)
    rows = []
    for w, ts in T.items():
        buys = [t for t in ts if t.get("side") == "BUY" and now - float(t["timestamp"]) >= 3 * 86400 and t.get("token_id") in px and 0.05 <= float(t["price"]) <= 0.95]
        if len(buys) < 5: continue
        cost = sum(float(t["size"]) * (float(t["price"]) + 0.01) for t in buys)
        val = sum(float(t["size"]) * px[t["token_id"]][0] for t in buys)
        # equal-weight per market (what a small copier would do: same stake on each market they enter)
        per = {}
        for t in buys: per.setdefault(t["condition_id"], []).append(t)
        eq = [px[b[0]["token_id"]][0] / (float(b[0]["price"]) + 0.01) - 1 for b in per.values()]
        rows.append(dict(w=w, name=W[w].get("user_name"), pnl_all=W[w].get("pnl"), trades_21d=len(ts), buys=len(buys), markets=len(per),
                         ret_weighted=val / cost - 1, ret_equal=st.mean(eq), win=sum(1 for x in eq if x > 0) / len(eq),
                         closed_share=sum(1 for b in per.values() if px[b[0]["token_id"]][1]) / len(per)))
    json.dump(rows, open(f"{OUT}/pm_wallets.json", "w"), indent=1)
    slow = [r for r in rows if r["trades_21d"] <= 1500]; fast = [r for r in rows if r["trades_21d"] > 1500]
    L = [f"# Copying Polymarket's top wallets — {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC", "",
         "Top wallets by all-time profit. Each BUY they made 3-21 days ago, copied 1c worse, valued at today's or final price. Equal stake per market.", "",
         f"Wallets tested: {len(rows)}. Human-speed wallets (<=1,500 trades in 21 days): {len(slow)}; bot-speed: {len(fast)}.", ""]
    for name, g in (("Human-speed wallets", slow), ("Bot-speed wallets", fast)):
        if not g: continue
        L += [f"## {name}", "", f"Average copy return per market: {st.mean(r['ret_equal'] for r in g):+.1%}; median wallet: {st.median(r['ret_equal'] for r in g):+.1%}; "
              f"wallets with positive copy return: {sum(1 for r in g if r['ret_equal'] > 0)}/{len(g)}", "",
              "| Wallet | All-time profit $ | Markets entered | Copy return per market | Win rate | Money-weighted return | Share already settled |", "|---|---|---|---|---|---|---|"]
        for r in sorted(g, key=lambda r: -r["ret_equal"]):
            L.append(f"| {r['name'] or r['w'][:10]} | {r['pnl_all']:,.0f} | {r['markets']} | {r['ret_equal']:+.1%} | {r['win']:.0%} | {r['ret_weighted']:+.1%} | {r['closed_share']:.0%} |")
        L.append("")
    open(f"{OUT}/PM_WALLETS.md", "w").write("\n".join(L) + "\n"); print("\n".join(L))
if __name__ == "__main__": main()
