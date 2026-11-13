"""Polymarket liquidity-rewards ranking.

For every market with an active reward allocation, estimate what a two-sided
maker quote earns per day, given the competing liquidity already resting
inside the max spread. Score per docs: S = ((v - s)/v)^2 * size, sampled each
minute; share = my Q / (my Q + others' Q); paid daily at midnight UTC.
"""
import json, os, time, datetime as dt, requests

CLOB = "https://clob.polymarket.com"
OUT = "data/edges"
CAPITAL = float(os.environ.get("PM_CAPITAL", "1000"))   # $ resting per market (both sides)
QUOTE_FRAC = 0.5          # quote at half the max spread from mid (fill risk vs score)
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"

def get(url, **kw):
    for i in range(5):
        r = S.get(url, timeout=20, **kw)
        if r.status_code == 429: time.sleep(2 + 2 * i); continue
        r.raise_for_status(); return r.json()
    r.raise_for_status()

def reward_markets():
    out, cur, seen = [], None, set()
    for page in range(60):
        p = {"page_size": 500}
        if cur: p["next_cursor"] = cur
        j = get(f"{CLOB}/rewards/markets/multi", params=p)
        data = j.get("data", j if isinstance(j, list) else [])
        out += data
        cur = j.get("next_cursor") if isinstance(j, dict) else None
        print(f"page {page}: +{len(data)} (total {len(out)}) cursor={cur}", flush=True)
        if not cur or cur in ("LTE=", "") or not data or cur in seen: break
        seen.add(cur)
    return out

def books(tokens):
    res = {}
    for i in range(0, len(tokens), 400):
        chunk = tokens[i:i + 400]
        for k in range(5):
            r = S.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=60)
            if r.status_code == 429: time.sleep(3 + 3 * k); continue
            r.raise_for_status(); break
        for b in r.json(): res[b.get("asset_id")] = b
    return res

def side_score(levels, mid, v, sign):
    q = 0.0
    for l in levels:
        p, sz = float(l["price"]), float(l["size"])
        s = (mid - p) * 100 if sign > 0 else (p - mid) * 100
        if 0 <= s < v: q += ((v - s) / v) ** 2 * sz
    return q

def pick_tokens(m):
    toks = m.get("tokens") or []
    ids = [t.get("token_id") for t in toks if t.get("token_id")]
    if not ids:
        ids = [m.get(k) for k in ("token_id", "asset_id") if m.get(k)]
    return ids

def main():
    os.makedirs(OUT, exist_ok=True)
    mk = reward_markets()
    print("reward markets:", len(mk))
    if mk: print("sample keys:", sorted(mk[0].keys()))
    with open(f"{OUT}/pm_rewards_raw_sample.json", "w") as f: json.dump(mk[:3], f, indent=1)
    now = dt.datetime.now(dt.timezone.utc)
    live = []
    for m in mk:
        cfgs = m.get("rewards_config") or []
        rate = 0.0
        for c in cfgs:
            try:
                end = c.get("end_date")
                if end and dt.datetime.fromisoformat(str(end).replace("Z", "+00:00")).replace(tzinfo=dt.timezone.utc) < now: continue
            except Exception: pass
            rate += float(c.get("rate_per_day") or 0)
        if rate <= 0: continue
        ids = pick_tokens(m)
        if not ids: continue
        live.append((m, rate, ids[0], ids[1] if len(ids) > 1 else None))
    print("with live rate:", len(live), "total $/day:", round(sum(x[1] for x in live)))
    bk = books([t for x in live for t in x[2:] if t])
    rows = []
    mirror_hits = mirror_n = 0
    for m, rate, tok, tno in live:
        b = bk.get(tok); bn = bk.get(tno) if tno else None
        if not b or not b.get("bids") or not b.get("asks"): continue
        bb = max(float(x["price"]) for x in b["bids"]); ba = min(float(x["price"]) for x in b["asks"])
        mid = (bb + ba) / 2
        v = float(m.get("rewards_max_spread") or 0); minsz = float(m.get("rewards_min_size") or 0)
        if v <= 0 or not (0.10 <= mid <= 0.90): continue      # outside band needs strict two-sided; skip
        q1 = side_score(b["bids"], mid, v, +1); q2 = side_score(b["asks"], mid, v, -1)
        if bn:
            # NO bids at q act as YES asks at 1-q; NO asks at q act as YES bids at 1-q.
            nb = [{"price": 1 - float(x["price"]), "size": x["size"]} for x in bn.get("bids", [])]
            na = [{"price": 1 - float(x["price"]), "size": x["size"]} for x in bn.get("asks", [])]
            yes_ask_px = {round(float(x["price"]), 3) for x in b["asks"]}
            mirror_n += 1; mirror_hits += any(round(x["price"], 3) in yes_ask_px for x in nb)
            # The YES book already includes mirrored NO orders (verified 2584/2584), so they are not added again.
        others = min(q1, q2) if min(q1, q2) > 0 else max(q1, q2) / 3
        # my quote: bid YES and bid NO (= ask YES) at QUOTE_FRAC*v from mid; capital ~ shares*(1 - spread)
        s = QUOTE_FRAC * v
        shares = CAPITAL / max(1e-6, 1 - 2 * s / 100)
        shares_side = shares / 1.0
        if shares_side < minsz: continue
        mine = ((v - s) / v) ** 2 * shares_side
        share = mine / (mine + others)
        earn = rate * share
        rows.append(dict(q=(m.get("question") or m.get("market_slug") or "")[:90],
                         slug=m.get("market_slug") or m.get("slug") or "", cid=m.get("condition_id"),
                         rate=rate, v=v, minsz=minsz, mid=round(mid, 3), spread=round((ba - bb) * 100, 1),
                         others=round(others), share=round(share, 3), earn=round(earn, 2),
                         apr=round(earn * 365 / CAPITAL * 100, 1),
                         end=str(m.get("end_date") or m.get("end_date_iso") or "")[:10],
                         bid=bb, ask=ba, tok=tok, tno=tno, tick=float(b.get("tick_size") or 0.01)))
    rows.sort(key=lambda r: -r["earn"])
    print(f"mirror check: NO-book bid prices seen as YES asks in {mirror_hits}/{mirror_n} markets")
    # status check on Gamma for the top candidates: must be open, accepting orders, end in the future
    top = rows[:300]; st = {}
    for i in range(0, len(top), 50):
        ids = [r["cid"] for r in top[i:i + 50]]
        try:
            g = get("https://gamma-api.polymarket.com/markets", params=[("condition_ids", c) for c in ids] + [("limit", 100)])
            for x in g: st[x.get("conditionId")] = x
        except Exception as e: print("gamma err", e)
    keep = []
    for r in rows:
        g = st.get(r["cid"])
        if r in top and g is None: r["status"] = "unknown"
        if g:
            r["status"] = ("closed" if g.get("closed") else "open") + ("" if g.get("acceptingOrders", True) else "/not-accepting")
            r["vol24"] = round(float(g.get("volume24hr") or 0)); r["liq"] = round(float(g.get("liquidityNum") or 0))
            r["end"] = str(g.get("endDate") or r["end"])[:10]
        r["empty_book"] = r["spread"] > 2 * r["v"]
        ok = r.get("status") == "open" and r["end"] >= now.strftime("%Y-%m-%d")
        if ok: keep.append(r)
    print("after status filter:", len(keep), "of", len(rows))
    competitive = [r for r in keep if not r["empty_book"]]
    empty = [r for r in keep if r["empty_book"]]
    json.dump(rows, open(f"{OUT}/pm_rewards.json", "w"), indent=1)
    hdr = ["| # | Market | Pool $/d | Max spread c | Min size | Mid | Book spread c | 24h vol $ | Others' score | Est. share | Est. $/day per $%d | Ends |" % CAPITAL,
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    def tab(rs, n):
        out = list(hdr)
        for i, r in enumerate(rs[:n], 1):
            out.append(f"| {i} | [{r['q']}](https://polymarket.com/market/{r['slug']}) | {r['rate']:.0f} | {r['v']} | {r['minsz']:.0f} | {r['mid']} | {r['spread']} | {r.get('vol24','?')} | {r['others']:,} | {r['share']:.1%} | {r['earn']:.2f} | {r['end']} |")
        return out
    L = [f"# Polymarket liquidity rewards — {now:%Y-%m-%d %H:%M} UTC", "",
         f"Assumes ${CAPITAL:.0f} resting per market, quoted at {QUOTE_FRAC:.0%} of max spread on both sides, and that competing liquidity stays as it is now. Only open markets accepting orders with a future end date.", "",
         f"Markets with live rewards: {len(live)}; pool ${sum(x[1] for x in live):,.0f}/day. Open and scored: {len(keep)}.", "",
         "## A. Competitive books (real two-sided market already exists)", ""] + tab(competitive, 30) + [
         "", f"Top 10 A combined: ${sum(r['earn'] for r in competitive[:10]):.2f}/day on ${CAPITAL*10:,.0f}.", "",
         "## B. Empty books (no maker inside the max spread; you would set the price yourself, highest pick-off risk)", ""] + tab(empty, 20)
    open(f"{OUT}/PM_REWARDS.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L[:45]))

if __name__ == "__main__":
    main()
