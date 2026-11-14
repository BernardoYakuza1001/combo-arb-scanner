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
  * Dead-man switch: sends Polymarket a heartbeat every 5 s. If this PC sleeps, crashes or loses
    internet, Polymarket itself cancels all your open orders about 10 s later.
  * Loss stop: cancels everything and exits if trading loss since start exceeds PMMM_MAX_LOSS (default $30).
  * Blind stop: if a market's order book cannot be read for 3 cycles, its orders are cancelled.
  * Keeps Windows awake while it runs (only while the window is open).
  * Shield rule: only quotes behind PMMM_AHEAD order sizes of other people's resting orders.
  * Stop-loss: a filled position is sold at market once the price is PMMM_STOP (default 4c) below entry.
  * Exit order: after a fill, the opposite quote becomes a resting SELL of the held shares (frees the cash).
  * Re-ranks markets every 6 hours; orders in dropped markets are cancelled.

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
MAX_LOSS = float(os.environ.get("PMMM_MAX_LOSS", "30"))
RESELECT_S = float(os.environ.get("PMMM_RESELECT_H", "6")) * 3600
LIVE_CYCLE_S = float(os.environ.get("PMMM_CYCLE_S", "8"))

def keep_awake(on=True):
    """Ask Windows not to sleep while this process runs (process-level request, reverts when it exits)."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
    except Exception: pass

def start_heartbeat(L, stop_flag):
    """Dead-man switch. Returns the thread, or None if the venue rejects heartbeats."""
    import threading
    state = {"id": None, "fails": 0}
    def beat():
        r = L.c.post_heartbeat(state["id"])
        if isinstance(r, dict) and r.get("heartbeat_id"): state["id"] = r["heartbeat_id"]
    try: beat()
    except Exception as e:
        log(ev="heartbeat_unavailable", err=str(e)[:200]); return None
    def loop():
        while not stop_flag["stop"]:
            time.sleep(5)
            try: beat(); state["fails"] = 0
            except Exception as e:
                state["fails"] += 1
                if state["fails"] in (1, 5, 20): log(ev="heartbeat_err", n=state["fails"], err=str(e)[:150])
                state["id"] = None if state["fails"] % 3 == 0 else state["id"]
    t = threading.Thread(target=loop, daemon=True); t.start()
    log(ev="heartbeat_on"); return t

def pnl(mk):
    return sum(m.mtm() for m in mk)

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
        from py_clob_client.order_builder.constants import SELL
        self.OrderArgs, self.OrderType, self.BUY, self.SELL = OrderArgs, OrderType, BUY, SELL
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

    def place(self, token, price, size, tick, sell=False):
        """Resting post-only order (BUY by default, SELL for exits)."""
        dec = max(0, -int(round(__import__("math").log10(tick))))
        size = __import__("math").floor(float(size) * 100) / 100
        o = self.c.create_order(self.OrderArgs(price=round(price, dec), size=size, side=self.SELL if sell else self.BUY, token_id=token))
        r = self.c.post_order(o, self.OrderType.GTC, post_only=True)
        if isinstance(r, dict) and r.get("success", True) and r.get("orderID"): return r["orderID"]
        raise RuntimeError(f"rejected: {r}")

    def stop_sell(self, token, price, size, tick):
        """Stop-loss: sell now at up to `price` (fill what is available, cancel the rest)."""
        dec = max(0, -int(round(__import__("math").log10(tick))))
        size = __import__("math").floor(float(size) * 100) / 100
        o = self.c.create_order(self.OrderArgs(price=round(max(price, tick), dec), size=size, side=self.SELL, token_id=token))
        return self.c.post_order(o, self.OrderType.FAK)

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
    """Shares currently held, by token id (Data API v2)."""
    out = {}; cur = None
    for _ in range(20):
        p = {"user": funder, "limit": 500}
        if cur: p["cursor"] = cur
        d = http("GET", "https://data-api.polymarket.com/v2/positions", params=p) or {}
        rows = d.get("data", []) if isinstance(d, dict) else d
        for r in rows: out[str(r.get("token_id"))] = float(r.get("current_size") or 0)
        pg = d.get("pagination", {}) if isinstance(d, dict) else {}
        cur = pg.get("next_cursor")
        if not rows or not pg.get("has_more") or not cur: break
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
    keep_awake(True)
    def load_markets(old=()):
        new = bot.select()
        if not new: return list(old)
        pos = positions(L.funder); keep = {m.cid: m for m in old}
        out = []
        for m in new:
            if m.cid in keep: out.append(keep[m.cid]); continue     # keep running state (inventory, cash, pause)
            m.yes = pos.get(str(m.tok), 0.0); m.no = pos.get(str(m.tno), 0.0)
            s = min(m.yes, m.no); m.yes -= s; m.no -= s
            m.cash = -(m.yes + m.no) * 0.5                          # rough carry-in cost; baseline below removes it
            out.append(m)
        log(ev="markets", markets=[m.q[:50] for m in out], held={m.q[:30]: (m.yes, m.no) for m in out if m.yes or m.no})
        return out
    mk = load_markets()
    if not mk: log(ev="abort", why="no market passed the filters"); return
    live = {}; errors = 0; t0 = time.time(); last_score = 0; scoring_frac = "n/a"; last_day = None
    last_select = t0; baseline = None; retired_pnl = 0.0; hb_flag = {"stop": False}
    if not DRY: start_heartbeat(L, hb_flag)
    try:
        while not os.path.exists(STOP):
            now = time.time()
            if now - last_select > RESELECT_S:
                last_select = now
                try:
                    new = load_markets(mk); ids = {m.cid for m in new}
                    for m in mk:
                        if m.cid in ids: continue
                        for sd in ("Y", "N"):
                            o = live.pop((m.cid, sd), None)
                            if o and not DRY: L.cancel(o["oid"])
                        retired_pnl += m.mtm(); log(ev="market_dropped", q=m.q[:50], yes=m.yes, no=m.no)
                    mk = new
                except Exception as e: log(ev="reselect_err", err=str(e)[:200])
            bk = {b.get("asset_id"): b for b in (http("POST", f"{CLOB}/books", json=[{"token_id": m.tok} for m in mk]) or [])}
            exposure = sum(m.yes * (m.mid or .5) + m.no * (1 - (m.mid or .5)) for m in mk) + \
                       sum(o["price"] * (o["size"] - o["matched"]) for o in live.values() if not o.get("sell"))
            for m in mk:
                b = bk.get(m.tok)
                if not b or not b.get("bids") or not b.get("asks"):
                    m.blind = getattr(m, "blind", 0) + 1
                    if m.blind == 3:
                        for sd in ("Y", "N"):
                            o = live.pop((m.cid, sd), None)
                            if o and not DRY:
                                try: L.cancel(o["oid"])
                                except Exception as e: log(ev="error", q=m.q[:50], err=str(e)[:200])
                        log(ev="blind_cancel", q=m.q[:50])
                    continue
                m.blind = 0
                try:
                    # 1) fills
                    for sd in ("Y", "N"):
                        o = live.get((m.cid, sd))
                        if not o or DRY: continue
                        got, status = L.matched(o["oid"])
                        if got > o["matched"]:
                            d = got - o["matched"]; o["matched"] = got
                            if o.get("sell"):       # exit order: sold held shares of the other outcome
                                if sd == "N": m.yes = max(0.0, m.yes - d)
                                else: m.no = max(0.0, m.no - d)
                                m.cash += d * o["real_px"]
                                log(ev="exit_fill", q=m.q[:50], sold="YES" if sd == "N" else "NO", px=o["real_px"], sz=round(d, 2))
                            else:
                                if sd == "Y": m.avg_y = (getattr(m, "avg_y", 0.0) * m.yes + o["price"] * d) / (m.yes + d); m.yes += d
                                else: m.avg_n = (getattr(m, "avg_n", 0.0) * m.no + o["price"] * d) / (m.no + d); m.no += d
                                m.cash -= d * o["price"]
                                log(ev="fill", q=m.q[:50], side=sd, px=o["price"], sz=round(d, 2))
                        if status.upper() not in ("LIVE", "OPEN", ""):
                            live.pop((m.cid, sd), None)
                    s = min(m.yes, m.no)
                    if s > 0: m.yes -= s; m.no -= s; m.merged += s; m.cash += s; log(ev="pair_locked", q=m.q[:50], sets=round(s, 2))
                    # 2) decide
                    mid = adj_mid(b, m.minsz)
                    if mid is None: continue
                    m.mid = mid; m.mids.append((now, mid))
                    if m.yes > 0 and getattr(m, "avg_y", None) is None: m.avg_y = mid            # carried-in position: stop measured from here
                    if m.no > 0 and getattr(m, "avg_n", None) is None: m.avg_n = 1 - mid
                    while m.mids and now - m.mids[0][0] > CFG["news_win_s"]: m.mids.popleft()
                    if max(x[1] for x in m.mids) - min(x[1] for x in m.mids) > CFG["news_move"]:
                        if now >= m.paused_until: log(ev="pause", q=m.q[:50], mid=round(mid, 3))
                        m.paused_until = now + CFG["pause_s"]
                    # stop-loss on held positions
                    bb = max(float(l["price"]) for l in b["bids"]); ba = min(float(l["price"]) for l in b["asks"])
                    for side, held, avg, tok, exit_px in (("Y", m.yes, getattr(m, "avg_y", None), m.tok, bb),
                                                          ("N", m.no, getattr(m, "avg_n", None), m.tno, 1 - ba)):
                        if held >= 5 and avg is not None and exit_px <= avg - CFG["stop_c"]:
                            for sd2 in ("Y", "N"):
                                o2 = live.pop((m.cid, sd2), None)
                                if o2 and not DRY: L.cancel(o2["oid"])
                            r = None if DRY else L.stop_sell(tok, exit_px - 2 * m.tick, held, m.tick)
                            m.cash += held * (exit_px - m.tick)
                            log(ev="stop_loss", q=m.q[:50], sold="YES" if side == "Y" else "NO", sz=round(held, 2), entry=round(avg, 3),
                                px=round(exit_px, 3), est_loss=round(held * (avg - exit_px + m.tick), 2), resp=str(r)[:200])
                            if side == "Y": m.yes = 0.0
                            else: m.no = 0.0
                            m.paused_until = now + CFG["pause_s"]
                    tg = None if now < m.paused_until else target(m, b, mid)
                    want = {"Y": None, "N": None}       # slot -> (slot price, size, sell?)
                    if tg:
                        yb, nb, n, yb0, nb0 = tg; imb = m.yes - m.no
                        if yb is not None and imb < CFG["max_imb"] * n: want["Y"] = (yb, n, False)
                        if nb is not None and -imb < CFG["max_imb"] * n: want["N"] = (nb, n, False)
                        # exit orders: holding YES -> the NO slot becomes SELL YES at the same book level (1 - nb0)
                        if m.yes >= 5: want["N"] = (nb0, m.yes, True)
                        if m.no >= 5: want["Y"] = (yb0, m.no, True)
                    # 3) reconcile
                    for sd, w in want.items():
                        o = live.get((m.cid, sd))
                        if o and w and bool(o.get("sell")) == w[2] and abs(o["price"] - w[0]) <= 0.01 + 1e-9 and \
                           0 < ((mid - o["price"]) if sd == "Y" else ((1 - mid) - o["price"])) < m.v / 100 and \
                           (abs((o["size"] - o["matched"]) - w[1]) < 1 if w[2] else o["size"] - o["matched"] >= m.minsz):
                            continue
                        if o:
                            if not DRY: L.cancel(o["oid"])
                            live.pop((m.cid, sd), None)
                            if not o.get("sell"): exposure -= o["price"] * (o["size"] - o["matched"])
                        if w and w[2]:      # exit: SELL the held outcome at the mirrored price
                            tok = m.tok if sd == "N" else m.tno; real = round(1 - w[0], 6)
                            oid = f"dry-{m.cid[:8]}-{sd}-{int(now)}" if DRY else L.place(tok, real, w[1], m.tick, sell=True)
                            live[(m.cid, sd)] = dict(oid=oid, price=w[0], size=__import__("math").floor(w[1] * 100) / 100, matched=0.0, sell=True, real_px=real)
                            log(ev="exit_quote", q=m.q[:50], sell="YES" if sd == "N" else "NO", px=real, sz=round(w[1], 2), dry=DRY)
                        elif w:
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
            # loss stop (trading P&L since start; rewards are paid separately and not counted here)
            if all(m.mid is not None for m in mk):
                cur = pnl(mk) + retired_pnl
                if baseline is None: baseline = cur
                if cur - baseline < -MAX_LOSS:
                    log(ev="abort", why=f"loss stop: trading P&L {cur - baseline:+.2f} is below -{MAX_LOSS:.0f}"); return
            write_status(mk, live, usdc, cap, scoring_frac, t0)
            time.sleep(LIVE_CYCLE_S)
    finally:
        hb_flag["stop"] = True; keep_awake(False)
        if not DRY:
            try: L.cancel_all()
            except Exception as e: log(ev="cancel_all_err", err=str(e)[:200])
        log(ev="stopped")

if __name__ == "__main__":
    main()
