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
RESULT = os.path.join(HERE, "PREFLIGHT_RESULT.txt")     # same lines as on screen (no secrets), readable afterwards
def out(line):
    print(line, flush=True)
    with open(RESULT, "a", encoding="utf-8") as f: f.write(line + "\n")

def check(name, fn):
    global ok
    try:
        r = fn(); out(f"[ OK ] {name}: {r}")
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt): raise
        ok = False; out(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:400]}")

def main():
    import datetime
    open(RESULT, "w", encoding="utf-8").write(f"Preflight {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
    load_env()
    check("pmmm\\.env has PM_PRIVATE_KEY", lambda: "present" if os.environ.get("PM_PRIVATE_KEY", "").startswith("0x") else (_ for _ in ()).throw(ValueError("missing or not starting with 0x")))
    check("pmmm\\.env has PM_FUNDER", lambda: os.environ["PM_FUNDER"][:6] + "..." + os.environ["PM_FUNDER"][-4:])
    check("wallet type", lambda: {"3": "3 (Deposit Wallet)", "1": "1 (older email/Google)", "2": "2 (older MetaMask)", "0": "0 (plain wallet)"}[os.environ.get("PM_SIG_TYPE", "1")])
    check("region allowed", lambda: (lambda g: g if not g.get("blocked") else (_ for _ in ()).throw(ValueError(f"BLOCKED: {g}")))(requests.get("https://polymarket.com/api/geoblock", timeout=15).json()))
    from pmmm.live import make_client
    box = {}
    check("login", lambda: (box.__setitem__("L", make_client()), "ok")[1])
    if "L" not in box: out("STOPPED: login failed"); return
    L = box["L"]
    check("wallet", lambda: L.funder[:6] + "..." + L.funder[-4:])
    check("USDC balance", lambda: f"${L.usdc():,.2f}")
    check("open orders now", lambda: len(L.open_map()))
    check("holdings readable", lambda: f"{len((getattr(L, 'positions', None) or (lambda: {}))())} positions")
    def sign():
        r = requests.get("https://clob.polymarket.com/rewards/markets/multi", params={"page_size": 50}, timeout=20).json()
        m = next((x for x in r["data"] if x.get("rewards_config") and x.get("tokens")), None) or next(x for x in r["data"] if x.get("tokens"))
        tok = m["tokens"][0]["token_id"]
        if hasattr(L, "k"):
            if L.k.get_closed_only_mode(): raise RuntimeError("account is in closed-only mode: it cannot open new positions")
            L.k.create_limit_order(token_id=str(tok), price="0.01", size="5", side="BUY", post_only=True)
        else:
            L.c.create_order(L.OrderArgs(price=float(L.c.get_tick_size(tok)), size=5.0, side=L.BUY, token_id=tok))
        return f"signed (not sent) a test order on '{m['question'][:50]}'"
    check("order signing", sign)
    check("rewards endpoint (yesterday)", lambda: L.earnings(__import__("datetime").date.today().isoformat()))
    out("ALL GOOD" if ok else "SOME CHECKS FAILED")

if __name__ == "__main__":
    main()
