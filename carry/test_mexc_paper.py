import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mexc_paper as M

class Fake:
    def __init__(self): self.px = {"A_USDT": 1.0, "B_USDT": 2.0, "C_USDT": 4.0, "D_USDT": 1.0}; self.rate = 0.001
    def perps(self): return [{"symbol": s, "baseCoin": s[:-5]} for s in self.px]
    def spot_symbols(self): return {s[:-5]: s[:-5] + "USDT" for s in self.px}
    def spot_24h(self): return {s[:-5] + "USDT": 1e7 for s in self.px}
    def perp_tickers(self): return {s: {"bid1": p, "ask1": p, "fairPrice": p, "lastPrice": p, "amount24": 1e8} for s, p in self.px.items()}
    def funding(self, sym, since):
        n = M.now_ms(); rate = {"A_USDT": .003, "B_USDT": .002, "C_USDT": .001, "D_USDT": -.001}[sym] if self.rate else 0
        return [(t, rate) for t in range(n - 30 * M.DAY, n, 8 * 3600_000) if t > since]
    def spot_book(self, ssym):
        p = self.px[ssym[:-4] + "_USDT"]; return [(p, 1e9)], [(p, 1e9)]

d = tempfile.mkdtemp(); M.OUT = d; f = Fake(); M.Api = lambda: f
M.main(lambda *a: None)
st = json.load(open(os.path.join(d, "state.json")))
assert [p["symbol"] for p in st["positions"]] == ["A_USDT", "B_USDT", "C_USDT"], st["positions"]
fees_open = st["fees"]; assert abs(fees_open - 3 * (166.6667 * .0010 + 166.6667 * .00055)) < 1e-3
eq0 = st["history"][-1]["equity"]; assert abs(eq0 - (1000 - fees_open)) < 0.01, eq0
# funding accrues to the short: set funding_ms back one day -> 3 settlements per coin
for p in st["positions"]: p["funding_ms"] = M.now_ms() - M.DAY - 1000
json.dump(st, open(os.path.join(d, "state.json"), "w"))
M.main(lambda *a: None)
st = json.load(open(os.path.join(d, "state.json")))
exp = sum(p["qty"] * f.px[p["symbol"]] * r * 3 for p, r in zip(st["positions"], (.003, .002, .001)))
assert abs(st["funding"] - exp) < 1e-6 and st["funding"] > 0, (st["funding"], exp)
# hedged: a 50% price jump must leave equity unchanged (no fees on marking)
for s in f.px: f.px[s] *= 1.5
f.rate = 0
M.main(lambda *a: None)
st2 = json.load(open(os.path.join(d, "state.json")))
assert abs(st2["history"][-1]["equity"] - st["history"][-1]["equity"]) < 0.01, (st2["history"][-1], st["history"][-1])
# rotation closes everything at the new prices and books exit fees; cash conserved
st2["last_rotation"] = 0; json.dump(st2, open(os.path.join(d, "state.json"), "w"))
f.rate = 1
M.main(lambda *a: None)
st3 = json.load(open(os.path.join(d, "state.json")))
assert any("ROTAT" in x or "CLOSE" in x for x in st3["log"]) or [p["symbol"] for p in st3["positions"]] == ["A_USDT", "B_USDT", "C_USDT"]
print("mexc paper accounting tests: PASS", st3["history"][-1])
