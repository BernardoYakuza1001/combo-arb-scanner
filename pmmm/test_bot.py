import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pmmm.bot import *

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
