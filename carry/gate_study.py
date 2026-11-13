"""Run the carry selection study on Gate.io (same-exchange spot + USDT perp), using the exact
analyse() / report() of carry/study.py (written for Bybit). Bybit blocks US cloud servers;
Gate does not. Public data only."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study  # noqa: E402

G = "https://api.gateio.ws/api/v4"
DAYS = 420
OUT = os.path.join(os.path.dirname(HERE), "data", "carry")


def get(s, path, params=None):
    for a in range(5):
        try:
            r = s.get(G + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (a + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1 + a)
    return None


def funding(sym):
    s = requests.Session()
    now = int(time.time())
    start = now - DAYS * 86400
    rows = {}
    end = now
    while end > start:
        lst = get(s, "/futures/usdt/funding_rate", {"contract": sym, "limit": 1000, "from": max(start, end - 300 * 86400), "to": end}) or []
        for x in lst:
            rows[int(x["t"]) * 1000] = float(x["r"])
        end = max(start, end - 300 * 86400) - 1
    return sym, sorted(rows.items())


def main():
    os.makedirs(OUT, exist_ok=True)
    s = requests.Session()
    perps = [c for c in (get(s, "/futures/usdt/contracts") or []) if not c.get("in_delisting")]
    spot = {p["base"] for p in (get(s, "/spot/currency_pairs") or []) if p.get("quote") == "USDT" and p.get("trade_status") == "tradable"}
    uni = sorted(c["name"] for c in perps if c["name"].endswith("_USDT") and c["name"][:-5] in spot)
    print(f"{len(perps)} USDT perps, {len(uni)} with a tradable USDT spot pair", flush=True)
    series = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (sym, rows) in enumerate(ex.map(funding, uni), 1):
            if rows:
                series[sym] = study.daily_sums(rows)
            if i % 50 == 0:
                print(f"funding {i}/{len(uni)}", flush=True)
    res = study.analyse(series)
    with open(os.path.join(OUT, "gate_results.json"), "w") as f:
        json.dump(res, f, indent=1)
    rep = study.report(res).replace("Carry selection study (Bybit", "Carry selection study (Gate.io")
    with open(os.path.join(OUT, "GATE_RESULTS.md"), "w") as f:
        f.write(rep)
    print(rep[:2500], flush=True)
    print("STUDY_DONE", flush=True)


if __name__ == "__main__":
    main()
