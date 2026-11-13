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
        live.append((m, rate, ids[0]))
    print("with live rate:", len(live), "total $/day:", round(sum(r for _, r, _ in live)))
    bk = books([t for _, _, t in live])
    rows = []
    for m, rate, tok in live:
        b = bk.get(tok)
        if not b or not b.get("bids") or not b.get("asks"): continue
        bb = max(float(x["price"]) for x in b["bids"]); ba = min(float(x["price"]) for x in b["asks"])
        mid = (bb + ba) / 2
        v = float(m.get("rewards_max_spread") or 0); minsz = float(m.get("rewards_min_size") or 0)
        if v <= 0 or not (0.10 <= mid <= 0.90): continue      # outside band needs strict two-sided; skip
        q1 = side_score(b["bids"], mid, v, +1); q2 = side_score(b["asks"], mid, v, -1)
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
                         bid=bb, ask=ba))
    rows.sort(key=lambda r: -r["earn"])
    with open(f"{OUT}/pm_rewards.json", "w") as f: json.dump(rows, f, indent=1)
    L = [f"# Polymarket liquidity rewards — {now:%Y-%m-%d %H:%M} UTC", "",
         f"Assumes ${CAPITAL:.0f} resting per market, quoted at {QUOTE_FRAC:.0%} of max spread both sides, "
         "and that competing liquidity stays as it is now. Estimate only; fills are the risk.", "",
         f"Markets with live rewards: {len(live)}; total pool ${sum(r for _, r, _ in live):,.0f}/day", "",
         "| # | Market | Pool $/d | Max spread c | Min size | Mid | Book spread c | Others' score | Est. share | Est. $/day | APR on capital | Ends |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows[:40], 1):
        L.append(f"| {i} | [{r['q']}](https://polymarket.com/market/{r['slug']}) | {r['rate']:.0f} | {r['v']} | {r['minsz']:.0f} | {r['mid']} | {r['spread']} | {r['others']:,} | {r['share']:.1%} | {r['earn']:.2f} | {r['apr']:.0f}% | {r['end']} |")
    top10 = sum(r["earn"] for r in rows[:10])
    L += ["", f"Top 10 combined: ${top10:.2f}/day on ${CAPITAL*10:,.0f} resting."]
    open(f"{OUT}/PM_REWARDS.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L[:20]))

if __name__ == "__main__":
    main()
