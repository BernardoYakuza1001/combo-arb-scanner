"""Discovery + fast book polling + episode timing.

An episode starts when a basket's top-of-book set costs <= 1 - min_edge after fees AND at
least the minimum order size is fillable at a profit; it ends at the first poll where that
no longer holds. Durations are therefore measured to one poll interval (logged per poll).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from .pricing import top_edge, walk
from .relations import discover

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def now() -> float:
    return time.time()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class Api:
    def __init__(self, session, log, min_gap: float = 0.25):
        self.s, self.log, self.min_gap, self.last = session, log, min_gap, 0.0
        self.calls = self.errors = 0

    def _wait(self):
        d = self.min_gap - (now() - self.last)
        if d > 0:
            time.sleep(d)
        self.last = now()

    def req(self, method, url, **kw):
        for attempt in range(4):
            self._wait()
            self.calls += 1
            try:
                r = self.s.request(method, url, timeout=20, **kw)
                if r.status_code == 429:
                    self.log(f"429 on {url}, backing off")
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:  # network / JSON
                self.errors += 1
                self.log(f"error {type(e).__name__} on {url}")
                time.sleep(1 + attempt)
        return None


def fetch_events(api: Api, tag_slugs, pages: int, log=None) -> list[dict]:
    seen, out = set(), []
    for tag in tag_slugs:
        for p in range(pages):
            t0 = now()
            params = {"active": "true", "closed": "false", "limit": 100, "offset": 100 * p,
                      "order": "volume24hr", "ascending": "false"}
            if tag:
                params["tag_slug"] = tag
            page = api.req("GET", f"{GAMMA}/events", params=params)
            if not page:
                break
            for e in page:
                if e.get("id") not in seen:
                    seen.add(e.get("id"))
                    out.append(e)
            if log:
                log(f"events: tag {tag or 'all'} page {p + 1}: {len(page)} events in {now() - t0:.1f}s (total {len(out)})")
            if len(page) < 100:
                break
    return out


def fetch_books(api: Api, tokens: list[str]) -> dict:
    out = {}
    for i in range(0, len(tokens), 50):
        chunk = tokens[i:i + 50]
        res = api.req("POST", f"{CLOB}/books", json=[{"token_id": t} for t in chunk])
        for b in res or []:
            asks = sorted(((float(a["price"]), float(a["size"])) for a in b.get("asks") or []), key=lambda x: x[0])
            bids = sorted(((float(a["price"]), float(a["size"])) for a in b.get("bids") or []), key=lambda x: -x[0])
            out[str(b.get("asset_id"))] = {"asks": asks, "bids": bids, "ts": b.get("timestamp")}
    return out


class Tracker:
    """Opens/closes episodes per basket; writes closed episodes to a JSONL file."""

    def __init__(self, path: str, min_edge: float):
        self.path, self.min_edge, self.open = path, min_edge, {}
        self.closed = 0

    def update(self, b, books, t: float, poll_s: float):
        e = top_edge(b["legs"], books)
        sets, cost, profit = walk(b["legs"], books) if (e is not None and e >= self.min_edge) else (0, 0, 0)
        live = e is not None and e >= self.min_edge and sets > 0
        ep = self.open.get(b["id"])
        if live:
            started = any(c.start and c.start <= iso(t) for c in b["legs"])
            if not ep:
                self.open[b["id"]] = ep = {
                    "basket": b["id"], "kind": b["kind"], "event": b["event"], "title": b["title"],
                    "legs": [c.label for c in b["legs"]], "tokens": [c.token for c in b["legs"]],
                    "first_seen": iso(t), "t0": t, "polls": 0, "max_edge": e, "max_profit": profit,
                    "max_sets": sets, "cost_at_max": cost, "in_play_at_start": started,
                    "delay_s": max(c.delay_s for c in b["legs"]),
                    "asks_at_start": [((books.get(c.token) or {}).get("asks") or [[None]])[0][0] for c in b["legs"]],
                    "poll_s": poll_s}
            ep["polls"] += 1
            ep["last_seen"] = iso(t)
            ep["t1"] = t
            if profit > ep["max_profit"]:
                ep.update(max_profit=profit, max_sets=sets, cost_at_max=cost)
            ep["max_edge"] = max(ep["max_edge"], e)
        elif ep:
            ep["ended"] = iso(t)
            ep["seen_s"] = round(ep["t1"] - ep["t0"], 2)            # first to last poll seen open
            ep["upper_s"] = round(t - ep["t0"], 2)                  # first seen to first seen closed
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in ep.items() if k not in ("t0", "t1")}) + "\n")
            del self.open[b["id"]]
            self.closed += 1
        return e


def run(cfg: dict, log, max_cycles: int | None = None):
    import requests
    data = cfg["data_dir"]
    os.makedirs(data, exist_ok=True)
    api = Api(requests.Session(), log, cfg["min_request_gap_s"])
    tracker = Tracker(os.path.join(data, "episodes.jsonl"), cfg["min_edge"])
    baskets, tokens, hot, last_disc = [], [], set(), 0.0
    near = {}
    cycle = 0
    t_start = now()
    max_rt = cfg.get("max_runtime_s")
    tracker.path = os.path.join(data, cfg.get("episodes_file", "episodes.jsonl"))
    while max_cycles is None or cycle < max_cycles:
        t = now()
        if max_rt and t - t_start > max_rt:
            for bid in list(tracker.open):          # close open episodes so nothing is lost
                ep = tracker.open.pop(bid)
                ep["ended"] = iso(t); ep["seen_s"] = round(ep["t1"] - ep["t0"], 2)
                ep["upper_s"] = round(t - ep["t0"], 2); ep["cut_by_run_end"] = True
                with open(tracker.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({k: v for k, v in ep.items() if k not in ("t0", "t1")}) + "\n")
            log(f"max runtime reached after {cycle} cycles")
            break
        if t - last_disc > cfg["discovery_every_s"] or not baskets:
            evs = fetch_events(api, cfg["tag_slugs"], cfg["event_pages"], log)
            td = now()
            fams, baskets, st = discover(evs, cfg["max_legs"])
            log(f"relation search took {now() - td:.1f}s")
            tokens = sorted({c.token for b in baskets for c in b["legs"]})
            last_disc = t
            log(f"discovery: {len(evs)} events, {st.get('families', 0)} families, {len(baskets)} baskets, "
                f"{len(tokens)} tokens, skipped {({k: v for k, v in st.items() if k not in ('families', 'baskets')})}")
            with open(os.path.join(data, "baskets.json"), "w", encoding="utf-8") as f:
                json.dump([{"id": b["id"], "kind": b["kind"], "title": b["title"],
                            "legs": [c.label for c in b["legs"]]} for b in baskets], f, indent=0)
        # hot baskets (top edge within `hot_edge` of profit) every cycle; the rest every `cold_every`
        cold_turn = cycle % cfg["cold_every"] == 0
        poll = [b for b in baskets if cold_turn or b["id"] in hot or b["id"] in tracker.open]
        ptoks = sorted({c.token for b in poll for c in b["legs"]})
        t_req = now()
        books = fetch_books(api, ptoks)
        t_done = now()
        poll_s = round(t_done - t_req, 2)
        new_hot = set()
        for b in poll:
            e = tracker.update(b, books, t_done, poll_s)
            if e is not None and e >= cfg["hot_edge"]:
                new_hot.add(b["id"])
            if e is not None:
                near[b["kind"]] = max(near.get(b["kind"], -9), e)
        hot = new_hot if cold_turn else (hot | new_hot)
        cycle += 1
        if cycle % cfg["status_every"] == 0 or max_cycles:
            st = {"time": iso(now()), "cycle": cycle, "baskets": len(baskets), "polled": len(poll),
                  "tokens_polled": len(ptoks), "books_got": len(books), "poll_s": poll_s, "hot": len(hot),
                  "open_episodes": len(tracker.open), "closed_episodes": tracker.closed,
                  "best_edge_by_kind_since_start": {k: round(v, 4) for k, v in near.items()},
                  "api_calls": api.calls, "api_errors": api.errors}
            with open(os.path.join(data, "status.json"), "w", encoding="utf-8") as f:
                json.dump(st, f, indent=1)
            log(f"cycle {cycle}: polled {len(poll)} baskets / {len(ptoks)} tokens in {poll_s}s, "
                f"hot {len(hot)}, open {len(tracker.open)}, closed {tracker.closed}")
        rest = cfg["cycle_s"] - (now() - t)
        if rest > 0:
            time.sleep(rest)
