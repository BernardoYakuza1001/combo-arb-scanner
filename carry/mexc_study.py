"""Carry selection study on MEXC (same-exchange spot + USDT perp), ~540 days of funding.
Same analyse()/report() as the Bybit study (carry/study.py), same fee model, so the rule ranking
is directly comparable. Bybit and Binance block US cloud servers; MEXC does not."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "data", "carry")
CUT_MS = int(time.time() * 1000) - 420 * 86_400_000


def get(s, url, params=None):
    for a in range(6):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (a + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1 + a)
    return None


def funding(sym):
    s = requests.Session()
    rows, page = {}, 1
    while True:
        j = get(s, "https://contract.mexc.com/api/v1/contract/funding_rate/history",
                {"symbol": sym, "page_size": 1000, "page_num": page}) or {}
        d = j.get("data") or {}
        for x in d.get("resultList") or []:
            if int(x["settleTime"]) >= CUT_MS:
                rows[int(x["settleTime"])] = float(x["fundingRate"])
        if page >= int(d.get("totalPage") or 0):
            break
        page += 1
        time.sleep(0.1)
    return sym, sorted(rows.items())


def main():
    os.makedirs(OUT, exist_ok=True)
    s = requests.Session()
    perps = [x for x in (get(s, "https://contract.mexc.com/api/v1/contract/detail") or {}).get("data", [])
             if x.get("quoteCoin") == "USDT" and x.get("state") == 0]
    spot = {x["baseAsset"] for x in (get(s, "https://api.mexc.com/api/v3/exchangeInfo") or {}).get("symbols", [])
            if x.get("quoteAsset") == "USDT" and str(x.get("status")) in ("1", "ENABLED", "TRADING")}
    uni = sorted(x["symbol"] for x in perps if x.get("baseCoin") in spot)
    print(f"{len(perps)} live USDT perps, {len(uni)} with a USDT spot pair", flush=True)
    series = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (sym, rows) in enumerate(ex.map(funding, uni), 1):
            if rows:
                series[sym] = study.daily_sums(rows)
            if i % 100 == 0:
                print(f"funding {i}/{len(uni)}", flush=True)
    res = study.analyse(series)
    with open(os.path.join(OUT, "mexc_results.json"), "w") as f:
        json.dump(res, f, indent=1)
    rep = study.report(res).replace("Carry selection study (Bybit", "Carry selection study (MEXC")
    with open(os.path.join(OUT, "MEXC_RESULTS.md"), "w") as f:
        f.write(rep)
    print(rep[:1800], flush=True)
    print("STUDY_DONE", flush=True)


if __name__ == "__main__":
    main()
