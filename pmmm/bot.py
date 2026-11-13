"""PMMM: Polymarket liquidity-rewards market maker.

Quotes a resting BUY on YES and a BUY on NO in each selected market, inside the
reward max spread. It re-centres on the size-cutoff-adjusted midpoint, pauses a
market when its price jumps (news), stops bidding the side it already holds too
much of, and merges YES+NO pairs into $1. Each minute it scores rewards with
Polymarket's formula against the real book.

PAPER mode (default) runs against live books and the live trade tape: an order
fills when the tape prints through its price, or at its price after the queue
ahead of it has traded, or when the book crosses it. No orders are sent.
"""
import json, math, os, sys, time, datetime as dt
from collections import deque
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
OUT = os.environ.get("PMMM_OUT", "data/pmmm")
CFG = dict(
    n_markets=int(os.environ.get("PMMM_N", "12")),
    cap=float(os.environ.get("PMMM_CAP", "200")),         # $ per market (both bids)
    quote_frac=float(os.environ.get("PMMM_FRAC", "0.5")),  # distance from mid as share of max spread
    cycle_s=15, sample_s=60,
    max_imb=1.0,              # stop bidding a side once net inventory exceeds this many order sizes
    news_move=0.04, news_win_s=600, pause_s=1800,
    min_days=7,
    run_s=int(os.environ.get("PMMM_RUN_S", "3600")),
)
S = requests.Session(); S.headers["User-Agent"] = "pmmm/1.0"

def http(method, url, **kw):
    for i in range(5):
        try:
            r = S.request(method, url, timeout=20, **kw)
            if r.status_code == 429: time.sleep(2 + 2 * i); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            if i == 4: print("http err", url[:80], e, flush=True); return None
            time.sleep(1 + i)

# ---------------- pure logic (unit-tested) ----------------
def tsec(x):
    try:
        f = float(x); return f / 1000 if f > 1e12 else f
    except Exception:
        t = str(x).strip().replace(" ", "T").replace("Z", "+00:00")
        if t.endswith("+00"): t += ":00"
        try: return dt.datetime.fromisoformat(t).timestamp()
        except Exception: return None

def floor_tick(p, tick): return math.floor(p / tick + 1e-9) * tick
def adj_side(levels, minsz, best_first):
    """Price at which cumulative size first reaches minsz (size-cutoff-adjusted best)."""
    lv = sorted(((float(l["price"]), float(l["size"])) for l in levels), reverse=best_first)
    cum = 0.0
    for p, s in lv:
        cum += s
        if cum >= minsz: return p
    return lv[-1][0] if lv else None

def adj_mid(book, minsz):
    if not book or not book.get("bids") or not book.get("asks"): return None
    b = adj_side(book["bids"], minsz, True); a = adj_side(book["asks"], minsz, False)
    if b is None or a is None or b >= a: return None
    return (a + b) / 2

def score(dist_c, v, size):
    return ((v - dist_c) / v) ** 2 * size if 0 <= dist_c < v else 0.0

def qmin(q1, q2, mid, c=3.0):
    if 0.10 <= mid <= 0.90: return max(min(q1, q2), max(q1 / c, q2 / c))
    return min(q1, q2)

def book_q(book, mid, v):
    q1 = sum(score((mid - float(l["price"])) * 100, v, float(l["size"])) for l in book["bids"])
    q2 = sum(score((float(l["price"]) - mid) * 100, v, float(l["size"])) for l in book["asks"])
    return q1, q2

class Order:
    def __init__(self, side, price, size, ts, queue):
        self.side, self.price, self.size, self.rem, self.ts, self.queue = side, price, size, size, ts, queue

def level_size(levels, price):
    return sum(float(l["size"]) for l in levels if abs(float(l["price"]) - price) < 1e-9)

def apply_trade(o, yes_px, size, ts):
    """o.side 'Y' bids YES at o.price; 'N' bids NO at o.price. yes_px = trade price in YES terms.
    Returns filled shares."""
    if o.rem <= 0 or ts <= o.ts: return 0.0
    # in YES terms: Y bid at p fills on prints <= p ; N bid at q is a YES ask at 1-q, fills on prints >= 1-q
    if o.side == "Y": through, at = yes_px < o.price - 1e-9, abs(yes_px - o.price) < 1e-9
    else:
        k = 1 - o.price; through, at = yes_px > k + 1e-9, abs(yes_px - k) < 1e-9
    if through:
        f = min(o.rem, size)
    elif at:
        if o.queue >= size: o.queue -= size; return 0.0
        f = min(o.rem, size - o.queue); o.queue = 0.0
    else:
        return 0.0
    o.rem -= f
    return f

# ---------------- market state ----------------
class Mkt:
    def __init__(self, r):
        self.cid, self.q, self.slug = r["cid"], r["q"], r["slug"]
        self.tok, self.tno = r["tok"], r["tno"]
        self.v, self.minsz, self.rate = float(r["v"]), float(r["minsz"]), float(r["rate"])
        self.tick = float(r.get("tick") or 0.01)
        self.mids = deque(); self.paused_until = 0
        self.orders = {"Y": None, "N": None}
        self.yes = self.no = 0.0; self.cash = 0.0; self.merged = 0.0
        self.reward = 0.0; self.fills = []; self.seen = set(); self.samples = 0; self.scored = 0
        self.mid = None; self.share_sum = 0.0

    def mtm(self):
        m = self.mid if self.mid is not None else 0.5
        return self.cash + self.yes * m + self.no * (1 - m)

def target(m, book, mid):
    s = CFG["quote_frac"] * m.v / 100
    best_bid = max(float(l["price"]) for l in book["bids"]); best_ask = min(float(l["price"]) for l in book["asks"])
    yb = floor_tick(mid - s, m.tick)
    if mid - yb >= m.v / 100: yb += m.tick
    yb = min(yb, best_ask - m.tick)
    nb = floor_tick((1 - mid) - s, m.tick)
    if (1 - mid) - nb >= m.v / 100: nb += m.tick
    nb = min(nb, (1 - best_bid) - m.tick)
    if yb <= 0 or nb <= 0: return None
    n = max(m.minsz, math.floor(CFG["cap"] / (yb + nb)))
    return yb, nb, n

def step(m, book, trades, now):
    mid = adj_mid(book, m.minsz)
    if mid is None: return
    m.mid = mid
    m.mids.append((now, mid))
    while m.mids and now - m.mids[0][0] > CFG["news_win_s"]: m.mids.popleft()
    # fills from tape (before re-quoting)
    for t in trades:
        key = (t.get("transaction_hash") or t.get("transactionHash"), t.get("token_id"), t.get("price"), t.get("size"), t.get("timestamp"), t.get("proxy_wallet"))
        if key in m.seen: continue
        m.seen.add(key)
        try: px = float(t["price"]); sz = float(t["size"]); ts = tsec(t["timestamp"])
        except Exception: continue
        if ts is None: continue
        yes_px = px if t.get("token_id") == m.tok else 1 - px
        for sd in ("Y", "N"):
            o = m.orders[sd]
            if o:
                f = apply_trade(o, yes_px, sz, ts)
                if f: book_fill(m, o, f, now, "tape")
    # crossing book fills
    best_bid = max(float(l["price"]) for l in book["bids"]); best_ask = min(float(l["price"]) for l in book["asks"])
    o = m.orders["Y"]
    if o and o.rem > 0 and best_ask <= o.price + 1e-9:
        f = min(o.rem, sum(float(l["size"]) for l in book["asks"] if float(l["price"]) <= o.price + 1e-9)); o.rem -= f; book_fill(m, o, f, now, "cross")
    o = m.orders["N"]
    if o and o.rem > 0 and best_bid >= 1 - o.price - 1e-9:
        f = min(o.rem, sum(float(l["size"]) for l in book["bids"] if float(l["price"]) >= 1 - o.price - 1e-9)); o.rem -= f; book_fill(m, o, f, now, "cross")
    # merge pairs
    sets = min(m.yes, m.no)
    if sets > 0: m.yes -= sets; m.no -= sets; m.cash += sets; m.merged += sets
    # news pause
    if m.mids and max(x[1] for x in m.mids) - min(x[1] for x in m.mids) > CFG["news_move"]:
        if now >= m.paused_until: m.fills.append(dict(ts=now, kind="pause", mid=mid))
        m.paused_until = now + CFG["pause_s"]
    if now < m.paused_until:
        m.orders = {"Y": None, "N": None}; return
    tg = target(m, book, mid)
    if not tg: m.orders = {"Y": None, "N": None}; return
    yb, nb, n = tg
    imb = m.yes - m.no
    want = {"Y": yb if imb < CFG["max_imb"] * n else None, "N": nb if -imb < CFG["max_imb"] * n else None}
    for sd, px in want.items():
        o = m.orders[sd]
        if px is None: m.orders[sd] = None; continue
        if o and o.rem >= m.minsz:
            dist = (mid - o.price) if sd == "Y" else ((1 - mid) - o.price)
            if 0 < dist < m.v / 100 and abs(o.price - px) <= 0.01 + 1e-9 and \
               (o.price < best_ask if sd == "Y" else o.price < 1 - best_bid): continue   # keep queue spot
        lv = book["bids"] if sd == "Y" else book["asks"]
        q = level_size(lv, px if sd == "Y" else 1 - px)
        m.orders[sd] = Order(sd, px, n, now, q)

def book_fill(m, o, f, now, how):
    if o.side == "Y": m.yes += f
    else: m.no += f
    m.cash -= f * o.price
    m.fills.append(dict(ts=now, side=o.side, px=o.price, sz=round(f, 2), how=how))

def sample(m, book, now):
    if m.mid is None: return
    m.samples += 1
    q1, q2 = book_q(book, m.mid, m.v)
    others = qmin(q1, q2, m.mid)
    my1 = my2 = 0.0
    o = m.orders["Y"]
    if o and o.rem >= m.minsz: my1 = score((m.mid - o.price) * 100, m.v, o.rem)
    o = m.orders["N"]
    if o and o.rem >= m.minsz: my2 = score(((1 - o.price) - m.mid) * 100, m.v, o.rem)
    mine = qmin(my1, my2, m.mid)
    if mine <= 0: return
    sh = mine / (mine + others)
    m.scored += 1; m.share_sum += sh
    m.reward += m.rate / 1440 * sh

# ---------------- selection ----------------
def select():
    from edges import pm_rewards
    try: pm_rewards.main()
    except Exception as e: print("refresh failed, using last ranking:", e)
    rows = json.load(open("data/edges/pm_rewards.json"))
    cut = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=CFG["min_days"])).strftime("%Y-%m-%d")
    rows = [r for r in rows if r.get("status") == "open" and not r.get("empty_book") and r.get("tok") and r.get("tno") and r["end"] >= cut]
    def est(r):
        v = r["v"]; s = CFG["quote_frac"] * v; n = CFG["cap"] / (1 - 2 * s / 100)
        if n < r["minsz"]: return 0
        mine = ((v - s) / v) ** 2 * n
        return r["rate"] * mine / (mine + r["others"])
    rows.sort(key=lambda r: -est(r))
    return [Mkt(r) for r in rows[:CFG["n_markets"]]]

# ---------------- run ----------------
def report(mk, t0, now, final=False):
    os.makedirs(OUT, exist_ok=True)
    hrs = (now - t0) / 3600
    tot_r = sum(m.reward for m in mk); tot_p = sum(m.mtm() for m in mk)
    L = [f"# PMMM paper run — {dt.datetime.fromtimestamp(t0, dt.timezone.utc):%Y-%m-%d %H:%M} UTC, {hrs:.2f} h{' (final)' if final else ''}", "",
         f"{len(mk)} markets, ${CFG['cap']:.0f} each (${CFG['cap']*len(mk):,.0f} total), quotes at {CFG['quote_frac']:.0%} of max spread.", "",
         f"**Rewards accrued: ${tot_r:.2f}  |  Trading P&L (marked at mid): ${tot_p:+.2f}  |  Net: ${tot_r+tot_p:+.2f}**",
         f"Per day at this pace: rewards ${tot_r/max(hrs,1e-9)*24:.2f}, net ${(tot_r+tot_p)/max(hrs,1e-9)*24:+.2f}", "",
         "| Market | Pool $/d | Mid | Avg share | Scored min | Rewards $ | Fills | Merged sets | YES inv | NO inv | Trade P&L $ | Pauses |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in sorted(mk, key=lambda m: -(m.reward + m.mtm())):
        nf = sum(1 for f in m.fills if "side" in f); np_ = sum(1 for f in m.fills if f.get("kind") == "pause")
        avg = m.share_sum / m.scored if m.scored else 0
        L.append(f"| [{m.q[:60]}](https://polymarket.com/market/{m.slug}) | {m.rate:.0f} | {m.mid if m.mid is None else round(m.mid,3)} | {avg:.1%} | {m.scored}/{m.samples} | {m.reward:.2f} | {nf} | {m.merged:.0f} | {m.yes:.0f} | {m.no:.0f} | {m.mtm():+.2f} | {np_} |")
    open(f"{OUT}/SUMMARY.md", "w").write("\n".join(L) + "\n")
    json.dump([dict(q=m.q, cid=m.cid, reward=m.reward, mtm=m.mtm(), yes=m.yes, no=m.no, merged=m.merged, fills=m.fills[-200:]) for m in mk],
              open(f"{OUT}/state.json", "w"), indent=1)
    print("\n".join(L[:6]), flush=True)

def main():
    mk = select()
    print("markets:", [m.q[:40] for m in mk], flush=True)
    t0 = time.time(); last_sample = 0; last_rep = t0
    while time.time() - t0 < CFG["run_s"]:
        now = time.time()
        bk = http("POST", f"{CLOB}/books", json=[{"token_id": m.tok} for m in mk]) or []
        bk = {b.get("asset_id"): b for b in bk}
        do_sample = now - last_sample >= CFG["sample_s"]
        for m in mk:
            b = bk.get(m.tok)
            if not b or not b.get("bids") or not b.get("asks"): continue
            tr = http("GET", f"{DATA}/v2/trades", params={"condition_id": m.cid, "limit": 100})
            tr = (tr or {}).get("data", []) if isinstance(tr, dict) else (tr or [])
            step(m, b, tr, now)
            if do_sample: sample(m, b, now)
        if do_sample: last_sample = now
        if now - last_rep > 300: report(mk, t0, now); last_rep = now
        time.sleep(max(0, CFG["cycle_s"] - (time.time() - now)))
    report(mk, t0, time.time(), final=True)

if __name__ == "__main__":
    main()
