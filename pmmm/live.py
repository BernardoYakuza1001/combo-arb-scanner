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
  PM_SIG_TYPE=3          3 = Deposit Wallet (accounts created after May 2026); 1 = older email/Google; 2 = older MetaMask
"""
import os, sys, time, json, datetime as dt, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm import bot
from pmmm.bot import CFG, adj_mid, target, http, CLOB, fair_mid, DATA
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "STOP")
_DRY = os.environ.get("PMMM_DRY") == "YES"       # dry runs keep their own files, so they never overwrite the live ones
LOG = os.path.join(HERE, "dry_log.jsonl" if _DRY else "live_log.jsonl")
STATUS = os.path.join(HERE, "DRY_STATUS.md" if _DRY else "LIVE_STATUS.md")
REBID_WAIT_S = float(os.environ.get("PMMM_REBID_WAIT_S", "900"))   # after a fill, do not bid that side again for this long
MAX_TOTAL = float(os.environ.get("PMMM_MAX_TOTAL", "1000"))
DRY = os.environ.get("PMMM_DRY") == "YES"
MAX_LOSS = float(os.environ.get("PMMM_MAX_LOSS", "30"))
RESELECT_S = float(os.environ.get("PMMM_RESELECT_H", "6")) * 3600
LIVE_CYCLE_S = float(os.environ.get("PMMM_CYCLE_S", "8"))
TAPE_REFRESH_S = 60

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

    def open_map(self):
        """All my open orders in one call: id -> shares matched so far."""
        return {o["id"]: float(o.get("size_matched") or 0) for o in (self.c.get_orders() or []) if o.get("id")}

    def scoring(self, oids):
        if not oids: return {}
        r = self.c.are_orders_scoring(self.OSP(orderIds=oids))
        return r if isinstance(r, dict) else {}

    def earnings(self, day):
        path = "/rewards/user/total"
        h = self.l2h(self.c.signer, self.c.creds, self.RA(method="GET", request_path=path))
        r = requests.get(CLOB + path, headers=h, params={"date": day, "maker_address": self.funder}, timeout=20)
        return r.json() if r.ok else {"error": r.status_code}

class LiveSDK:
    """Adapter for Polymarket's unified client (package polymarket-client). Required for accounts created
    after May 2026, which hold funds in a Deposit Wallet (wallet type 3). Same interface as Live."""
    def __init__(self):
        from polymarket import SecureClient
        w = os.environ.get("PM_FUNDER") or None
        self.k = SecureClient.create(private_key=os.environ["PM_PRIVATE_KEY"], wallet=w)
        self.funder = str(self.k.wallet)
        outer = self
        class _HB:      # start_heartbeat() talks to .c.post_heartbeat(id)
            def post_heartbeat(self, hid):
                return {"heartbeat_id": outer.k.send_order_heartbeat(heartbeat_id=hid or "").heartbeat_id}
        self.c = _HB()

    def usdc(self):
        return float(self.k.get_balance_allowance(asset_type="COLLATERAL").balance) / 1e6

    def place(self, token, price, size, tick, sell=False):
        dec = max(0, -int(round(__import__("math").log10(tick))))
        size = __import__("math").floor(float(size) * 100) / 100
        r = self.k.place_limit_order(token_id=str(token), price=f"{price:.{dec}f}", size=f"{size:.2f}",
                                     side="SELL" if sell else "BUY", post_only=True)
        if getattr(r, "ok", False): return str(r.order_id)
        raise RuntimeError(f"rejected: {getattr(r, 'code', '')} {getattr(r, 'message', '')}")

    def stop_sell(self, token, price, size, tick):
        dec = max(0, -int(round(__import__("math").log10(tick))))
        size = __import__("math").floor(float(size) * 100) / 100
        return self.k.place_market_order(token_id=str(token), side="SELL", shares=f"{size:.2f}",
                                         min_price=f"{max(price, tick):.{dec}f}", order_type="FAK")

    def cancel(self, oid): self.k.cancel_order(order_id=oid)
    def cancel_all(self): self.k.cancel_all()

    def open_map(self):
        return {str(o.id): float(o.size_matched) for o in self.k.list_open_orders().iter_items()}

    def matched(self, oid):
        try:
            o = self.k.get_order(order_id=oid)
            return float(o.size_matched), str(o.status)
        except Exception:
            return -1.0, "GONE"          # no longer open: holdings are re-read from positions

    def scoring(self, oids):
        return {str(k): bool(v) for k, v in self.k.get_orders_scoring(order_ids=list(oids)).items()} if oids else {}

    def earnings(self, day):
        return {"earnings": float(sum(e.earnings for e in self.k.get_total_earnings_for_user_for_day(date=day)))}

    def positions(self):
        return {str(p.asset_id): float(p.current_size) for p in self.k.list_positions(user=self.funder).iter_items()}

    def merge(self, cid):
        return self.k.merge_positions(condition_id=cid, amount="max")

def retry(fn, what, tries=6):
    """Network calls at startup can be cut by the connection; try again a few times before giving up."""
    for i in range(tries):
        try: return fn()
        except Exception as e:
            log(ev="retry", what=what, attempt=i + 1, err=f"{type(e).__name__}: {str(e)[:160]}")
            if i == tries - 1: raise
            time.sleep(5 * (i + 1))

def make_client():
    """Deposit Wallet accounts (PM_SIG_TYPE=3) use the unified client; older wallet types use py-clob-client."""
    return LiveSDK() if os.environ.get("PM_SIG_TYPE", "1") == "3" else Live()

def ext_book(b, mine):
    """The order book without my own resting orders, so my quotes never anchor themselves.
    mine: my live orders in this market (slot 'Y' rests in the YES bids at price; slot 'N' rests in the YES asks at 1 - price)."""
    bids = {round(float(l["price"]), 6): float(l["size"]) for l in b["bids"]}
    asks = {round(float(l["price"]), 6): float(l["size"]) for l in b["asks"]}
    for sd, o in mine:
        rem = o["size"] - o["matched"]
        side, px = (bids, round(o["price"], 6)) if sd == "Y" else (asks, round(1 - o["price"], 6))
        if px in side:
            side[px] -= rem
            if side[px] <= 1e-6: del side[px]
    return {"bids": [{"price": str(p), "size": str(z)} for p, z in bids.items()],
            "asks": [{"price": str(p), "size": str(z)} for p, z in asks.items()]}

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
    L = retry(make_client, "login")
    getpos = getattr(L, "positions", None) or (lambda: positions(L.funder))
    usdc = retry(L.usdc, "balance"); cap = min(MAX_TOTAL, usdc)
    log(ev="start", dry=DRY, usdc=round(usdc, 2), cap=cap, per_market=CFG["cap"], n=CFG["n_markets"])
    if cap < max(CFG["cap"], 20): log(ev="abort", why=f"USDC ${usdc:.2f} is below one market's size ${CFG['cap']:.0f}"); return
    if not DRY: retry(L.cancel_all, "cancel old orders")
    keep_awake(True)
    def load_markets(old=()):
        new = bot.select()
        if not new: return list(old)
        pos = retry(getpos, "read holdings"); keep = {m.cid: m for m in old}
        out = []
        for m in new:
            if m.cid in keep: keep[m.cid].exit_only = False; out.append(keep[m.cid]); continue     # keep running state (inventory, cash, pause)
            m.yes = pos.get(str(m.tok), 0.0); m.no = pos.get(str(m.tno), 0.0)
            s = min(m.yes, m.no); m.yes -= s; m.no -= s
            m.cash = -(m.yes + m.no) * 0.5                          # rough carry-in cost; baseline below removes it
            out.append(m)
        # Never orphan a position: any market where I hold 5+ shares stays managed (exit orders only),
        # even when it has dropped out of the ranking.
        try:
            have = {m.cid for m in out}
            for r in json.load(open("data/edges/pm_rewards.json", encoding="utf-8")):
                if r.get("cid") in have or not r.get("tok") or not r.get("tno"): continue
                y, n = pos.get(str(r["tok"]), 0.0), pos.get(str(r["tno"]), 0.0)
                if max(y, n) < 5: continue
                m = keep.get(r["cid"]) or bot.Mkt(r)
                if r["cid"] not in keep:
                    s2 = min(y, n); m.yes, m.no = y - s2, n - s2; m.cash = -(m.yes + m.no) * 0.5
                m.exit_only = True; out.append(m); have.add(r["cid"])
        except Exception as e: log(ev="held_markets_err", err=str(e)[:200])
        log(ev="markets", markets=[m.q[:50] for m in out], held={m.q[:30]: (m.yes, m.no) for m in out if m.yes or m.no})
        return out
    mk = load_markets()
    if not mk: log(ev="abort", why="no market passed the filters"); return
    live = {}; errors = 0; t0 = time.time(); last_score = 0; scoring_frac = "n/a"; last_day = None
    last_select = t0; baseline = None; retired_pnl = 0.0; hb_flag = {"stop": False}; tapes = {}; cycles = 0; last_pos = t0; resync = False
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
            bk = {}
            for i in range(0, len(mk), 200):
                for b in (http("POST", f"{CLOB}/books", json=[{"token_id": m.tok} for m in mk[i:i + 200]]) or []): bk[b.get("asset_id")] = b
            # holdings: re-read real positions every 2 minutes (and at once when an order vanished), so the
            # bot's count can never drift from the account. Markets with a fill in the last minute are skipped
            # because the positions feed lags a few seconds.
            if not DRY and cycles >= 1 and (resync or now - last_pos > 300):
                try:
                    pos = getpos(); last_pos = now; resync = False
                    for m in mk:
                        if now - getattr(m, "last_fill", 0) < 60: continue
                        y, n = pos.get(str(m.tok), 0.0), pos.get(str(m.tno), 0.0); s2 = min(y, n)
                        if abs((y - s2) - m.yes) > 0.5 or abs((n - s2) - m.no) > 0.5:
                            log(ev="positions_resync", q=m.q[:50], yes=round(y - s2, 2), no=round(n - s2, 2), was=(round(m.yes, 2), round(m.no, 2)))
                        m.yes, m.no = y - s2, n - s2
                except Exception as e: log(ev="positions_err", err=str(e)[:150])
            # my open orders in one call (falls back to per-order checks if it fails)
            omap = None
            if not DRY and live:
                try: omap = L.open_map()
                except Exception as e: log(ev="open_orders_err", err=str(e)[:150])
            # trade tape for markets whose book is wide (needed to anchor the price); refreshed at most once a minute
            need = [m for m in mk if getattr(m, "wide", True) and now - tapes.get(m.cid, (0, []))[0] > TAPE_REFRESH_S]
            if need:
                def tape(m):
                    tr = http("GET", f"{DATA}/v2/trades", params={"condition_id": m.cid, "limit": 100})
                    return (tr or {}).get("data", []) if isinstance(tr, dict) else (tr or [])
                with ThreadPoolExecutor(8) as ex:
                    for m, tr in zip(need, ex.map(tape, need)): tapes[m.cid] = (now, tr)
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
                        if omap is not None and o["oid"] in omap: got, status = omap[o["oid"]], "LIVE"
                        else: got, status = L.matched(o["oid"])
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
                                if sd == "Y": m.fill_y = now
                                else: m.fill_n = now
                            m.last_fill = now
                        if status.upper() not in ("LIVE", "OPEN", ""):
                            live.pop((m.cid, sd), None)
                            if status.upper() == "GONE": resync = True
                    s = min(m.yes, m.no)
                    if s > 0:
                        m.yes -= s; m.no -= s; m.merged += s; m.cash += s; log(ev="pair_locked", q=m.q[:50], sets=round(s, 2))
                        if s >= 1 and hasattr(L, "merge") and not DRY:      # turn the YES+NO pair back into USDC
                            try: L.merge(m.cid); log(ev="merged", q=m.q[:50])
                            except Exception as e: log(ev="merge_err", q=m.q[:50], err=str(e)[:150])
                    # 2) decide (on the book without my own orders)
                    b = ext_book(b, [(sd, live[(m.cid, sd)]) for sd in ("Y", "N") if (m.cid, sd) in live])
                    mid = fair_mid(m, b, tapes.get(m.cid, (0, []))[1], now) if b["bids"] and b["asks"] else None
                    if mid is None:        # nothing to anchor on: stay out of this market
                        for sd in ("Y", "N"):
                            o = live.pop((m.cid, sd), None)
                            if o and not DRY: L.cancel(o["oid"])
                        continue
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
                        # a fill means someone thought my price was too high: wait before bidding that side again
                        fy = now - getattr(m, "fill_y", 0) >= REBID_WAIT_S; fn = now - getattr(m, "fill_n", 0) >= REBID_WAIT_S
                        xo = getattr(m, "exit_only", False)      # held-but-unranked market: sell what I hold, buy nothing
                        if yb is not None and fy and not xo and imb < CFG["max_imb"] * n: want["Y"] = (yb, n, False)
                        if nb is not None and fn and not xo and -imb < CFG["max_imb"] * n: want["N"] = (nb, n, False)
                        # exit orders: holding YES -> the NO slot becomes SELL YES at the same book level (1 - nb0)
                        # exits never sell below what the shares cost (plus one tick)
                        if m.yes >= 5: want["N"] = (min(nb0, round(1 - (getattr(m, "avg_y", None) or 0) - m.tick, 6)), m.yes, True)
                        if m.no >= 5: want["Y"] = (min(yb0, round(1 - (getattr(m, "avg_n", None) or 0) - m.tick, 6)), m.no, True)
                    # small balances: open a market only if BOTH sides fit under the cap (one side alone earns far less)
                    if not live.get((m.cid, "Y")) and not live.get((m.cid, "N")):
                        need = sum(w[0] * w[1] for w in want.values() if w and not w[2])
                        if need and exposure + need > cap: want = {"Y": want["Y"] if want["Y"] and want["Y"][2] else None,
                                                                    "N": want["N"] if want["N"] and want["N"][2] else None}
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
                    scoring_frac = f"{k}/{len(ids)}"
                    names = {m.cid: m.q[:40] for m in mk}
                    log(ev="scoring", scoring=scoring_frac,
                        not_scoring=[f"{names.get(c, c[:8])} {sd} {'sell' if o.get('sell') else 'bid'} {o.get('real_px', o['price'])}"
                                     for (c, sd), o in live.items() if not sc.get(o["oid"])])
                    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
                    if day != last_day: last_day = day; log(ev="earnings", day=day, result=L.earnings(day))
                except Exception as e: log(ev="scoring_err", err=str(e)[:200])
            # loss stop (trading P&L since start; rewards are paid separately and not counted here)
            cycles += 1
            if cycles >= 2:
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
