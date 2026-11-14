import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm.bot import *
import pytest

@pytest.fixture(autouse=True)
def _no_shield(monkeypatch):
    monkeypatch.setitem(CFG, 'ahead_x', 0)      # older tests use thin books; shield tested separately


def bk(bids, asks): return {"bids": [{"price": str(p), "size": str(s)} for p, s in bids], "asks": [{"price": str(p), "size": str(s)} for p, s in asks]}

def test_score_and_qmin():
    assert abs(score(0, 4, 100) - 100) < 1e-9 and score(4, 4, 100) == 0 and abs(score(2, 4, 100) - 25) < 1e-9
    assert qmin(90, 30, 0.5) == 30 and qmin(90, 0, 0.5) == 30 and qmin(90, 0, 0.95) == 0

def test_adj_mid_uses_size_cutoff():
    b = bk([(0.48, 5), (0.47, 100)], [(0.52, 100)])
    assert abs(adj_mid(b, 50) - 0.495) < 1e-9

def test_fill_queue_and_through():
    o = Order("Y", 0.45, 100, 0, 50)
    assert apply_trade(o, 0.45, 30, 1) == 0 and o.queue == 20     # queue ahead consumed first
    assert apply_trade(o, 0.45, 50, 2) == 30                       # 20 ahead, then 30 to me
    assert apply_trade(o, 0.44, 500, 3) == 70 and o.rem == 0       # printed through
    n = Order("N", 0.50, 10, 0, 0)                                  # NO bid 0.50 = YES ask 0.50
    assert apply_trade(n, 0.49, 10, 1) == 0 and apply_trade(n, 0.51, 4, 1) == 4
    assert apply_trade(Order("Y", 0.45, 10, 5, 0), 0.40, 10, 4) == 0   # trade before order: ignored

def test_merge_and_pnl():
    m = Mkt(dict(cid="c", q="q", slug="s", tok="Y", tno="N", v=4.5, minsz=20, rate=100, tick=0.01))
    b = bk([(0.48, 100)], [(0.52, 100)])
    step(m, b, [], 10.0)
    yb, nb = m.orders["Y"].price, m.orders["N"].price
    assert yb < 0.5 and nb < 0.5 and 0.5 - yb < 0.045 and 0.5 - nb < 0.045
    n = m.orders["Y"].size
    trades = [dict(token_id="Y", price=str(yb - 0.01), size=str(n), timestamp=20), dict(token_id="Y", price=str(1 - nb + 0.01), size=str(n), timestamp=21)]
    step(m, b, trades, 30.0)
    assert m.merged == n and m.yes == 0 and m.no == 0
    assert abs(m.mtm() - n * (1 - yb - nb)) < 1e-6 and m.mtm() > 0

def test_sample_reward_share():
    m = Mkt(dict(cid="c", q="q", slug="s", tok="Y", tno="N", v=4.5, minsz=20, rate=1440, tick=0.01))
    b = bk([(0.49, 1e-9)], [(0.51, 1e-9)])
    step(m, b, [], 1.0); sample(m, b, 2.0)
    assert m.reward > 0.99      # sole maker gets ~ all of the per-minute pool (1440/1440)


def test_shield_rule_steps_back_or_skips(monkeypatch):
    monkeypatch.setitem(CFG, "ahead_x", 2); monkeypatch.setitem(CFG, "quote_frac", 0.5)
    m = Mkt(dict(cid="c", q="q", slug="s", tok="Y", tno="N", v=4.5, minsz=20, rate=100, tick=0.01))
    # deep bids: 1000 at 0.49 -> YES quote allowed; asks thin (only 10 in front) -> NO side skipped
    b = bk([(0.49, 1000), (0.47, 50)], [(0.51, 10), (0.56, 1000)])
    yb, nb, n, yb0, nb0 = target(m, b, 0.50)
    assert yb is not None and sum(s for p, s in [(0.49, 1000)] if p > yb) >= 2 * n
    assert nb is None and nb0 is not None
    # depth only at the far edge: quote steps back behind it but stays inside the reward zone
    b2 = bk([(0.49, 10), (0.47, 5000)], [(0.51, 5000)])
    yb2 = target(m, b2, 0.50)[0]
    assert yb2 is not None and yb2 < 0.47 and 0.50 - yb2 < 0.045

def test_stop_loss_caps_a_news_fill():
    m = Mkt(dict(cid="c", q="q", slug="s", tok="Y", tno="N", v=4.5, minsz=20, rate=100, tick=0.01))
    step(m, bk([(0.48, 100)], [(0.52, 100)]), [], 10.0)
    yb, n = m.orders["Y"].price, m.orders["Y"].size
    # news: price collapses through the YES bid to 0.30/0.34
    crash = bk([(0.30, 1000)], [(0.34, 1000)])
    step(m, crash, [dict(token_id="Y", price=str(yb - 0.05), size=str(n), timestamp=20)], 30.0)
    stops = [f for f in m.fills if f.get("kind") == "stop"]
    assert len(stops) == 1 and m.yes == 0 and m.orders == {"Y": None, "N": None}     # sold and paused
    assert abs(m.mtm() - (-(yb - 0.29) * n)) < 1e-6                                   # loss is realised, nothing left at risk
    step(m, bk([(0.10, 1000)], [(0.14, 1000)]), [], 40.0)
    assert abs(m.mtm() - (-(yb - 0.29) * n)) < 1e-6                                   # further collapse costs nothing more


def test_wide_book_centres_on_last_trade_or_stays_out():
    m = Mkt(dict(cid="c", q="q", slug="s", tok="Y", tno="N", v=4.5, minsz=20, rate=100, tick=0.01))
    wide = bk([(0.30, 100)], [(0.80, 100)])
    step(m, wide, [], 1000.0)
    assert m.orders == {"Y": None, "N": None}                       # no trade to anchor on: no quotes
    step(m, wide, [dict(token_id="N", price="0.64", size="50", timestamp=900)], 1015.0)   # NO at 0.64 = YES at 0.36
    y, n = m.orders["Y"], m.orders["N"]
    assert y and n and abs(m.mid - 0.36) < 1e-9
    assert 0.36 - 0.045 < y.price < 0.36 and 0.64 - 0.045 < n.price < 0.64
