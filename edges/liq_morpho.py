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
query($skip:Int!,$since:Int!){
  transactions(first:1000, skip:$skip, orderBy: Timestamp, orderDirection: Desc,
               where:{type_in:[MarketLiquidation], timestamp_gte:$since}){
    pageInfo{countTotal count}
    items{
      hash timestamp blockNumber chain{ id network }
      user{ address }
      data{
        ... on MarketLiquidationTransactionData{
          liquidator seizedAssets repaidAssets seizedAssetsUsd repaidAssetsUsd badDebtAssetsUsd
          market{ uniqueKey lltv loanAsset{ symbol } collateralAsset{ symbol } }
        }
      }
    }
  }
}"""

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
    rows, skip = [], 0
    while True:
        d = gql(Q, {"skip": skip, "since": since})["transactions"]
        items = d["items"]; rows += items
        print("fetched", len(rows), "of", d["pageInfo"]["countTotal"], flush=True)
        if len(items) < 1000 or skip > 60000: break
        skip += 1000
    L = []
    for t in rows:
        x = t.get("data") or {}
        seized = float(x.get("seizedAssetsUsd") or 0); repaid = float(x.get("repaidAssetsUsd") or 0)
        m = x.get("market") or {}
        L.append(dict(chain=(t.get("chain") or {}).get("network") or str((t.get("chain") or {}).get("id")),
                      ts=int(t["timestamp"]), liq=(x.get("liquidator") or "").lower(), seized=seized, repaid=repaid,
                      bonus=seized - repaid, bad=float(x.get("badDebtAssetsUsd") or 0),
                      pair=f"{(m.get('collateralAsset') or {}).get('symbol')}/{(m.get('loanAsset') or {}).get('symbol')}",
                      lltv=float(m.get("lltv") or 0) / 1e18, hash=t["hash"]))
    json.dump(L, open(f"{OUT}/liq_morpho.json", "w", encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    R = [f"# Morpho Blue liquidations, last {DAYS} days — {now:%Y-%m-%d %H:%M} UTC", "",
         "Bonus = collateral seized minus debt repaid (USD, Morpho's own valuation), before gas and swap costs.", "",
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
