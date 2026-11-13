"""PMMM live mode: runs on YOUR machine with YOUR Polymarket key.

Same strategy as the paper bot (bot.py): two resting BUY orders per market
(YES and NO) inside the reward max spread, re-centred on the adjusted mid,
paused on price jumps, one-sided once inventory is lopsided.

Safety:
  * Does nothing unless PMMM_LIVE=YES. With PMMM_DRY=YES it logs the orders it would send and sends none.
  * Orders are post-only: they can only rest in the book (maker), never cross it as taker.
  * Key and wallet are read from pmmm\\.env, written by you; they are never printed or logged.
  * Hard cap on dollars in open orders + inventory: min(PMMM_MAX_TOTAL, USDC balance).
  * Cancels every open order at start, on exit, on crash, and when a file named STOP appears in pmmm\\.
  * Stops itself after 20 API errors in a row.

pmmm\\.env:
  PM_PRIVATE_KEY=0x...   Polymarket > Settings > Export private key
  PM_FUNDER=0x...        your Polymarket wallet address (profile / deposit address)
  PM_SIG_TYPE=1          1 = email or Google login, 2 = MetaMask / browser-wallet login
"""
import os, sys, time, json, datetime as dt, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm import bot
from pmmm.bot import CFG, adj_mid, target, http, CLOB

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "STOP")
LOG = os.path.join(HERE, "live_log.jsonl")
STATUS = os.path.join(HERE, "LIVE_STATUS.md")
MAX_TOTAL = float(os.environ.get("PMMM_MAX_TOTAL", "1000"))
DRY = os.environ.get("PMMM_DRY") == "YES"

def load_env():
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def log(**kw):
    kw["t"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f: f.write(json.dumps(kw) + "\n")
    print({k: v for k, v in kw.items() if k != "t"}, flush=True)

class Live:
    def __init__(self):
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType, BalanceAllowanceParams, AssetType, OrdersScoringParams, RequestArgs
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.headers.headers import create_level_2_headers
        self.OrderArgs, self.OrderType, self.BUY = OrderArgs, OrderType, BUY
        self.BAP, self.AT, self.OSP, self.RA, self.l2h = BalanceAllowanceParams, AssetType, OrdersScoringParams, RequestArgs, create_level_2_headers
        self.funder = os.environ["PM_FUNDER"]
        self.c = ClobClient(CLOB, key=os.environ["PM_PRIVATE_KEY"], chain_id=137,
                            signature_type=int(os.environ.get("PM_SIG_TYPE", "1")), funder=self.funder)
        self.c.set_api_creds(self.c.create_or_derive_api_creds())

    def usdc(self):
        p = self.BAP(asset_type=self.AT.COLLATERAL, signature_type=int(os.environ.get("PM_SIG_TYPE", "1")))
        try: self.c.update_balance_allowance(p)
        except Exception: pass
        r = self.c.get_balance_allowance(p)
        return float(r.get("balance", 0)) / 1e6

    def place(self, token, price, size, tick):
        dec = max(0, -int(round(__import__("math").log10(tick))))
        o = self.c.create_order(self.OrderArgs(price=round(price, dec), size=round(float(size), 2), side=self.BUY, token_id=token))
        r = self.c.post_order(o, self.OrderType.GTC, post_only=True)
        if isinstance(r, dict) and r.get("success", True) and r.get("orderID"): return r["orderID"]
        raise RuntimeError(f"rejected: {r}")

    def cancel(self, oid): self.c.cancel(oid)
    def cancel_all(self): self.c.cancel_all()

    def matched(self, oid):
        o = self.c.get_order(oid) or {}
        return float(o.get("size_matched") or 0), str(o.get("status") or "")

    def scoring(self, oids):
        if not oids: return {}
        r = self.c.are_orders_scoring(self.OSP(orderIds=oids))
        return r if isinstance(r, dict) else {}

    def earnings(self, day):
        path = "/rewards/user/total"
        h = self.l2h(self.c.signer, self.c.creds, self.RA(method="GET", request_path=path))
        r = requests.get(CLOB + path, headers=h, params={"date": day, "maker_address": self.funder}, timeout=20)
        return r.json() if r.ok else {"error": r.status_code}

def positions(funder):
    out = {}
    for page in range(10):
        d = http("GET", "https://data-api.polymarket.com/positions",
                 params={"user": funder, "sizeThreshold": 0, "limit": 500, "offset": 500 * page}) or []
        for p in d: out[str(p.get("asset"))] = float(p.get("size") or 0)
        if len(d) < 500: break
    return out

def write_status(mk, live, usdc, cap, scoring_frac, t0):
    L = [f"# PMMM live status — {dt.datetime.now():%Y-%m-%d %H:%M:%S} {'(DRY RUN)' if DRY else ''}", "",
         f"Running {(time.time()-t0)/3600:.1f} h. USDC at start ${usdc:,.2f}; cap ${cap:,.0f}. Orders earning rewards right now: {scoring_frac}", "",
         "| Market | Mid | YES bid | NO bid | YES held | NO held | Paused |", "|---|---|---|---|---|---|---|"]
    for m in mk:
        y = live.get((m.cid, "Y")); n = live.get((m.cid, "N"))
        ys = "" if not y else "%s x %.0f" % (y["price"], y["size"]); ns = "" if not n else "%s x %.0f" % (n["price"], n["size"])
        mid = "" if m.mid is None else round(m.mid, 3); pz = "yes" if time.time() < m.paused_until else ""
        L.append(f"| {m.q[:60]} | {mid} | {ys} | {ns} | {m.yes:.0f} | {m.no:.0f} | {pz} |")
    open(STATUS, "w", encoding="utf-8").write("\n".join(L) + "\n")

def main():
    load_env()
    if os.environ.get("PMMM_LIVE") != "YES" and not DRY:
        print("Live mode is off. Use RUN_LIVE.bat (or DRY_RUN.bat to test without orders)."); return
    for k in ("PM_PRIVATE_KEY", "PM_FUNDER"):
        if not os.environ.get(k): print(f"Missing {k} in pmmm\\.env"); return
    L = Live()
    usdc = L.usdc(); cap = min(MAX_TOTAL, usdc)
    log(ev="start", dry=DRY, usdc=round(usdc, 2), cap=cap, per_market=CFG["cap"], n=CFG["n_markets"])
    if cap < CFG["cap"]: log(ev="abort", why=f"USDC ${usdc:.2f} is below one market's size ${CFG['cap']:.0f}"); return
    if not DRY: L.cancel_all()
    mk = bot.select()
    pos = positions(L.funder)
    for m in mk:
        m.yes = pos.get(str(m.tok), 0.0); m.no = pos.get(str(m.tno), 0.0)
        s = min(m.yes, m.no); m.yes -= s; m.no -= s
    log(ev="markets", markets=[m.q[:50] for m in mk], held={m.q[:30]: (m.yes, m.no) for m in mk if m.yes or m.no})
    live = {}; errors = 0; t0 = time.time(); last_score = 0; scoring_frac = "n/a"; last_day = None
    try:
        while not os.path.exists(STOP):
            now = time.time()
            bk = {b.get("asset_id"): b for b in (http("POST", f"{CLOB}/books", json=[{"token_id": m.tok} for m in mk]) or [])}
            exposure = sum(m.yes * (m.mid or .5) + m.no * (1 - (m.mid or .5)) for m in mk) + \
                       sum(o["price"] * (o["size"] - o["matched"]) for o in live.values())
            for m in mk:
                b = bk.get(m.tok)
                if not b or not b.get("bids") or not b.get("asks"): continue
                try:
                    # 1) fills
                    for sd in ("Y", "N"):
                        o = live.get((m.cid, sd))
                        if not o or DRY: continue
                        got, status = L.matched(o["oid"])
                        if got > o["matched"]:
                            d = got - o["matched"]; o["matched"] = got
                            if sd == "Y": m.yes += d
                            else: m.no += d
                            log(ev="fill", q=m.q[:50], side=sd, px=o["price"], sz=round(d, 2))
                        if status.upper() not in ("LIVE", "OPEN", ""):
                            live.pop((m.cid, sd), None)
                    s = min(m.yes, m.no)
                    if s > 0: m.yes -= s; m.no -= s; m.merged += s; log(ev="pair_locked", q=m.q[:50], sets=round(s, 2))
                    # 2) decide
                    mid = adj_mid(b, m.minsz)
                    if mid is None: continue
                    m.mid = mid; m.mids.append((now, mid))
                    while m.mids and now - m.mids[0][0] > CFG["news_win_s"]: m.mids.popleft()
                    if max(x[1] for x in m.mids) - min(x[1] for x in m.mids) > CFG["news_move"]:
                        if now >= m.paused_until: log(ev="pause", q=m.q[:50], mid=round(mid, 3))
                        m.paused_until = now + CFG["pause_s"]
                    tg = None if now < m.paused_until else target(m, b, mid)
                    want = {"Y": None, "N": None}
                    if tg:
                        yb, nb, n = tg; imb = m.yes - m.no
                        if imb < CFG["max_imb"] * n: want["Y"] = (yb, n)
                        if -imb < CFG["max_imb"] * n: want["N"] = (nb, n)
                    # 3) reconcile
                    for sd, w in want.items():
                        o = live.get((m.cid, sd))
                        if o and w and abs(o["price"] - w[0]) <= 0.01 + 1e-9 and \
                           0 < ((mid - o["price"]) if sd == "Y" else ((1 - mid) - o["price"])) < m.v / 100 and \
                           o["size"] - o["matched"] >= m.minsz:
                            continue
                        if o:
                            if not DRY: L.cancel(o["oid"])
                            live.pop((m.cid, sd), None); exposure -= o["price"] * (o["size"] - o["matched"])
                        if w:
                            cost = w[0] * w[1]
                            if exposure + cost > cap: continue
                            oid = f"dry-{m.cid[:8]}-{sd}-{int(now)}" if DRY else L.place(m.tok if sd == "Y" else m.tno, w[0], w[1], m.tick)
                            live[(m.cid, sd)] = dict(oid=oid, price=w[0], size=w[1], matched=0.0); exposure += cost
                            log(ev="quote", q=m.q[:50], side=sd, px=w[0], sz=w[1], dry=DRY)
                    errors = 0
                except Exception as e:
                    errors += 1; log(ev="error", q=m.q[:50], err=str(e)[:300])
                    if errors >= 20: log(ev="abort", why="20 errors in a row"); return
            # rewards check every 5 min
            if not DRY and now - last_score > 300:
                last_score = now
                try:
                    ids = [o["oid"] for o in live.values()]
                    sc = L.scoring(ids); k = sum(1 for i in ids if sc.get(i))
                    scoring_frac = f"{k}/{len(ids)}"; log(ev="scoring", scoring=scoring_frac)
                    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
                    if day != last_day: last_day = day; log(ev="earnings", day=day, result=L.earnings(day))
                except Exception as e: log(ev="scoring_err", err=str(e)[:200])
            write_status(mk, live, usdc, cap, scoring_frac, t0)
            time.sleep(CFG["cycle_s"])
    finally:
        if not DRY:
            try: L.cancel_all()
            except Exception as e: log(ev="cancel_all_err", err=str(e)[:200])
        log(ev="stopped")

if __name__ == "__main__":
    main()
