"""Probe which Polymarket leaderboard / wallet-trade endpoints answer, and with what fields."""
import json, requests
S = requests.Session(); S.headers["User-Agent"] = "edges/1.0"
def show(url, **p):
    try:
        r = S.get(url, params=p, timeout=20); t = r.text[:500].replace("\n", " ")
        print(r.status_code, url, p, "->", t, flush=True); return r.json() if r.ok else None
    except Exception as e: print("ERR", url, e); return None
lb = None
for u, p in [("https://lb-api.polymarket.com/profit", dict(window="30d", limit=20)),
             ("https://data-api.polymarket.com/v1/leaderboard", dict(timePeriod="MONTH", orderBy="PNL", limit=20)),
             ("https://data-api.polymarket.com/v2/leaderboard", dict(time_period="MONTH", order_by="PNL", limit=20)),
             ("https://data-api.polymarket.com/leaderboard", dict(window="30d", limit=20))]:
    j = show(u, **p)
    if j and not lb: lb = j
w = None
if lb:
    rows = lb if isinstance(lb, list) else lb.get("data", [])
    if rows: w = rows[0].get("user_id") or rows[0].get("proxyWallet") or rows[0].get("proxy_wallet") or rows[0].get("user") or rows[0].get("address")
print("wallet", w)
if w:
    for u, p in [("https://data-api.polymarket.com/v2/trades", dict(user_id=w, limit=3)),
                 ("https://data-api.polymarket.com/v2/activity", dict(user_id=w, limit=3)),
                 ("https://data-api.polymarket.com/v2/positions", dict(user_id=w, limit=3)),
                 ("https://data-api.polymarket.com/v2/positions", dict(user=w, limit=3)),
                 ("https://data-api.polymarket.com/v2/closed-positions", dict(user_id=w, limit=3)),
                 ("https://data-api.polymarket.com/v2/trades", dict(user=w, limit=5)),
                 ("https://data-api.polymarket.com/v2/trades", dict(proxy_wallet=w, limit=5)),
                 ("https://data-api.polymarket.com/v2/activity", dict(user=w, limit=5)),
                 ("https://data-api.polymarket.com/activity", dict(user=w, limit=5)),
                 ("https://data-api.polymarket.com/positions", dict(user=w, limit=5)),
                 ("https://data-api.polymarket.com/closed-positions", dict(user=w, limit=5)),
                 ("https://data-api.polymarket.com/v2/closed-positions", dict(user=w, limit=5))]:
        show(u, **p)
