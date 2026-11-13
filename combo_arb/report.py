"""Summarise episodes and apply the pre-registered gate (REGISTRATION.md)."""
from __future__ import annotations

import json
import os
import statistics as st
from datetime import datetime


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def qualifies(e, g) -> bool:
    return (e["max_edge"] >= g["min_edge"] and e["max_profit"] >= g["min_profit_usd"]
            and e["seen_s"] >= g["min_seen_s"])


def summarise(eps, g, days: float) -> dict:
    q = [e for e in eps if qualifies(e, g)]
    by_kind = {}
    for e in eps:
        k = by_kind.setdefault(e["kind"], {"episodes": 0, "qualifying": 0, "profit_q": 0.0})
        k["episodes"] += 1
        if qualifies(e, g):
            k["qualifying"] += 1
            k["profit_q"] += e["max_profit"]
    durs = [e["seen_s"] for e in eps]
    res = {
        "days": round(days, 2), "episodes": len(eps), "qualifying": len(q),
        "qualifying_events": len({e["event"] for e in q}),
        "qualifying_profit_usd": round(sum(e["max_profit"] for e in q), 2),
        "weekly_profit_usd": round(sum(e["max_profit"] for e in q) * 7 / days, 2) if days else None,
        "median_seen_s_all": st.median(durs) if durs else None,
        "share_seen_ge_min": round(sum(1 for d in durs if d >= g["min_seen_s"]) / len(durs), 3) if durs else None,
        "median_edge_all": st.median([e["max_edge"] for e in eps]) if eps else None,
        "in_play_share": round(sum(1 for e in eps if e["in_play_at_start"]) / len(eps), 3) if eps else None,
        "by_kind": by_kind,
    }
    res["gate"] = {
        "enough_days": days >= g["min_days"],
        "episodes": len(q) >= g["min_qualifying"],
        "distinct_events": res["qualifying_events"] >= g["min_events"],
        "weekly_profit": (res["weekly_profit_usd"] or 0) >= g["min_weekly_profit_usd"],
    }
    res["verdict"] = ("PASS" if all(res["gate"].values()) else
                      "PENDING" if not res["gate"]["enough_days"] else "FAIL")
    return res


def write(cfg) -> dict:
    d = cfg["data_dir"]
    import glob
    eps = [e for fp in sorted(glob.glob(os.path.join(d, "episodes*.jsonl"))) for e in load(fp)]
    start_f = os.path.join(d, "started_at.txt")
    days = 0.0
    if os.path.exists(start_f):
        t0 = datetime.fromisoformat(open(start_f).read().strip())
        days = (datetime.now(t0.tzinfo) - t0).total_seconds() / 86400
    res = summarise(eps, cfg["gate"], days)
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    top = sorted((e for e in eps if qualifies(e, cfg["gate"])), key=lambda e: -e["max_profit"])[:15]
    L = [f"# Combinatorial arbitrage report — verdict: **{res['verdict']}**", "",
         f"Collected {res['days']} days. Episodes {res['episodes']}, qualifying {res['qualifying']} "
         f"across {res['qualifying_events']} events; qualifying profit ${res['qualifying_profit_usd']} "
         f"(~${res['weekly_profit_usd']}/week).", "",
         f"Median time seen open (all episodes): {res['median_seen_s_all']} s; share lasting >= "
         f"{cfg['gate']['min_seen_s']} s: {res['share_seen_ge_min']}. In-play at start: {res['in_play_share']}.", "",
         "| Gate | Met |", "|---|---|"] + [f"| {k} | {v} |" for k, v in res["gate"].items()] + [
         "", "| Kind | Episodes | Qualifying | Qualifying profit |", "|---|---|---|---|"] + [
         f"| {k} | {v['episodes']} | {v['qualifying']} | ${v['profit_q']:.2f} |" for k, v in res["by_kind"].items()] + [
         "", "## Largest qualifying episodes", ""] + [
         f"- {e['first_seen']} {e['title']}: edge {e['max_edge']:.2%}, ${e['max_profit']:.2f} on {e['max_sets']:.0f} sets, "
         f"seen {e['seen_s']} s, in-play {e['in_play_at_start']} | " + " + ".join(e["legs"]) for e in top]
    with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return res
