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


def test_thin_mode_anchor_ignores_own_orders_and_follows_fills(tmp_path, monkeypatch):
    """Wide book 0.30/0.80, last trade 0.36. The bot must quote around 0.36, must not re-anchor on its own
    resting orders, must move down after its YES bid is hit, and must stay out when there is no trade."""
    from pmmm import live, bot
    monkeypatch.setattr(live, "STOP", str(tmp_path / "STOP")); monkeypatch.setattr(live, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(live, "STATUS", str(tmp_path / "STATUS.md")); monkeypatch.setattr(live, "LIVE_CYCLE_S", 0)
    monkeypatch.setattr(live, "MAX_LOSS", 500.0)
    for k, v in dict(ahead_x=0, stop_c=1.0, cap=1.0, quote_frac=0.75, news_move=1.0).items(): monkeypatch.setitem(bot.CFG, k, v)
    os.environ.update(PMMM_LIVE="YES", PM_PRIVATE_KEY="0xabc", PM_FUNDER="0xF00")
    st = dict(orders={}, n=0, cycle=0, cancelled=[], tape_a=[dict(token_id="YES", price="0.36", size="30", timestamp=time.time() - 3600)])
    class C:
        def post_heartbeat(self, hid): return {"heartbeat_id": "h"}
    class FakeLive:
        funder = "0xF00"; c = C()
        def usdc(self): return 5000.0
        def place(self, tok, px, sz, tick, sell=False):
            st["n"] += 1; oid = f"o{st['n']}"; st["orders"][oid] = dict(tok=tok, px=px, sz=sz, sell=sell, matched=0.0, live=True); return oid
        def cancel(self, oid): st["orders"][oid]["live"] = False; st["cancelled"].append(oid)
        def cancel_all(self): pass
        def open_map(self): return {i: o["matched"] for i, o in st["orders"].items() if o["live"]}
        def matched(self, oid): o = st["orders"][oid]; return o["matched"], ("LIVE" if o["live"] else "MATCHED")
        def scoring(self, ids): return {}
        def earnings(self, d): return {}
        def stop_sell(self, *a): raise AssertionError("stop-loss must be off in thin mode")
    monkeypatch.setattr(live, "Live", FakeLive)
    A = bot.Mkt(dict(cid="0xA", q="Anchored market", slug="a", tok="YES", tno="NO", v=4.5, minsz=20, rate=100, tick=0.01))
    B = bot.Mkt(dict(cid="0xB", q="No trades market", slug="b", tok="YES2", tno="NO2", v=4.5, minsz=20, rate=100, tick=0.01))
    monkeypatch.setattr(bot, "select", lambda: [A, B]); monkeypatch.setattr(live, "positions", lambda f: {})
    def book_for(tok):
        bids = {0.20: 100.0}; asks = {0.80: 100.0}
        for o in st["orders"].values():           # the exchange shows my resting orders in the book too
            if not o["live"] or o["matched"] >= o["sz"]: continue
            if o["tok"] == tok and not o["sell"]: bids[round(o["px"], 2)] = bids.get(round(o["px"], 2), 0) + o["sz"]
            if o["tok"] == ("NO" if tok == "YES" else "NO2") and not o["sell"]: asks[round(1 - o["px"], 2)] = asks.get(round(1 - o["px"], 2), 0) + o["sz"]
            if o["tok"] == tok and o["sell"]: asks[round(o["px"], 2)] = asks.get(round(o["px"], 2), 0) + o["sz"]
        return {"asset_id": tok, "bids": [{"price": str(p), "size": str(z)} for p, z in bids.items()], "asks": [{"price": str(p), "size": str(z)} for p, z in asks.items()]}
    snap = {}
    def fake_http(method, url, **kw):
        if method == "GET":      # trade tape
            return {"data": st["tape_a"] if kw["params"]["condition_id"] == "0xA" else []}
        st["cycle"] += 1
        if st["cycle"] == 4:     # someone hits my YES bid: fill + a print at my price
            snap["before"] = {i: dict(o) for i, o in st["orders"].items() if o["live"]}
            oid, o = next((i, o) for i, o in st["orders"].items() if o["live"] and o["tok"] == "YES" and not o["sell"])
            o["matched"] = o["sz"]; o["live"] = False
            st["tape_a"] = st["tape_a"] + [dict(token_id="YES", price=str(o["px"]), size=str(o["sz"]), timestamp=time.time())]
            snap["fill_px"] = o["px"]
        if st["cycle"] >= 8: open(live.STOP, "w").close()
        return [book_for("YES"), book_for("YES2")]
    monkeypatch.setattr(live, "http", fake_http)
    monkeypatch.setattr(live, "TAPE_REFRESH_S", -1)      # refresh the tape every cycle
    live.main()
    ev = [json.loads(l) for l in open(live.LOG)]
    first = [e for e in ev if e["ev"] == "quote"][:2]
    assert {e["side"] for e in first} == {"Y", "N"}
    yq = next(e for e in first if e["side"] == "Y"); nq = next(e for e in first if e["side"] == "N")
    assert 0.36 - 0.045 < yq["px"] < 0.36 and 0.64 - 0.045 < nq["px"] < 0.64 and yq["sz"] == 20      # around last trade, minimum size
    assert all("No trades" not in e.get("q", "") for e in ev if e["ev"] in ("quote", "exit_quote"))     # market B never quoted
    # cycles 2-3: own orders are in the book, yet nothing was re-quoted (no self-anchoring, no churn)
    assert len(snap["before"]) == 2 and not [c for c in st["cancelled"] if c in ("o1", "o2") and st["cycle"] < 4]
    # after the fill: an exit SELL of YES is posted, and it is anchored at the fill price (lower than before)
    ex = [e for e in ev if e["ev"] == "exit_quote"]
    assert ex and ex[0]["sell"] == "YES" and snap["fill_px"] < ex[0]["px"] < snap["fill_px"] + 0.045
    assert any(e["ev"] == "fill" for e in ev) and A.yes == 20


def test_setup_key_validation_and_file(tmp_path, monkeypatch, capsys):
    from pmmm import setup_key as sk
    assert sk.clean_key(" ab" * 0 + "A" * 64) == "0x" + "A" * 64 and sk.clean_key("0x" + "1" * 64 + "\n") == "0x" + "1" * 64
    assert sk.clean_key("0x123") is None and sk.clean_key("hello") is None
    assert sk.clean_addr("0x" + "a" * 40) and sk.clean_addr("0x" + "a" * 39) is None
    monkeypatch.setattr(sk, "ENV", str(tmp_path / ".env")); monkeypatch.setattr(sk, "detect", lambda k: None)
    key = "0x" + "7" * 64
    monkeypatch.setattr(sk.os, "system", lambda c: 0)
    answers = iter([key, "0x" + "b" * 40, "1"]); monkeypatch.setattr("builtins.input", lambda p="": next(answers))
    sk.main()
    txt = open(sk.ENV).read()
    assert f"PM_PRIVATE_KEY={key}" in txt and "PM_SIG_TYPE=1" in txt and "PM_FUNDER=0x" + "b" * 40 in txt
    assert key not in capsys.readouterr().out          # the key is never printed


def test_small_balance_opens_only_markets_that_fit_both_sides(tmp_path, monkeypatch):
    """$72 balance, 20-share markets at ~$19 a pair: three markets get both orders, the fourth gets none."""
    from pmmm import live, bot
    monkeypatch.setattr(live, "STOP", str(tmp_path / "STOP")); monkeypatch.setattr(live, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(live, "STATUS", str(tmp_path / "STATUS.md")); monkeypatch.setattr(live, "LIVE_CYCLE_S", 0)
    monkeypatch.setattr(live, "MAX_TOTAL", 1000.0)
    for k, v in dict(ahead_x=0, stop_c=1.0, cap=1.0, quote_frac=0.75).items(): monkeypatch.setitem(bot.CFG, k, v)
    os.environ.update(PMMM_LIVE="YES", PM_PRIVATE_KEY="0xabc", PM_FUNDER="0xF00")
    placed = []; cyc = {"n": 0}
    class C:
        def post_heartbeat(self, hid): return {"heartbeat_id": "h"}
    class FakeLive:
        funder = "0xF00"; c = C()
        def usdc(self): return 72.0
        def place(self, tok, px, sz, tick, sell=False): placed.append((tok, px, sz)); return f"o{len(placed)}"
        def cancel(self, oid): pass
        def cancel_all(self): pass
        def open_map(self): return {f"o{i+1}": 0.0 for i in range(len(placed))}
        def matched(self, oid): return 0.0, "LIVE"
        def scoring(self, ids): return {}
        def earnings(self, d): return {}
    monkeypatch.setattr(live, "Live", FakeLive)
    mk = [bot.Mkt(dict(cid=f"0x{i}", q=f"M{i}", slug="s", tok=f"Y{i}", tno=f"N{i}", v=4.5, minsz=20, rate=50, tick=0.01)) for i in range(4)]
    monkeypatch.setattr(bot, "select", lambda: mk); monkeypatch.setattr(live, "positions", lambda f: {})
    def fake_http(method, url, **kw):
        if method == "GET": return {"data": []}
        cyc["n"] += 1
        if cyc["n"] >= 3: open(live.STOP, "w").close()
        return [{"asset_id": f"Y{i}", "bids": [{"price": "0.48", "size": "500"}], "asks": [{"price": "0.52", "size": "500"}]} for i in range(4)]
    monkeypatch.setattr(live, "http", fake_http)
    live.main()
    per = {}
    for tok, px, sz in placed: per.setdefault(tok[1:], []).append(tok[0])
    assert sorted(per) == ["0", "1", "2"] and all(sorted(v) == ["N", "Y"] for v in per.values())    # 3 full pairs, market 3 untouched
    assert sum(px * sz for _, px, sz in placed) <= 72


def test_setup_key_finds_wallet_by_balance(tmp_path, monkeypatch, capsys):
    from pmmm import setup_key as sk
    from eth_account import Account
    acct = Account.create(); cands = sk.candidates(acct.address)
    assert [c[0] for c in cands] == ["1", "2", "0"] and len({c[1] for c in cands}) == 3 and cands[2][1] == acct.address
    # known derivation check (public signer address -> wallet candidates)
    k = sk.candidates("0x0421F86e2F69f1A0DDDA760827019407E4d749df")
    assert k[0][1] == "0xe734e2fAc39B062f3044648bbaDc818364FAC950" and k[1][1] == "0xb163220Ef974D93ddBA4b3Ea3Bca9782FFB608Fa"
    monkeypatch.setattr(sk, "ENV", str(tmp_path / ".env")); monkeypatch.setattr(sk.os, "system", lambda c: 0)
    monkeypatch.setattr(sk, "balance", lambda key, sig, addr: 72.77 if sig == "2" else 0.0)
    answers = iter([acct.key.hex()]); monkeypatch.setattr("builtins.input", lambda p="": next(answers))
    sk.main()
    txt = open(sk.ENV).read()
    assert f"PM_FUNDER={cands[1][1]}" in txt and "PM_SIG_TYPE=2" in txt
    assert acct.key.hex()[-20:] not in capsys.readouterr().out
