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
    monkeypatch.setattr(live, "STATUS", str(tmp_path / "STATUS.md")); monkeypatch.setitem(bot.CFG, "ahead_x", 0)
    monkeypatch.setitem(bot.CFG, "cycle_s", 0); monkeypatch.setattr(live, "LIVE_CYCLE_S", 0)
    os.environ.update(PMMM_LIVE="YES", PM_PRIVATE_KEY="0xabc", PM_FUNDER="0xF00")
    state = dict(placed=[], cancelled=[], cancel_all=0, cycle=0, matched={})
    class FakeLive:
        funder = "0xF00"
        def __init__(self): pass
        def usdc(self): return 5000.0
        def place(self, tok, px, sz, tick, sell=False):
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


def _setup(tmp_path, monkeypatch, books, fills_at=None, max_cycles=8, max_loss=30.0):
    from pmmm import live, bot
    monkeypatch.setattr(live, "STOP", str(tmp_path / "STOP")); monkeypatch.setattr(live, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(live, "STATUS", str(tmp_path / "STATUS.md")); monkeypatch.setattr(live, "LIVE_CYCLE_S", 0)
    monkeypatch.setitem(bot.CFG, "ahead_x", 0); monkeypatch.setattr(live, "MAX_LOSS", max_loss)
    os.environ.update(PMMM_LIVE="YES", PM_PRIVATE_KEY="0xabc", PM_FUNDER="0xF00")
    st = dict(placed=[], cancelled=[], cancel_all=0, cycle=0, matched={}, beats=0, sells=[], stops=[])
    class C:
        def post_heartbeat(self, hid): st["beats"] += 1; return {"heartbeat_id": f"h{st['beats']}"}
    class FakeLive:
        funder = "0xF00"; c = C()
        def usdc(self): return 5000.0
        def place(self, tok, px, sz, tick, sell=False):
            oid = f"o{len(st['placed'])}"; (st["sells"] if sell else st["placed"]).append((oid, tok, px, sz)); return oid if not sell else "s" + oid
        def stop_sell(self, tok, px, sz, tick): st["stops"].append((tok, px, sz)); return {"success": True}
        def cancel(self, oid): st["cancelled"].append(oid)
        def cancel_all(self): st["cancel_all"] += 1
        def matched(self, oid): return st["matched"].get(oid, 0.0), "LIVE"
        def scoring(self, ids): return {}
        def earnings(self, d): return {}
    monkeypatch.setattr(live, "Live", FakeLive)
    m = bot.Mkt(dict(cid="0xcid", q="Test market", slug="t", tok="YES", tno="NO", v=4.5, minsz=50, rate=100, tick=0.01))
    monkeypatch.setattr(bot, "select", lambda: [m]); monkeypatch.setattr(live, "positions", lambda f: {})
    def fake_http(method, url, **kw):
        st["cycle"] += 1
        if fills_at and st["cycle"] == fills_at:
            for oid, tok, px, sz in st["placed"]:
                if tok == "YES": st["matched"][oid] = sz
        if st["cycle"] >= max_cycles: open(live.STOP, "w").close()
        return books(st["cycle"])
    monkeypatch.setattr(live, "http", fake_http)
    return live, st, m

def _bk(bid, ask): return [{"asset_id": "YES", "bids": [{"price": str(bid), "size": "500"}], "asks": [{"price": str(ask), "size": "500"}]}]

def test_loss_stop_exits_and_cancels(tmp_path, monkeypatch):
    # YES bid fills, then the price collapses: stop-loss sells, and the realised loss trips the $5 daily stop -> abort
    live, st, m = _setup(tmp_path, monkeypatch, lambda c: _bk(0.48, 0.52) if c < 4 else _bk(0.20, 0.24), fills_at=3, max_cycles=50, max_loss=5.0)
    live.main()
    ev = [json.loads(l) for l in open(live.LOG)]
    assert any(e["ev"] == "abort" and "loss stop" in e["why"] for e in ev)
    assert st["cycle"] < 50 and st["cancel_all"] == 2 and ev[-1]["ev"] == "stopped"

def test_blind_cancel_and_heartbeat(tmp_path, monkeypatch):
    live, st, m = _setup(tmp_path, monkeypatch, lambda c: _bk(0.48, 0.52) if c < 3 else [], max_cycles=8)
    live.main()
    ev = [json.loads(l)["ev"] for l in open(live.LOG)]
    assert "blind_cancel" in ev and len(st["cancelled"]) == 2      # both resting orders pulled once blind
    assert "heartbeat_on" in ev and st["beats"] >= 1


def test_exit_order_then_stop_loss(tmp_path, monkeypatch):
    # YES bid fills at cycle 3. Next cycle: the NO slot becomes a resting SELL of the YES shares.
    # Then the price drops 6c: stop-loss sells the YES at market and the market is paused.
    live, st, m = _setup(tmp_path, monkeypatch, lambda c: _bk(0.48, 0.52) if c < 6 else _bk(0.40, 0.44), fills_at=3, max_cycles=9, max_loss=500.0)
    live.main()
    ev = [json.loads(l) for l in open(live.LOG)]
    names = [e["ev"] for e in ev]
    assert "fill" in names and "exit_quote" in names and "stop_loss" in names
    assert st["sells"] and st["sells"][0][1] == "YES" and st["sells"][0][2] > 0.50      # exit sells YES above mid
    assert len(st["stops"]) == 1 and st["stops"][0][0] == "YES" and m.yes == 0
    sl = next(e for e in ev if e["ev"] == "stop_loss"); assert 0 < sl["est_loss"] < 30
    assert names.index("exit_quote") < names.index("stop_loss")
