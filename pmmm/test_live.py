"""Offline tests for live.py: real order signing with a throwaway key, and the live loop against a fake exchange."""
import os, sys, json, time, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_real_signing_offline():
    from eth_account import Account
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
    from py_clob_client.order_builder.constants import BUY
    acct = Account.create()
    c = ClobClient("https://clob.polymarket.com", key=acct.key.hex(), chain_id=137, signature_type=1, funder=acct.address)
    c._ClobClient__resolve_fee_rate = lambda tok, fee: 0   # fee lookup is the only network call
    c._ClobClient__resolve_fee_rate = lambda tok, fee: 0          # these two are the only network lookups
    c._ClobClient__resolve_tick_size = lambda tok, tick: tick or "0.01"
    c.get_neg_risk = lambda tok: False
    o = c.create_order(OrderArgs(price=0.43, size=212.0, side=BUY, token_id="1234567890"),
                       PartialCreateOrderOptions(tick_size="0.01", neg_risk=False))
    d = o.dict()
    assert d["side"] == "BUY" and int(d["makerAmount"]) == round(0.43 * 212 * 1e6) and int(d["takerAmount"]) == 212_000_000
    assert d["signature"].startswith("0x") and len(d["signature"]) > 100

def test_live_loop_fake_exchange(tmp_path, monkeypatch):
    from pmmm import live, bot
    monkeypatch.setattr(live, "STOP", str(tmp_path / "STOP")); monkeypatch.setattr(live, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(live, "STATUS", str(tmp_path / "STATUS.md"))
    monkeypatch.setitem(bot.CFG, "cycle_s", 0)
    os.environ.update(PMMM_LIVE="YES", PM_PRIVATE_KEY="0xabc", PM_FUNDER="0xF00")
    state = dict(placed=[], cancelled=[], cancel_all=0, cycle=0, matched={})
    class FakeLive:
        funder = "0xF00"
        def __init__(self): pass
        def usdc(self): return 5000.0
        def place(self, tok, px, sz, tick):
            oid = f"o{len(state['placed'])}"; state["placed"].append((oid, tok, px, sz)); return oid
        def cancel(self, oid): state["cancelled"].append(oid)
        def cancel_all(self): state["cancel_all"] += 1
        def matched(self, oid): return state["matched"].get(oid, 0.0), "LIVE"
        def scoring(self, ids): return {i: True for i in ids}
        def earnings(self, d): return {"earnings": 1.23}
    monkeypatch.setattr(live, "Live", FakeLive)
    m = bot.Mkt(dict(cid="0xcid", q="Test market", slug="t", tok="YES", tno="NO", v=4.5, minsz=50, rate=100, tick=0.01))
    monkeypatch.setattr(bot, "select", lambda: [m])
    monkeypatch.setattr(live, "positions", lambda f: {})
    book = {"asset_id": "YES", "bids": [{"price": "0.48", "size": "500"}], "asks": [{"price": "0.52", "size": "500"}]}
    def fake_http(method, url, **kw):
        state["cycle"] += 1
        if state["cycle"] == 3:   # after quotes are placed: YES bid and NO bid both fully filled
            for oid, tok, px, sz in state["placed"]: state["matched"][oid] = sz
        if state["cycle"] >= 6: open(live.STOP, "w").close()
        return [book]
    monkeypatch.setattr(live, "http", fake_http)
    live.main()
    ev = [json.loads(l)["ev"] for l in open(live.LOG)]
    assert state["cancel_all"] == 2                      # at start and at exit
    assert len(state["placed"]) >= 4                     # quoted, filled, re-quoted
    toks = {p[1] for p in state["placed"]}; assert toks == {"YES", "NO"}
    assert "fill" in ev and "pair_locked" in ev and ev[-1] == "stopped"
    for oid, tok, px, sz in state["placed"]:
        assert 0.5 - px < 0.045 and sz >= 50             # inside max spread, at least min size
    assert m.yes == 0 and m.no == 0 and m.merged > 0
