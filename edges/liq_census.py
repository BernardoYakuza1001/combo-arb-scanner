"""Liquidation census across lending protocols and chains (last N days, straight from on-chain logs).

Aave V3 and its forks emit LiquidationCall(collateralAsset, debtAsset, user, debtToCover,
liquidatedCollateralAmount, liquidator, receiveAToken). Compound-style markets (Moonwell, Venus, ...)
emit LiquidateBorrow(liquidator, borrower, repayAmount, cTokenCollateral, seizeTokens).
For each: count, debt repaid (USD), liquidator bonus (USD), distinct winners and how concentrated they are.
Prices are today's prices (DefiLlama). Bonus for Aave = collateral seized - debt repaid; for Compound-style
it is estimated from the protocol's usual incentive (8%) because seized amounts are in cTokens.
"""
import json, os, time, collections, datetime as dt, requests
from concurrent.futures import ThreadPoolExecutor

OUT = "data/edges"; DAYS = int(os.environ.get("LIQ_DAYS", "30"))
AAVE_TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"
COMP_TOPIC = "0x298637f684da70674f26509b10f07ec2fbc77a335ab1e7d6215a4b2484d8bb52"   # LiquidateBorrow(address,address,uint256,address,uint256)

CHAINS = {   # name: (rpc, defillama chain, {protocol: (kind, address or None=all emitters)})
    "ethereum":  ("https://ethereum-rpc.publicnode.com", "ethereum", {"Aave V3": ("aave", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"), "Spark": ("aave", "0xC13e21B648A5Ee794902342038FF3aDAB66BE987")}),
    "base":      ("https://base-rpc.publicnode.com", "base", {"Aave V3": ("aave", "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"), "Compound-style (Moonwell etc.)": ("comp", None)}),
    "arbitrum":  ("https://arbitrum-one-rpc.publicnode.com", "arbitrum", {"Aave V3": ("aave", "0x794a61358D6845594F94dc1DB02A252b5b4814aD")}),
    "optimism":  ("https://optimism-rpc.publicnode.com", "optimism", {"Aave V3": ("aave", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"), "Compound-style (Moonwell etc.)": ("comp", None)}),
    "polygon":   ("https://polygon-bor-rpc.publicnode.com", "polygon", {"Aave V3": ("aave", "0x794a61358D6845594F94dc1DB02A252b5b4814aD")}),
    "avalanche": ("https://avalanche-c-chain-rpc.publicnode.com", "avax", {"Aave V3": ("aave", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"), "Compound-style (Benqi etc.)": ("comp", None)}),
    "gnosis":    ("https://gnosis-rpc.publicnode.com", "xdai", {"Aave V3": ("aave", "0xb50201558B00496A145fE76f7424749556E326D8")}),
    "scroll":    ("https://scroll-rpc.publicnode.com", "scroll", {"Aave V3": ("aave", "0x11fCfe756c05AD438e312a7fd934381537D3cFfe")}),
    "bsc":       ("https://bsc-rpc.publicnode.com", "bsc", {"Aave V3": ("aave", "0x6807dc923806fE8Fd134338EABCA509979a7e0cB"), "Compound-style (Venus etc.)": ("comp", None)}),
    "linea":     ("https://linea-rpc.publicnode.com", "linea", {"Aave V3": ("aave", "0xc47b8C00b0f69a36fa203Ffeac0334874574a8Ac")}),
    "sonic":     ("https://sonic-rpc.publicnode.com", "sonic", {"Aave V3": ("aave", "0x5362dBb1e601abF3a4c14c22ffEdA64042E5eAA3")}),
}

def rpc(url, method, params, tries=5):
    for i in range(tries):
        try:
            r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60)
            j = r.json()
            if "error" in j: raise RuntimeError(str(j["error"])[:200])
            return j["result"]
        except Exception as e:
            last = e; time.sleep(1 + i)
    raise last

def block_at(url, ts):
    head = int(rpc(url, "eth_blockNumber", []), 16)
    lo, hi = 1, head
    while lo < hi:
        mid = (lo + hi) // 2
        t = int(rpc(url, "eth_getBlockByNumber", [hex(mid), False])["timestamp"], 16)
        if t < ts: lo = mid + 1
        else: hi = mid
    return lo, head

def logs(url, address, topic, start, end, log):
    out, step, b = [], 20000, start
    while b <= end:
        e = min(end, b + step - 1)
        p = {"fromBlock": hex(b), "toBlock": hex(e), "topics": [topic]}
        if address: p["address"] = address
        try:
            out += rpc(url, "eth_getLogs", [p], tries=2); b = e + 1; step = min(step * 2, 200000)
        except Exception as ex:
            if step <= 500: log(f"giving up range {b}-{e}: {ex}"); b = e + 1; continue
            step //= 4
    return out

DEC, PRICE = {}, {}
def prices(llama, addrs):
    need = [a for a in addrs if (llama, a) not in PRICE]
    for i in range(0, len(need), 60):
        keys = ",".join(f"{llama}:{a}" for a in need[i:i + 60])
        try:
            j = requests.get(f"https://coins.llama.fi/prices/current/{keys}", timeout=30).json().get("coins", {})
        except Exception: j = {}
        for a in need[i:i + 60]:
            c = j.get(f"{llama}:{a}") or {}
            PRICE[(llama, a)] = (float(c.get("price") or 0), int(c.get("decimals") or 18))

def underlying(url, ctoken):
    try:
        r = rpc(url, "eth_call", [{"to": ctoken, "data": "0x6f307dc3"}, "latest"], tries=2)   # underlying()
        return "0x" + r[-40:]
    except Exception: return None   # native-asset market (e.g. mETH / vBNB): no underlying()

def scan(name):
    url, llama, protos = CHAINS[name]; rows = []
    log = lambda m: print(f"[{name}] {m}", flush=True)
    try: start, end = block_at(url, int(time.time() - DAYS * 86400))
    except Exception as e: log(f"no block range: {e}"); return rows
    for proto, (kind, addr) in protos.items():
        L = logs(url, addr, AAVE_TOPIC if kind == "aave" else COMP_TOPIC, start, end, log)
        log(f"{proto}: {len(L)} liquidation logs")
        if kind == "aave":
            assets = set()
            for l in L: assets |= {"0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]}
            prices(llama, sorted(assets))
            for l in L:
                col, debt = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
                d = l["data"][2:]; dc, lc, liq = int(d[0:64], 16), int(d[64:128], 16), "0x" + d[128 + 24:192]
                pd, dd = PRICE[(llama, debt)]; pc, dcm = PRICE[(llama, col)]
                debt_usd = dc / 10 ** dd * pd; col_usd = lc / 10 ** dcm * pc
                rows.append(dict(chain=name, proto=proto, liq=liq.lower(), debt=debt_usd, bonus=max(0.0, col_usd - debt_usd) if pd and pc else 0.0,
                                 priced=bool(pd and pc), block=int(l["blockNumber"], 16)))
        else:
            em = sorted({l["address"].lower() for l in L}); und = {}
            for a in em: und[a] = underlying(url, a)
            prices(llama, sorted({u for u in und.values() if u}))
            for l in L:
                d = l["data"][2:]
                if len(d) < 320: continue
                liq, rep = "0x" + d[24:64], int(d[128:192], 16)
                u = und.get(l["address"].lower()); p, dcm = PRICE.get((llama, u), (0.0, 18)) if u else (0.0, 18)
                debt_usd = rep / 10 ** dcm * p
                rows.append(dict(chain=name, proto=proto, liq=liq.lower(), debt=debt_usd, bonus=0.08 * debt_usd, priced=bool(p), block=int(l["blockNumber"], 16),
                                 market=l["address"].lower()))
    return rows

def main():
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(6) as ex: R = [r for rs in ex.map(scan, CHAINS) for r in rs]
    json.dump(R, open(f"{OUT}/liq_census.json", "w", encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    T = [f"# Liquidation census, last {DAYS} days — {now:%Y-%m-%d %H:%M} UTC", "",
         "Bonus = what liquidators kept before gas and swap costs (today's prices). Compound-style bonus estimated at 8% of repaid debt.", "",
         "| Chain | Protocol | Liquidations | Debt repaid $ | Bonus $ | Distinct winners | Top winner share | Top 3 share | Bonus $5-500 (count, $) |",
         "|---|---|---|---|---|---|---|---|---|"]
    G = collections.defaultdict(list)
    for r in R: G[(r["chain"], r["proto"])].append(r)
    for (ch, pr), xs in sorted(G.items(), key=lambda kv: -sum(x["bonus"] for x in kv[1])):
        tot = sum(x["bonus"] for x in xs); w = collections.Counter()
        for x in xs: w[x["liq"]] += x["bonus"]
        top = w.most_common(3); mid = [x for x in xs if 5 <= x["bonus"] <= 500]
        T.append(f"| {ch} | {pr} | {len(xs)} | {sum(x['debt'] for x in xs):,.0f} | {tot:,.0f} | {len(w)} | "
                 f"{(top[0][1] / tot if tot else 0):.0%} | {(sum(v for _, v in top) / tot if tot else 0):.0%} | {len(mid)} (${sum(x['bonus'] for x in mid):,.0f}) |")
    unpriced = sum(1 for r in R if not r["priced"])
    T += ["", f"Liquidations whose tokens had no price (counted, valued $0): {unpriced}"]
    open(f"{OUT}/LIQ_CENSUS.md", "w", encoding="utf-8").write("\n".join(T) + "\n"); print("\n".join(T))

if __name__ == "__main__":
    main()
