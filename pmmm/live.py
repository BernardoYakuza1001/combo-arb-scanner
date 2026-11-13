"""PMMM live mode: runs on YOUR machine with YOUR Polymarket key.

Same strategy as the paper bot (bot.py): two resting BUY orders per market
(YES and NO) inside the reward max spread, re-centred on the adjusted mid,
paused on price jumps, one-sided once inventory is lopsided.

Safety:
  * Does nothing unless PMMM_LIVE=YES is set.
  * Key and proxy address are read from the .env file you write yourself; they are never printed.
  * Hard cap on total dollars in open orders + inventory (PMMM_MAX_TOTAL).
  * Create a file named STOP next to this script to cancel everything and exit.
  * Cancels all orders on exit or on any crash.

.env (same folder):
  PM_PRIVATE_KEY=0x...          # exported from Polymarket (Settings > Export private key)
  PM_FUNDER=0x...               # your Polymarket wallet (proxy) address shown on your profile
  PM_SIG_TYPE=1                 # 1 = email/Magic login, 2 = browser-wallet login
"""
import os, sys, time, json, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm import bot
from pmmm.bot import CFG, adj_mid, target, http, CLOB

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "STOP")
LOG = os.path.join(HERE, "live_log.jsonl")
MAX_TOTAL = float(os.environ.get("PMMM_MAX_TOTAL", "1000"))

def load_env():
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def log(**kw):
    kw["t"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f: f.write(json.dumps(kw) + "\n")
    print(kw, flush=True)

class Live:
    def __init__(self):
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        self.OrderArgs, self.OrderType, self.BUY = OrderArgs, OrderType, BUY
        self.c = ClobClient(CLOB, key=os.environ["PM_PRIVATE_KEY"], chain_id=137,
                            signature_type=int(os.environ.get("PM_SIG_TYPE", "1")), funder=os.environ["PM_FUNDER"])
        self.c.set_api_creds(self.c.create_or_derive_api_creds())

    def place(self, token, price, size):
        o = self.c.create_order(self.OrderArgs(price=round(price, 4), size=float(size), side=self.BUY, token_id=token))
        r = self.c.post_order(o, self.OrderType.GTC)
        return r.get("orderID") if isinstance(r, dict) and r.get("success", True) else None

    def cancel(self, oid):
        try: self.c.cancel(order_id=oid)
        except Exception as e: log(ev="cancel_err", err=str(e))

    def matched(self, oid):
        try:
            o = self.c.get_order(oid); return float(o.get("size_matched") or 0), o.get("status")
        except Exception: return None, None

    def cancel_all(self):
        try: self.c.cancel_all()
        except Exception as e: log(ev="cancel_all_err", err=str(e))

def main():
    load_env()
    if os.environ.get("PMMM_LIVE") != "YES":
        print("Live mode is off. Set PMMM_LIVE=YES (see RUN_LIVE.bat) after the paper results look right."); return
    for k in ("PM_PRIVATE_KEY", "PM_FUNDER"):
        if not os.environ.get(k): print(f"Missing {k} in .env"); return
    L = Live()
    mk = bot.select()
    log(ev="start", markets=[m.q[:50] for m in mk], cap=CFG["cap"], max_total=MAX_TOTAL)
    live = {}          # (cid, side) -> dict(oid, price, size, matched)
    try:
        while not os.path.exists(STOP):
            now = time.time()
            bk = {b.get("asset_id"): b for b in (http("POST", f"{CLOB}/books", json=[{"token_id": m.tok} for m in mk]) or [])}
            exposure = sum(m.yes * (m.mid or .5) + m.no * (1 - (m.mid or .5)) for m in mk) + \
                       sum(o["price"] * (o["size"] - o["matched"]) for o in live.values())
            for m in mk:
                b = bk.get(m.tok)
                if not b or not b.get("bids") or not b.get("asks"): continue
                # 1) fills
                for sd in ("Y", "N"):
                    o = live.get((m.cid, sd))
                    if not o: continue
                    got, status = L.matched(o["oid"])
                    if got is not None and got > o["matched"]:
                        d = got - o["matched"]; o["matched"] = got
                        if sd == "Y": m.yes += d
                        else: m.no += d
                        m.cash -= d * o["price"]
                        log(ev="fill", q=m.q[:50], side=sd, px=o["price"], sz=d)
                    if status and status.upper() not in ("LIVE", "OPEN") and o["matched"] >= o["size"] - 1e-6:
                        live.pop((m.cid, sd), None)
                sets = min(m.yes, m.no)
                if sets > 0: m.yes -= sets; m.no -= sets; m.merged += sets   # held as full sets ($1 each at resolution)
                # 2) decide
                mid = adj_mid(b, m.minsz)
                if mid is None: continue
                m.mid = mid; m.mids.append((now, mid))
                while m.mids and now - m.mids[0][0] > CFG["news_win_s"]: m.mids.popleft()
                if max(x[1] for x in m.mids) - min(x[1] for x in m.mids) > CFG["news_move"]:
                    if now >= m.paused_until: log(ev="pause", q=m.q[:50], mid=mid)
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
                       0 < ((mid - o["price"]) if sd == "Y" else ((1 - mid) - o["price"])) < m.v / 100:
                        continue
                    if o: L.cancel(o["oid"]); live.pop((m.cid, sd), None); exposure -= o["price"] * (o["size"] - o["matched"])
                    if w:
                        cost = w[0] * w[1]
                        if exposure + cost > MAX_TOTAL: continue
                        oid = L.place(m.tok if sd == "Y" else m.tno, w[0], w[1])
                        if oid:
                            live[(m.cid, sd)] = dict(oid=oid, price=w[0], size=w[1], matched=0.0); exposure += cost
                            log(ev="quote", q=m.q[:50], side=sd, px=w[0], sz=w[1])
            time.sleep(CFG["cycle_s"])
    finally:
        L.cancel_all(); log(ev="stopped")

if __name__ == "__main__":
    main()
