"""Preflight for PMMM live. Places NO orders. Run PREFLIGHT.bat.

Checks: Python packages, .env present, region not blocked, Polymarket login
(API credentials derived from your key), USDC balance and allowance, open
orders, a live reward market and its tick size, and that an order can be
signed (signed locally, never sent).
"""
import os, sys, json, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm.live import load_env, HERE

ok = True
def check(name, fn):
    global ok
    try:
        r = fn(); print(f"[ OK ] {name}: {r}")
    except Exception as e:
        ok = False; print(f"[FAIL] {name}: {str(e)[:300]}")

def main():
    load_env()
    check("pmmm\\.env has PM_PRIVATE_KEY", lambda: "present" if os.environ.get("PM_PRIVATE_KEY", "").startswith("0x") else (_ for _ in ()).throw(ValueError("missing or not starting with 0x")))
    check("pmmm\\.env has PM_FUNDER", lambda: os.environ["PM_FUNDER"][:6] + "..." + os.environ["PM_FUNDER"][-4:])
    check("PM_SIG_TYPE", lambda: {"1": "1 (email/Google login)", "2": "2 (MetaMask/browser wallet)", "0": "0 (plain wallet)"}[os.environ.get("PM_SIG_TYPE", "1")])
    check("region allowed", lambda: (lambda g: g if not g.get("blocked") else (_ for _ in ()).throw(ValueError(f"BLOCKED: {g}")))(requests.get("https://polymarket.com/api/geoblock", timeout=15).json()))
    from pmmm.live import Live
    box = {}
    check("login (derive API credentials)", lambda: (box.__setitem__("L", Live()), "ok")[1])
    if "L" not in box: print("\nFix the items above and run again."); return
    L = box["L"]
    check("USDC balance", lambda: f"${L.usdc():,.2f}")
    check("allowance", lambda: str(L.c.get_balance_allowance(L.BAP(asset_type=L.AT.COLLATERAL, signature_type=int(os.environ.get('PM_SIG_TYPE', '1')))).get("allowances", "n/a"))[:120])
    check("open orders now", lambda: len(L.c.get_orders()))
    def sign():
        r = requests.get("https://clob.polymarket.com/rewards/markets/multi", params={"page_size": 50}, timeout=20).json()
        m = next(x for x in r["data"] if x.get("rewards_config") and x.get("tokens"))
        tok = m["tokens"][0]["token_id"]; tick = L.c.get_tick_size(tok)
        o = L.c.create_order(L.OrderArgs(price=float(tick), size=5.0, side=L.BUY, token_id=tok))
        return f"signed (not sent) a test order on '{m['question'][:50]}', tick {tick}"
    check("order signing", sign)
    check("rewards endpoint (yesterday)", lambda: L.earnings(__import__("datetime").date.today().isoformat()))
    print("\nALL GOOD - you can run DRY_RUN.bat, then RUN_LIVE.bat." if ok else "\nSome checks failed - send me the [FAIL] lines (never the key).")

if __name__ == "__main__":
    main()
