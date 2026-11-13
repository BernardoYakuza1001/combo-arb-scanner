"""MEXC same-exchange carry: forward PAPER test (public data only, never trades).

Rules (fixed 13 Nov 2026, before the first run):
  Universe  MEXC USDT perps with a tradable USDT spot pair, 24h spot volume >= $100k and
            24h perp turnover >= $1M (liquidity filter; the backtest had none).
  Select    every 30 days, top K=3 by trailing-30-day settled funding (the "f30" rule, best in the
            MEXC study: net 29.4% APR on hedged notional, rank corr 0.68).
  Position  per coin: long spot + short perp, same quantity, equal weight. Capital $1,000:
            50% buys spot, 50% sits as perp margin (spot and futures are separate accounts on MEXC),
            i.e. 1x short leverage.
  Prices    entries/exits walk the real spot order book (buy at asks / sell at bids) and use the
            perp's best bid/ask; fees spot taker 0.10%, perp taker 0.055% (conservative).
  Funding   accrued from MEXC's settled funding history on the short: qty * perp price * rate.
  Stops     STOP if equity drawdown from start >= 5%; warn if any perp's unrealised loss
            exceeds 60% of its margin; close a coin early if its trailing-7-day funding turns negative.
State in data/mexc_paper/state.json; a report per run in data/mexc_paper/report_<date>.md.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

CFG = {"CAPITAL": 1000.0, "SPOT_FRAC": 0.5, "K": 3, "ROTATE_DAYS": 30, "MAX_DD": 0.05,
       "SPOT_FEE": 0.0010, "PERP_FEE": 0.00055, "MIN_SPOT_VOL": 100_000, "MIN_PERP_TURNOVER": 1_000_000,
       "MARGIN_WARN": 0.6, "NEG7_EXIT": True}
VARIANT = os.environ.get("PAPER_VARIANT", "v1")
if VARIANT == "v2":
    # v2 (fixed 13 Nov 2026 after the cost study): no liquidity filter, keep a coin while it is
    # still top-10 by f30 at each 30-day review, skip coins whose live round trip costs > 3%.
    CFG.update(MIN_SPOT_VOL=0, MIN_PERP_TURNOVER=0, HOLD_TOP=10, MAX_RT_COST=0.03)
SPOT = "https://api.mexc.com/api/v3"
FUT = "https://contract.mexc.com/api/v1/contract"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data", "mexc_paper" + ("" if VARIANT == "v1" else "_" + VARIANT))
DAY = 86_400_000


class Api:
    def __init__(self):
        self.s = requests.Session()

    def get(self, url, params=None):
        for a in range(6):
            try:
                r = self.s.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 * (a + 1)); continue
                r.raise_for_status()
                return r.json()
            except Exception:
                time.sleep(1 + a)
        return None

    # --- market data ---
    def perps(self):
        return [x for x in (self.get(f"{FUT}/detail") or {}).get("data", [])
                if x.get("quoteCoin") == "USDT" and x.get("state") == 0]

    def spot_symbols(self):
        return {x["baseAsset"]: x["symbol"] for x in (self.get(f"{SPOT}/exchangeInfo") or {}).get("symbols", [])
                if x.get("quoteAsset") == "USDT" and str(x.get("status")) in ("1", "ENABLED", "TRADING")}

    def spot_24h(self):
        return {x["symbol"]: float(x.get("quoteVolume") or 0) for x in (self.get(f"{SPOT}/ticker/24hr") or [])}

    def perp_tickers(self):
        return {x["symbol"]: x for x in (self.get(f"{FUT}/ticker") or {}).get("data", [])}

    def funding(self, sym, since_ms):
        rows, page = [], 1
        while True:
            d = (self.get(f"{FUT}/funding_rate/history", {"symbol": sym, "page_size": 1000, "page_num": page}) or {}).get("data") or {}
            lst = d.get("resultList") or []
            rows += [(int(x["settleTime"]), float(x["fundingRate"])) for x in lst if int(x["settleTime"]) > since_ms]
            if page >= int(d.get("totalPage") or 0) or (lst and min(int(x["settleTime"]) for x in lst) <= since_ms):
                break
            page += 1
        return sorted(rows)

    def spot_book(self, spot_sym):
        b = self.get(f"{SPOT}/depth", {"symbol": spot_sym, "limit": 100}) or {}
        return ([(float(p), float(q)) for p, q in b.get("asks", [])], [(float(p), float(q)) for p, q in b.get("bids", [])])


def walk(levels, qty=None, usd=None):
    """Fill against levels [(price, size)] either a quantity or a USD amount; returns (qty, avg_price)."""
    got = cost = 0.0
    for p, s in levels:
        take = s
        if qty is not None:
            take = min(s, qty - got)
        elif usd is not None:
            take = min(s, (usd - cost) / p)
        if take <= 0:
            break
        got += take; cost += take * p
        if (qty is not None and got >= qty - 1e-12) or (usd is not None and cost >= usd - 1e-9):
            break
    return got, (cost / got if got else 0.0)


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def select(api, log):
    perps = api.perps(); spot = api.spot_symbols(); vol = api.spot_24h(); tk = api.perp_tickers()
    cands = []
    for p in perps:
        base, sym = p.get("baseCoin"), p["symbol"]
        if base not in spot:
            continue
        t = tk.get(sym) or {}
        if vol.get(spot[base], 0) < CFG["MIN_SPOT_VOL"] or float(t.get("amount24") or 0) < CFG["MIN_PERP_TURNOVER"]:
            continue
        cands.append((sym, spot[base]))
    log(f"[{VARIANT}] universe after liquidity filter: {len(cands)}")
    since = now_ms() - 30 * DAY
    scored = []
    for i, (sym, ssym) in enumerate(cands, 1):
        f = api.funding(sym, since)
        if f and f[0][0] < since + 3 * DAY:          # needs (almost) a full 30-day history
            scored.append((sum(r for _, r in f), sym, ssym))
        if i % 100 == 0:
            log(f"funding scored {i}/{len(cands)}")
    scored.sort(reverse=True)
    return scored


def equity(st, api):
    tk = api.perp_tickers()
    eq = st["cash"]
    for p in st["positions"]:
        asks, bids = api.spot_book(p["spot"])
        _, sell_px = walk(bids, qty=p["qty"])
        t = tk.get(p["symbol"]) or {}
        ask = float(t.get("ask1") or t.get("lastPrice") or p["entry_perp"])
        p["mark_spot"], p["mark_perp"] = sell_px or p.get("mark_spot", p["entry_spot"]), ask
        p["perp_upnl"] = p["qty"] * (p["entry_perp"] - ask)
        eq += p["qty"] * p["mark_spot"] + p["margin"] + p["perp_upnl"]
    return eq


def open_pos(st, api, scored, n, log):
    tk = api.perp_tickers()
    per = CFG["CAPITAL"] * CFG["SPOT_FRAC"] / CFG["K"]
    held = {p["symbol"] for p in st["positions"]}
    for f30, sym, ssym in scored:
        if len(st["positions"]) >= CFG["K"] or n <= 0:
            break
        if sym in held or f30 <= 0:
            continue
        asks, _ = api.spot_book(ssym)
        qty, spx = walk(asks, usd=per)
        bid = float((tk.get(sym) or {}).get("bid1") or 0)
        if qty <= 0 or bid <= 0 or qty * spx < per * 0.95:
            log(f"skip {sym}: thin book"); continue
        if CFG.get("MAX_RT_COST"):
            _, sell_back = walk(api.spot_book(ssym)[1], qty=qty)
            ask = float((tk.get(sym) or {}).get("ask1") or 0)
            rt = ((spx - sell_back) / spx if sell_back else 1) + ((ask - bid) / bid if ask else 1) \
                + 2 * (CFG["SPOT_FEE"] + CFG["PERP_FEE"])
            if rt > CFG["MAX_RT_COST"]:
                log(f"skip {sym}: round trip {rt:.2%} > {CFG['MAX_RT_COST']:.0%}"); continue
        fees = qty * spx * CFG["SPOT_FEE"] + qty * bid * CFG["PERP_FEE"]
        margin = per
        st["cash"] -= qty * spx + margin + fees
        st["fees"] += fees
        st["positions"].append({"symbol": sym, "spot": ssym, "qty": qty, "entry_spot": spx, "entry_perp": bid,
                                "margin": margin, "opened": now_ms(), "funding_ms": now_ms(), "f30_at_entry": f30,
                                "funding": 0.0})
        st["log"].append(f"{iso(now_ms())} OPEN {sym} qty {qty:.4f} spot {spx:.6g} perp {bid:.6g} (f30 {f30:.2%})")
        n -= 1


def close_pos(st, api, p, why):
    asks, bids = api.spot_book(p["spot"])
    _, spx = walk(bids, qty=p["qty"])
    ask = float((api.perp_tickers().get(p["symbol"]) or {}).get("ask1") or p["entry_perp"])
    spx = spx or p.get("mark_spot", p["entry_spot"])
    fees = p["qty"] * spx * CFG["SPOT_FEE"] + p["qty"] * ask * CFG["PERP_FEE"]
    pnl_perp = p["qty"] * (p["entry_perp"] - ask)
    st["cash"] += p["qty"] * spx + p["margin"] + pnl_perp - fees
    st["fees"] += fees
    st["positions"].remove(p)
    st["log"].append(f"{iso(now_ms())} CLOSE {p['symbol']} ({why}) spot {spx:.6g} perp {ask:.6g} "
                     f"funding ${p['funding']:.2f} basis ${p['qty'] * (spx - p['entry_spot']) + pnl_perp:.2f}")


def accrue(st, api):
    tk = api.perp_tickers()
    for p in st["positions"]:
        px = float((tk.get(p["symbol"]) or {}).get("fairPrice") or (tk.get(p["symbol"]) or {}).get("lastPrice") or p["entry_perp"])
        rows = api.funding(p["symbol"], p["funding_ms"])
        amt = sum(p["qty"] * px * r for _, r in rows)          # short receives when the rate is positive
        if rows:
            p["funding_ms"] = rows[-1][0]
        p["funding"] += amt; st["funding"] += amt; st["cash"] += amt
        p["neg7"] = sum(r for _, r in api.funding(p["symbol"], now_ms() - 7 * DAY)) < 0


def write_ticket(st, api, actions_log, capital):
    """Plain order ticket for a human, scaled to `capital` USDT. Never sends anything."""
    k = capital / CFG["CAPITAL"]
    cs = {p["symbol"]: float(p.get("contractSize") or 1) for p in api.perps()}
    tk = api.perp_tickers()
    L = [f"# MEXC carry — ORDER TICKET for ${capital:,.0f} ({iso(now_ms())} UTC)", "",
         "You place these orders yourself in the MEXC app. Spot and futures are separate accounts:",
         f"put about ${capital * CFG['SPOT_FRAC']:,.0f} in Spot and ${capital * (1 - CFG['SPOT_FRAC']):,.0f} in Futures (USDT-M).",
         "Futures settings for every coin: **Isolated margin, 1x leverage, Short**.", ""]
    if actions_log:
        L += ["## Do today", ""]
        for x in actions_log:
            if " OPEN " in x:
                sym = x.split(" OPEN ")[1].split()[0]
                p = next((q for q in st["positions"] if q["symbol"] == sym), None)
                if not p:
                    continue
                contracts = int(p["qty"] * k / cs.get(sym, 1))
                qty = contracts * cs.get(sym, 1)          # spot matches the short exactly
                L += [f"- **OPEN {sym}**: Spot BUY {qty:,.4f} {sym[:-5]} with a limit at most "
                      f"{p['entry_spot'] * 1.005:.6g} USDT  |  Futures SHORT {contracts:,} contracts "
                      f"({contracts * cs.get(sym, 1):,.4f} {sym[:-5]}), limit at least {p['entry_perp'] * 0.995:.6g}"]
            elif " CLOSE " in x:
                sym = x.split(" CLOSE ")[1].split()[0]
                L += [f"- **CLOSE {sym}**: sell ALL your spot {sym[:-5]} and close ALL of the {sym} short "
                      f"(reason: {x.split('(')[1].split(')')[0] if '(' in x else 'rule'})"]
        L.append("")
    else:
        L += ["## Do today: nothing. Keep holding.", ""]
    L += ["## What you should be holding", "",
          "Starting fresh? Open exactly this table (spot buy + futures short per coin), at the current market.", "",
          "| Coin | Spot qty | Short contracts | Margin | Mark (spot / perp) |", "|---|---|---|---|---|"]
    for p in st["positions"]:
        c = int(p["qty"] * k / cs.get(p["symbol"], 1))
        L.append(f"| {p['symbol'][:-5]} | {c * cs.get(p['symbol'], 1):,.4f} | {c:,} | ${p['margin'] * k:,.0f} | "
                 f"{p.get('mark_spot', p['entry_spot']):.6g} / {p.get('mark_perp', p['entry_perp']):.6g} |")
    L += ["", "## Safety rules (check daily)",
          "- Close a coin if the ticket says so (its 7-day funding turned negative).",
          "- Close EVERYTHING if total equity is down 5% from what you started with.",
          "- If a short is ever near liquidation (MEXC shows the price), add margin or close both legs.",
          "- Never use leverage above 1x on the short."]
    rep = "\n".join(L) + "\n"
    open(os.path.join(OUT, "TICKET.md"), "w").write(rep)
    return rep


def main(log=print):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "state.json")
    api = Api()
    st = json.load(open(path)) if os.path.exists(path) else None
    actions = []
    if st is not None:
        st["_log_len_before"] = len(st["log"])
    if st is None:
        st = {"start": now_ms(), "cash": CFG["CAPITAL"], "positions": [], "fees": 0.0, "funding": 0.0,
              "last_rotation": 0, "peak": CFG["CAPITAL"], "history": [], "log": [], "stopped": False, "rules": CFG,
              "_log_len_before": 0}
        actions.append("start")
    if not st["stopped"]:
        accrue(st, api)
        rotate = now_ms() - st["last_rotation"] >= CFG["ROTATE_DAYS"] * DAY
        exits = [p for p in st["positions"] if CFG["NEG7_EXIT"] and p.get("neg7")]
        if rotate or exits or len(st["positions"]) < CFG["K"]:
            scored = select(api, log)
            if rotate:
                keep = {s for _, s, _ in scored[:CFG.get("HOLD_TOP", CFG["K"])]}
                for p in list(st["positions"]):
                    if p["symbol"] not in keep:
                        close_pos(st, api, p, "rotation")
                st["last_rotation"] = now_ms()
                actions.append("rotation")
            for p in [p for p in st["positions"] if p.get("neg7")]:
                close_pos(st, api, p, "7-day funding negative"); actions.append(f"exit {p['symbol']}")
            open_pos(st, api, scored, CFG["K"] - len(st["positions"]), log)
    eq = equity(st, api)
    st["peak"] = max(st["peak"], eq)
    dd = 1 - eq / CFG["CAPITAL"]
    if dd >= CFG["MAX_DD"] and not st["stopped"]:
        for p in list(st["positions"]):
            close_pos(st, api, p, "STOP 5% drawdown")
        st["stopped"] = True; actions.append("STOP")
        eq = st["cash"]
    warn = [p["symbol"] for p in st["positions"] if -p.get("perp_upnl", 0) > CFG["MARGIN_WARN"] * p["margin"]]
    days = (now_ms() - st["start"]) / DAY
    st["history"].append({"t": iso(now_ms()), "equity": round(eq, 2), "funding": round(st["funding"], 2),
                          "fees": round(st["fees"], 2), "dd_from_start": round(dd, 4)})
    json.dump(st, open(path, "w"), indent=1)
    ann = (eq / CFG["CAPITAL"] - 1) * 365 / days if days >= 1 else None
    L = [f"# MEXC paper carry {VARIANT} — {iso(now_ms())} UTC (day {days:.1f})", "",
         f"- Equity **${eq:,.2f}** (start $1,000) | funding ${st['funding']:.2f} | fees ${st['fees']:.2f}"
         + (f" | annualised {ann:.1%}" if ann is not None else ""),
         f"- Drawdown from start {dd:.2%} (stop at 5%) {'-> STOPPED' if st['stopped'] else '-> ok'}"
         + (f" | MARGIN WARNING: {', '.join(warn)}" if warn else ""),
         f"- Actions this run: {', '.join(actions) or 'none'}", "",
         "| coin | qty | entry spot | mark spot | entry perp | mark perp | funding | f30 at entry |", "|---|---|---|---|---|---|---|---|"]
    for p in st["positions"]:
        L.append(f"| {p['symbol']} | {p['qty']:.4f} | {p['entry_spot']:.6g} | {p.get('mark_spot', 0):.6g} | {p['entry_perp']:.6g} | "
                 f"{p.get('mark_perp', 0):.6g} | ${p['funding']:.2f} | {p['f30_at_entry']:.2%} |")
    L += ["", "Recent log:", ""] + [f"- {x}" for x in st["log"][-10:]]
    if VARIANT == "v2":
        n0 = st.get("_log_len_before", 0)
        log(write_ticket(st, api, st["log"][n0:], float(os.environ.get("LIVE_CAPITAL", "1000"))))
    st.pop("_log_len_before", None)
    json.dump(st, open(path, "w"), indent=1)
    rep = "\n".join(L) + "\n"
    open(os.path.join(OUT, f"report_{datetime.now(timezone.utc):%Y-%m-%d}.md"), "w").write(rep)
    open(os.path.join(OUT, "LATEST.md"), "w").write(rep)
    log(rep)


if __name__ == "__main__":
    sys.exit(main())
