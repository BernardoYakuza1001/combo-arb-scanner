"""History mode: test the edge on the public trade tape of recently SETTLED events.

For every guaranteed basket (same relation engine as live mode), merge the legs' trade tapes
(data-api.polymarket.com/trades). Two questions, answered from real fills only:

1. Tape gaps: moments when every leg had a taker BUY print (= an ask that was really there)
   within W seconds of each other and the summed cost incl. taker fees was <= 1 - min_edge.
2. Captured arbs: one wallet buying every leg of a basket within W seconds at a summed cost < 1.
   That is someone actually taking the edge; their profit = (1 - cost) * matched size.
"""
from __future__ import annotations

import json
import os
import statistics as st
import time
from collections import defaultdict
from datetime import datetime, timezone

from . import relations as R
from .collector import GAMMA, Api
from .pricing import fee

DATA = "https://data-api.polymarket.com"


def parse_ts(x):
    """'2026-11-13 11:00:00+00' or '2026-11-13T11:00:00Z' -> unix seconds, else None."""
    if not x:
        return None
    t = str(x).strip().replace(" ", "T").replace("Z", "+00:00")
    if t.endswith("+00"):
        t += ":00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def closed_events(api, tags, pages, days, log):
    t_min = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen, out = set(), []
    for tag in tags:
        for p in range(pages):
            page = api.req("GET", f"{GAMMA}/events", params={
                "closed": "true", "tag_slug": tag, "limit": 100, "offset": 100 * p,
                "order": "endDate", "ascending": "false", "end_date_min": t_min})
            if not page:
                break
            for e in page:
                if e.get("id") not in seen:
                    seen.add(e.get("id")); out.append(e)
            log(f"closed events: {tag} page {p + 1}: {len(page)} (total {len(out)})")
            if len(page) < 100:
                break
    return out


def trades_for(api, condition_id, cap):
    rows, off = [], 0
    while off < cap:
        page = api.req("GET", f"{DATA}/trades", params={"market": condition_id, "limit": 500, "offset": off,
                                                         "takerOnly": "true"})
        if not page:
            break
        rows += page
        if len(page) < 500:
            break
        off += 500
    return rows


def analyse(baskets, tape, W, min_edge, start_of):
    """tape: token -> list of (ts, price, size, side, wallet). Returns (gaps, captures)."""
    gaps, caps = [], []
    for b in baskets:
        legs = b["legs"]
        buys = [sorted((t for t in tape.get(c.token, []) if t[3] == "BUY"), key=lambda x: x[0]) for c in legs]
        if any(not x for x in buys):
            continue
        # 1) tape gaps: anchor on each print of leg 0, cheapest print of every other leg within +-W
        last_end = -1
        for t0, p0, s0, _, _ in buys[0]:
            cost, size, ok = p0 + fee(p0, legs[0].fee_rate), s0, True
            for i in range(1, len(legs)):
                cand = [x for x in buys[i] if abs(x[0] - t0) <= W]
                if not cand:
                    ok = False; break
                x = min(cand, key=lambda y: y[1])
                cost += x[1] + fee(x[1], legs[i].fee_rate); size = min(size, x[2])
            if ok and cost <= 1 - min_edge and t0 > last_end:
                gaps.append({"basket": b["id"], "kind": b["kind"], "title": b["title"], "ts": t0,
                             "edge": round(1 - cost, 4), "size": round(size, 2),
                             "profit": round((1 - cost) * size, 2),
                             "in_play": bool(start_of.get(b["event"]) and t0 >= start_of[b["event"]]),
                             "legs": [c.label for c in legs]})
                last_end = t0 + W
        # 2) captured arbs: same wallet bought every leg within W
        by_w = defaultdict(lambda: [[] for _ in legs])
        for i, lst in enumerate(buys):
            for x in lst:
                by_w[x[4]][i].append(x)
        for w, per in by_w.items():
            if any(not x for x in per):
                continue
            for x0 in per[0]:
                picks = [x0]
                for i in range(1, len(legs)):
                    near = [x for x in per[i] if abs(x[0] - x0[0]) <= W]
                    if not near:
                        break
                    picks.append(min(near, key=lambda y: abs(y[0] - x0[0])))
                if len(picks) != len(legs):
                    continue
                cost = sum(x[1] + fee(x[1], legs[i].fee_rate) for i, x in enumerate(picks))
                size = min(x[2] for x in picks)
                if cost < 1:
                    caps.append({"wallet": w, "kind": b["kind"], "title": b["title"], "ts": x0[0],
                                 "edge": round(1 - cost, 4), "size": round(size, 2),
                                 "profit": round((1 - cost) * size, 2), "legs": [c.label for c in legs]})
    # one capture can match several overlapping baskets: keep the best per (wallet, title, ts)
    best = {}
    for c in caps:
        k = (c["wallet"], c["title"], c["ts"])
        if k not in best or c["profit"] > best[k]["profit"]:
            best[k] = c
    return gaps, list(best.values())


def run(cfg, log):
    import requests
    R.ALLOW_CLOSED = True
    h = cfg.get("history", {})
    api = Api(requests.Session(), log, cfg["min_request_gap_s"])
    evs = closed_events(api, h.get("tags", ["sports", "esports"]), h.get("pages", 6), h.get("days", 14), log)
    fams, baskets, stats = R.discover(evs, cfg["max_legs"])
    log(f"history: {len(evs)} settled events, {len(fams)} families, {len(baskets)} baskets, skipped {stats}")
    by_market = {}
    for e in evs:
        for m in e.get("markets") or []:
            by_market[m.get("slug")] = m
    need = sorted({c.market for b in baskets for c in b["legs"]})[: h.get("max_markets", 2500)]
    tape = defaultdict(list)
    for i, slug in enumerate(need, 1):
        m = by_market.get(slug) or {}
        for t in trades_for(api, m.get("conditionId"), h.get("max_trades_per_market", 3000)) if m.get("conditionId") else []:
            try:
                tape[str(t["asset"])].append((int(t["timestamp"]), float(t["price"]), float(t["size"]),
                                              str(t.get("side", "")).upper(), t.get("proxyWallet", "")))
            except (KeyError, ValueError, TypeError):
                continue
        if i % 200 == 0:
            log(f"trades: {i}/{len(need)} markets, {sum(len(v) for v in tape.values())} prints")
    start_of = {}
    for e in evs:
        s0 = e.get("startTime") or next((m.get("gameStartTime") for m in e.get("markets") or [] if m.get("gameStartTime")), None)
        ts = parse_ts(s0)
        if ts:
            start_of[e.get("slug")] = ts
    gaps, caps = analyse(baskets, tape, h.get("window_s", 10), h.get("min_edge", 0.01), start_of)
    d = cfg["data_dir"]; os.makedirs(d, exist_ok=True)
    days = h.get("days", 14)
    res = {
        "settled_events": len(evs), "baskets": len(baskets), "markets_with_tape": len(need),
        "prints": sum(len(v) for v in tape.values()), "window_s": h.get("window_s", 10), "days": days,
        "tape_gaps": len(gaps), "tape_gap_events": len({g["title"] for g in gaps}),
        "tape_gap_profit_usd": round(sum(g["profit"] for g in gaps), 2),
        "tape_gap_median_edge": st.median([g["edge"] for g in gaps]) if gaps else None,
        "tape_gaps_in_play_share": round(sum(g["in_play"] for g in gaps) / len(gaps), 3) if gaps else None,
        "captures": len(caps), "capture_wallets": len({c["wallet"] for c in caps}),
        "capture_profit_usd": round(sum(c["profit"] for c in caps), 2),
        "capture_median_edge": st.median([c["edge"] for c in caps]) if caps else None,
        "by_kind": {},
        "top_wallets": sorted(({"wallet": w, "captures": sum(1 for c in caps if c["wallet"] == w),
                                "profit": round(sum(c["profit"] for c in caps if c["wallet"] == w), 2)}
                               for w in {c["wallet"] for c in caps}), key=lambda x: -x["profit"])[:15],
    }
    for g in gaps:
        k = res["by_kind"].setdefault(g["kind"], {"gaps": 0, "gap_profit": 0.0, "captures": 0, "capture_profit": 0.0})
        k["gaps"] += 1; k["gap_profit"] = round(k["gap_profit"] + g["profit"], 2)
    for c in caps:
        k = res["by_kind"].setdefault(c["kind"], {"gaps": 0, "gap_profit": 0.0, "captures": 0, "capture_profit": 0.0})
        k["captures"] += 1; k["capture_profit"] = round(k["capture_profit"] + c["profit"], 2)
    with open(os.path.join(d, "history_result.json"), "w") as f:
        json.dump(res, f, indent=1)
    with open(os.path.join(d, "history_gaps.jsonl"), "w") as f:
        for g in sorted(gaps, key=lambda g: -g["profit"]):
            f.write(json.dumps(g) + "\n")
    with open(os.path.join(d, "history_captures.jsonl"), "w") as f:
        for c in sorted(caps, key=lambda c: -c["profit"]):
            f.write(json.dumps(c) + "\n")
    log("HISTORY_RESULT " + json.dumps({k: v for k, v in res.items() if k != "top_wallets"}))
    for g in sorted(gaps, key=lambda g: -g["profit"])[:10]:
        log(f"gap {g['edge']:+.2%} ${g['profit']} size {g['size']} in-play {g['in_play']} | {g['title'][:50]} | {' + '.join(l[:45] for l in g['legs'])}")
    for w in res["top_wallets"][:10]:
        log(f"wallet {w['wallet']}: {w['captures']} captures, ${w['profit']}")
