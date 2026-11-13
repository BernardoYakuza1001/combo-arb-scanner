import itertools
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from combo_arb import relations as R
from combo_arb.pricing import fee, top_edge, walk
from combo_arb.collector import Tracker
from combo_arb.report import summarise

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = json.load(open(os.path.join(HERE, "sample_events.json")))


def _toks(m):
    return [str(t) for t in R.loads(m["clobTokenIds"])]


def independent_payout(m, score_a, score_b, team_a, team_b, total_override=None):
    """Settle one market from its raw definition, for a given (A, B) score. Returns {token: payout}."""
    outs = R.loads(m["outcomes"])
    t0, t1 = _toks(m)
    st = m["sportsMarketType"]
    if st == "totals":
        total = score_a + score_b if total_override is None else total_override
        over = total > m["line"]
        o_over = outs.index("Over")
        win = [over if i == o_over else (not over) for i in range(2)]
    else:
        margin = {team_a: score_a - score_b, team_b: score_b - score_a}
        line = 0.0 if st.endswith("moneyline") else m["line"]
        w0 = margin[outs[0]] + line > 0
        win = [w0, not w0]
    return {t0: 1.0 if win[0] else 0.0, t1: 1.0 if win[1] else 0.0}


def test_series_baskets_hold_for_every_score():
    for ev in EVENTS:
        if "(BO" not in ev["title"]:
            continue
        fams, baskets, _ = R.discover([ev])
        ser = [b for b in baskets if b["kind"] == "series"]
        assert ser, f"no series baskets for {ev['slug']}"
        n = int(ev["title"].split("(BO")[1][0])
        w = n // 2 + 1
        mkts = [m for m in ev["markets"] if R.tradable(m) and m.get("sportsMarketType") in ("moneyline", "map_handicap", "totals")]
        team_a, team_b = sorted(R.loads(next(m for m in mkts if m["sportsMarketType"] == "moneyline")["outcomes"]))
        for a, b in [(w, k) for k in range(w)] + [(k, w) for k in range(w)]:
            pay = {}
            for m in mkts:
                pay.update(independent_payout(m, a, b, team_a, team_b))
            for bk in ser:
                total = sum(pay[c.token] for c in bk["legs"])
                assert total >= 1.0, (ev["slug"], (a, b), [c.label for c in bk["legs"]], total)


def test_total_family_baskets_hold():
    for ev in EVENTS:
        fams, baskets, _ = R.discover([ev])
        for bk in baskets:
            if bk["kind"] != "total":
                continue
            mk = {m["slug"]: m for m in ev["markets"]}
            for total in range(0, 60):
                s = 0.0
                for c in bk["legs"]:
                    m = mk[c.market]
                    p = independent_payout(m, 0, 0, "x", "y", total_override=total)
                    s += p[c.token]
                assert s >= 1.0, (bk["legs"], total)


def test_expected_baskets_found():
    _, baskets, _ = R.discover(EVENTS)
    labels = [" + ".join(sorted(c.label for c in b["legs"])) for b in baskets]
    assert any("Games Total: O/U 3.5 Over" in l and "Games Total: O/U 4.5 Under" in l for l in labels)
    assert not any("O/U 3.5 Under" in l and "O/U 4.5 Over" in l for l in labels), "middle-gap basket is not guaranteed"
    assert len({frozenset(c.token for c in b["legs"]) for b in baskets}) == len(baskets), "duplicate baskets"
    btc = [b for b in baskets if b["kind"] == "hit_up" and "november" in b["event"]]
    assert any({"Will Bitcoin reach $75,000 in November? YES", "Will Bitcoin reach $77,500 in November? NO"} == {c.label for c in b["legs"]} for b in btc)
    ser = [b for b in baskets if b["kind"] == "series" and b["event"].startswith("cs2")]
    assert any(len(b["legs"]) == 3 for b in ser), "BO3 three-way partition expected"


def test_ladder_baskets_hold_for_any_price_path():
    _, baskets, _ = R.discover(EVENTS)
    for b in baskets:
        if b["kind"] not in ("hit_up", "hit_dn"):
            continue
        # resolve from question text independently: reach $v -> max >= v ; dip to $v -> min <= v
        def val(c):
            return float(c.label.split("$")[1].split(" ")[0].replace(",", ""))
        vals = sorted({val(c) for c in b["legs"]})
        probes = [vals[0] / 2, vals[-1] * 2] + vals + [(x + y) / 2 for x, y in zip(vals, vals[1:])]
        for x in probes:
            s = 0.0
            for c in b["legs"]:
                v = val(c)
                yes = (x >= v) if "reach" in c.label else (x <= v)
                s += 1.0 if (yes == c.label.endswith("YES")) else 0.0
            assert s >= 1.0, ([c.label for c in b["legs"]], x)


def test_no_basket_reuses_a_market():
    _, baskets, _ = R.discover(EVENTS)
    for b in baskets:
        assert len({c.market for c in b["legs"]}) == len(b["legs"])


def _claim(tok, rate=0.05):
    return R.Claim(tok, "UP", 0, False, False, tok, tok, rate, 5.0, 0.01, 0.0, "")


def test_pricing_walk_and_fee():
    assert abs(fee(0.5, 0.05) - 0.0125) < 1e-12
    legs = [_claim("a"), _claim("b")]
    books = {"a": {"asks": [(0.40, 10), (0.45, 100)]}, "b": {"asks": [(0.50, 8), (0.58, 100)]}}
    e = top_edge(legs, books)
    assert abs(e - (1 - (0.40 + fee(0.40, .05) + 0.50 + fee(0.50, .05)))) < 1e-12
    sets, cost, profit = walk(legs, books)
    # 8 sets at 0.40+0.50 (+fees); next level 0.40+0.58 = 0.98 + fees (0.012+0.0122) > 1 -> stop
    assert abs(sets - 8) < 1e-9 and profit > 0
    sets2, _, _ = walk(legs, {"a": {"asks": [(0.40, 3)]}, "b": {"asks": [(0.50, 3)]}})
    assert sets2 == 0.0, "below the 5-share minimum must not count"


def test_tracker_episode_lifecycle():
    d = tempfile.mkdtemp()
    tr = Tracker(os.path.join(d, "ep.jsonl"), 0.001)
    legs = [_claim("a"), _claim("b")]
    b = {"id": "x", "kind": "total", "event": "e", "title": "t", "legs": legs}
    good = {"a": {"asks": [(0.40, 20)]}, "b": {"asks": [(0.50, 20)]}}
    bad = {"a": {"asks": [(0.55, 20)]}, "b": {"asks": [(0.50, 20)]}}
    tr.update(b, good, 100.0, 0.3)
    tr.update(b, good, 112.0, 0.3)
    tr.update(b, bad, 114.0, 0.3)
    rows = [json.loads(l) for l in open(os.path.join(d, "ep.jsonl"))]
    assert len(rows) == 1 and rows[0]["seen_s"] == 12.0 and rows[0]["upper_s"] == 14.0 and rows[0]["polls"] == 2


def test_report_gate():
    g = {"min_days": 7, "min_edge": 0.01, "min_profit_usd": 1.0, "min_seen_s": 10, "min_qualifying": 2,
         "min_events": 2, "min_weekly_profit_usd": 5}
    eps = [dict(kind="total", event=f"e{i}", max_edge=0.02, max_profit=3.0, seen_s=12, in_play_at_start=False) for i in range(3)]
    assert summarise(eps, g, 7.0)["verdict"] == "PASS"
    assert summarise(eps, g, 3.0)["verdict"] == "PENDING"
    eps[0]["seen_s"] = 4; eps[1]["seen_s"] = 4
    assert summarise(eps, g, 7.0)["verdict"] == "FAIL"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
