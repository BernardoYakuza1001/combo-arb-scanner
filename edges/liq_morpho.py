"""Who wins liquidations, and how much do they make? (Morpho Blue, all chains, from Morpho's public API)

For every liquidation in the last N days: chain, market, repaid and seized value in USD, the bonus
(seized - repaid), bad debt, and the liquidator address. Then per chain: how many liquidations, how
much bonus in total, how concentrated the winners are, and how many were small enough that the big
bots might ignore them. A liquidation needs no capital of your own (flash loan), but you must be first.
"""
import json, os, time, collections, datetime as dt, requests

API = "https://blue-api.morpho.org/graphql"
OUT = "data/edges"
DAYS = int(os.environ.get("LIQ_DAYS", "30"))
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"

Q = """
query($skip:Int!,$since:Int!,$until:Int!){
  marketTransactions(first:1000, skip:$skip, orderBy: Timestamp, orderDirection: Desc,
                     where:{type_in:[Liquidation], timestamp_gte:$since, timestamp_lte:$until}){
    pageInfo{countTotal count}
    items{
      txHash timestamp blockNumber chain{ id network }
      user{ address }
      market{ marketId lltv loanAsset{ symbol decimals PRICE } collateralAsset{ symbol decimals PRICE } }
      data{ ... on MarketTransactionLiquidationData{ liquidator seizedAssets repaidAssets badDebtAssets } }
    }
  }
}"""

PRICE_FORMS = ["price{ usd }", "price"]

def gql(query, variables):
    for i in range(5):
        r = S.post(API, json={"query": query, "variables": variables}, timeout=60)
        if r.status_code == 429: time.sleep(3 + 3 * i); continue
        j = r.json()
        if j.get("errors"): raise RuntimeError(json.dumps(j["errors"])[:800])
        return j["data"]
    raise RuntimeError("rate limited")

def main():
    os.makedirs(OUT, exist_ok=True)
    since = int(time.time() - DAYS * 86400)
    rows = []
    for k in range(DAYS):                         # one-day windows: the API caps skip at 10,000
        lo, hi = since + k * 86400, since + (k + 1) * 86400 - 1
        skip = 0
        while True:
            d = None
            for pf in PRICE_FORMS:
                try: d = gql(Q.replace("PRICE", pf), {"skip": skip, "since": lo, "until": hi})["marketTransactions"]; PRICE_FORMS[:] = [pf]; break
                except RuntimeError as e: print("price form", pf, "failed:", str(e)[:200])
            if d is None: raise RuntimeError("no price form worked")
            items = d["items"]; rows += items
            if not items or skip + len(items) >= d["pageInfo"]["countTotal"] or skip >= 9000: break
            skip += 1000
        print("day", k, "total so far", len(rows), flush=True)
    L = []
    for t in rows:
        x = t.get("data") or {}; m = t.get("market") or {}
        la, ca = m.get("loanAsset") or {}, m.get("collateralAsset") or {}
        def usd(raw, a):
            pr = a.get("price"); pr = pr.get("usd") if isinstance(pr, dict) else pr
            try: return int(raw) / 10 ** int(a.get("decimals") or 18) * float(pr or 0)
            except Exception: return 0.0
        seized = usd(x.get("seizedAssets") or 0, ca); repaid = usd(x.get("repaidAssets") or 0, la)
        L.append(dict(chain=(t.get("chain") or {}).get("network") or str((t.get("chain") or {}).get("id")),
                      ts=int(t["timestamp"]), liq=(x.get("liquidator") or "").lower(), seized=seized, repaid=repaid,
                      # Morpho's liquidation incentive is fixed by the market's LLTV: LIF = min(1.15, 1/(0.3*LLTV+0.7)).
                      # Bonus = repaid x (LIF-1): exact at the time of the liquidation, unlike today's prices.
                      bonus=repaid * (min(1.15, 1 / (0.3 * float(m.get("lltv") or 0) / 1e18 + 0.7)) - 1),
                      bad=usd(x.get("badDebtAssets") or 0, la),
                      pair=f"{ca.get('symbol')}/{la.get('symbol')}", lltv=float(m.get("lltv") or 0) / 1e18, hash=t["txHash"],
                      priced=bool(ca.get("price")) and bool(la.get("price"))))
    json.dump(L, open(f"{OUT}/liq_morpho.json", "w", encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    R = [f"# Morpho Blue liquidations, last {DAYS} days — {now:%Y-%m-%d %H:%M} UTC", "",
         "Bonus = debt repaid x (liquidation incentive - 1); the incentive is fixed by each market's LLTV. Debt valued at today's loan-token price (mostly stablecoins). Before gas and swap costs.", "",
         "| Chain | Liquidations | Total bonus $ | Median bonus $ | Bonus > $50 | Distinct winners | Top winner's share | Top 3 share | Bonus $5-50 (small) |",
         "|---|---|---|---|---|---|---|---|---|"]
    by = collections.defaultdict(list)
    for x in L: by[x["chain"]].append(x)
    for ch, xs in sorted(by.items(), key=lambda kv: -sum(x["bonus"] for x in kv[1])):
        tot = sum(x["bonus"] for x in xs); b = sorted(x["bonus"] for x in xs)
        win = collections.Counter(); [win.update({x["liq"]: x["bonus"]}) for x in xs]
        top = win.most_common(3); t1 = top[0][1] / tot if tot > 0 else 0; t3 = sum(v for _, v in top) / tot if tot > 0 else 0
        small = [x for x in xs if 5 <= x["bonus"] <= 50]
        R.append(f"| {ch} | {len(xs)} | {tot:,.0f} | {b[len(b)//2]:,.2f} | {sum(1 for v in b if v > 50)} | {len(win)} | {t1:.0%} | {t3:.0%} | {len(small)} (${sum(x['bonus'] for x in small):,.0f}) |")
    R += ["", "## Biggest winners overall", "", "| Liquidator | Chains | Liquidations | Bonus $ |", "|---|---|---|---|"]
    W = collections.defaultdict(lambda: [0, 0.0, set()])
    for x in L: w = W[x["liq"]]; w[0] += 1; w[1] += x["bonus"]; w[2].add(x["chain"])
    for a, (n, b, chs) in sorted(W.items(), key=lambda kv: -kv[1][1])[:12]:
        R.append(f"| {a[:10]}… | {', '.join(sorted(chs))} | {n} | {b:,.0f} |")
    R += ["", "## Biggest single liquidations", "", "| Chain | Pair | Repaid $ | Bonus $ | Winner |", "|---|---|---|---|---|"]
    for x in sorted(L, key=lambda x: -x["bonus"])[:12]:
        R.append(f"| {x['chain']} | {x['pair']} | {x['repaid']:,.0f} | {x['bonus']:,.0f} | {x['liq'][:10]}… |")
    days = collections.Counter(dt.datetime.fromtimestamp(x["ts"], dt.timezone.utc).strftime("%m-%d") for x in L)
    R += ["", "Liquidations per day: " + ", ".join(f"{k} {v}" for k, v in sorted(days.items()))]
    open(f"{OUT}/LIQ_MORPHO.md", "w", encoding="utf-8").write("\n".join(R) + "\n"); print("\n".join(R))

if __name__ == "__main__":
    main()
