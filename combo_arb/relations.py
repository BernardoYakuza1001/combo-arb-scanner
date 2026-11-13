"""Turn Polymarket markets into threshold claims on one quantity, then find baskets that
pay at least $1 in every possible outcome.

A claim is "X > c" (UP) or "X < c" (DN) on a family's quantity X, with an equality rule.
Families come only from structured metadata (sportsMarketType + line, or "↑/↓ price"
ladders inside one event), never from free-text guessing, so a basket's payoff is
mechanical. Anything that does not parse cleanly is skipped and counted.

Kinds of family:
  total   (sports) total points / rounds / maps for a scope, integer-valued
  margin  (sports) team A minus team B for a scope, integer-valued, A = alphabetical first
  series  (esports BOn) joint (maps won by A, maps won by B), exact score enumeration
  hit_up / hit_dn  (price ladders) max / min price in the event's window, continuous
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field

EPS = 1e-6


def loads(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


@dataclass(frozen=True)
class Claim:
    token: str          # CLOB token id
    side: str           # "UP" (X > c) or "DN" (X < c)
    c: float
    inclusive: bool     # True: equality counts as a win (">=" / "<=")
    eq_half: bool       # True: equality pays 0.5 (sports push/50-50 rule)
    label: str          # human-readable
    market: str         # market slug
    fee_rate: float
    min_size: float
    tick: float
    delay_s: float
    start: str          # game/event start time (ISO) or ""

    def payoff_scalar(self, x: float) -> float:
        if abs(x - self.c) < EPS:
            if self.eq_half:
                return 0.5
            return 1.0 if self.inclusive else 0.0
        return 1.0 if ((x > self.c) == (self.side == "UP")) else 0.0


@dataclass
class Family:
    key: str
    kind: str                       # total | margin | series | hit_up | hit_dn
    event: str
    title: str
    claims: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)   # for series: list of (a, b)
    payoff: dict = field(default_factory=dict)     # series: token -> payoff vector


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _common(m: dict, ev: dict) -> dict:
    fs = m.get("feeSchedule") or {}
    rate = _f(fs.get("rate"), 0.05) if m.get("feesEnabled", True) else 0.0
    return dict(market=m.get("slug", ""), fee_rate=rate, min_size=_f(m.get("orderMinSize"), 5.0),
                tick=_f(m.get("orderPriceMinTickSize"), 0.01), delay_s=_f(m.get("secondsDelay"), 0.0),
                start=str(m.get("gameStartTime") or ev.get("startTime") or ""))


ALLOW_CLOSED = False   # history mode reads settled markets


def tradable(m: dict) -> bool:
    if ALLOW_CLOSED:
        toks = loads(m.get("clobTokenIds")) or []
        return len(toks) == 2 and m.get("enableOrderBook") is not False
    if m.get("closed") or m.get("active") is False or m.get("archived"):
        return False
    if m.get("enableOrderBook") is False or m.get("acceptingOrders") is False:
        return False
    toks = loads(m.get("clobTokenIds")) or []
    return len(toks) == 2


# ---------- sports ----------

_GAME_RE = re.compile(r"\b(?:Map|Game)\s+(\d+)\b", re.I)
_BO_RE = re.compile(r"\bBO(\d)\b", re.I)


def sports_scope(stype: str, question: str):
    """Return (quantity, scope) or None. quantity in {'total','margin'}."""
    s = stype.lower()
    if s == "moneyline":
        return ("margin", "match")
    if s == "child_moneyline":
        g = _GAME_RE.search(question)
        return ("margin", f"game{g.group(1)}") if g else None
    if s in ("totals", "map_handicap", "spreads"):
        return ("total" if s == "totals" else "margin", "match")
    m = re.fullmatch(r"(round_)?(over_under|handicap)_game_(\d+)", s)
    if m:
        return ("total" if m.group(2) == "over_under" else "margin", f"game{m.group(3)}")
    # other types (halves, quarters, props) are not modelled: their scopes are ambiguous
    return None


def _team_names(outs):
    if not outs or len(outs) != 2:
        return None
    if {o.lower() for o in outs} & {"yes", "no", "over", "under", "draw", "tie"}:
        return None
    return outs


def sports_claims(m: dict, ev: dict, stats: dict):
    """Yield (quantity, scope, teams_or_None, [claim_yes, claim_no]) for one market."""
    stype = m.get("sportsMarketType") or ""
    sc = sports_scope(stype, m.get("question", ""))
    if not sc:
        stats["sports_unmodelled_type"] = stats.get("sports_unmodelled_type", 0) + 1
        return None
    qty, scope = sc
    toks = [str(t) for t in loads(m.get("clobTokenIds"))]
    outs = loads(m.get("outcomes")) or []
    com = _common(m, ev)
    line = m.get("line")
    q = m.get("question", "")
    if qty == "total":
        if line is None or [o.lower() for o in outs] not in (["over", "under"], ["under", "over"]):
            stats["sports_bad_total"] = stats.get("sports_bad_total", 0) + 1
            return None
        c = float(line)
        eqh = abs(c - round(c)) < EPS            # whole-number line: exact hit resolves 50-50
        up = Claim(toks[0] if outs[0].lower() == "over" else toks[1], "UP", c, False, eqh, f"{q} Over", **com)
        dn = Claim(toks[1] if outs[0].lower() == "over" else toks[0], "DN", c, False, eqh, f"{q} Under", **com)
        return qty, scope, None, [up, dn]
    teams = _team_names(outs)
    if not teams:
        stats["sports_bad_margin"] = stats.get("sports_bad_margin", 0) + 1
        return None
    L = 0.0 if stype.endswith("moneyline") else (float(line) if line is not None else None)
    if L is None:
        stats["sports_bad_margin"] = stats.get("sports_bad_margin", 0) + 1
        return None
    # cross-check the line against the question text "(−k)" when present
    mm = re.search(r"\(([-+]\d+(?:\.\d+)?)\)", q)
    if mm and abs(float(mm.group(1)) - L) > EPS and abs(float(mm.group(1)) + L) > EPS:
        stats["sports_line_mismatch"] = stats.get("sports_line_mismatch", 0) + 1
        return None
    if abs(L * 2 - round(L * 2)) > EPS:               # quarter lines split stakes: not modelled
        stats["sports_quarter_line"] = stats.get("sports_quarter_line", 0) + 1
        return None
    A, B = sorted(teams)
    eqh = abs(L - round(L)) < EPS                     # whole-number line / moneyline tie -> 50-50
    t0, t1 = toks
    # outcome[0] wins iff margin_outcome0 + L > 0, i.e. margin_outcome0 > -L
    if teams[0] == A:          # margin_A > -L
        yes = Claim(t0, "UP", -L, False, eqh, f"{q} -> {teams[0]}", **com)
        no = Claim(t1, "DN", -L, False, eqh, f"{q} -> {teams[1]}", **com)
    else:                      # margin_B > -L  <=>  margin_A < L
        yes = Claim(t0, "DN", L, False, eqh, f"{q} -> {teams[0]}", **com)
        no = Claim(t1, "UP", L, False, eqh, f"{q} -> {teams[1]}", **com)
    return qty, scope, (A, B), [yes, no]


def _series_length(ev: dict, fam_claims) -> int | None:
    m = _BO_RE.search(ev.get("title", "") or "")
    if m:
        n = int(m.group(1))
        return n if n % 2 == 1 else None
    return None


def build_sports(ev: dict, stats: dict) -> list[Family]:
    by = {}
    is_esports = any((t.get("slug") or "") == "esports" for t in ev.get("tags") or [] if isinstance(t, dict))
    for m in ev.get("markets") or []:
        if not m.get("sportsMarketType") or not tradable(m):
            continue
        r = sports_claims(m, ev, stats)
        if not r:
            continue
        qty, scope, teams, cl = r
        by.setdefault((qty, scope), {"teams": set(), "claims": []})
        if teams:
            by[(qty, scope)]["teams"].add(teams)
        by[(qty, scope)]["claims"] += cl
    fams = []
    slug, title = ev.get("slug", ""), ev.get("title", "")
    for (qty, scope), v in by.items():
        if len(v["teams"]) > 1:                         # inconsistent team names: skip
            stats["sports_team_conflict"] = stats.get("sports_team_conflict", 0) + 1
            continue
        fams.append(Family(f"{slug}|{qty}|{scope}", qty, slug, title, v["claims"]))
    # esports series: maps margin (moneyline + map handicap) and maps total jointly
    n = _series_length(ev, None) if is_esports else None
    if n:
        mar = by.get(("margin", "match"), {"claims": [], "teams": set()})
        tot = by.get(("total", "match"), {"claims": [], "teams": set()})
        if len(mar["teams"]) <= 1 and mar["claims"] and tot["claims"]:
            w = n // 2 + 1
            scores = [(w, k) for k in range(w)] + [(k, w) for k in range(w)]
            f = Family(f"{slug}|series|bo{n}", "series", slug, title, mar["claims"] + tot["claims"], scores)
            for c in mar["claims"]:
                f.payoff[c.token] = [c.payoff_scalar(a - b) for a, b in scores]
            for c in tot["claims"]:
                f.payoff[c.token] = [c.payoff_scalar(a + b) for a, b in scores]
            fams.append(f)
    return fams


# ---------- price-hit ladders ----------

_HIT_RE = re.compile(r"^\s*([↑↓])\s*\$?([\d,]+(?:\.\d+)?)\s*([kKmM]?)\s*$")


def build_ladders(ev: dict, stats: dict) -> list[Family]:
    ups, dns = [], []
    for m in ev.get("markets") or []:
        if not tradable(m):
            continue
        g = _HIT_RE.match(m.get("groupItemTitle") or "")
        if not g:
            continue
        outs = [o.lower() for o in (loads(m.get("outcomes")) or [])]
        if outs != ["yes", "no"]:
            stats["ladder_bad_outcomes"] = stats.get("ladder_bad_outcomes", 0) + 1
            continue
        v = float(g.group(2).replace(",", "")) * {"": 1, "k": 1e3, "m": 1e6}[g.group(3).lower()]
        toks = [str(t) for t in loads(m.get("clobTokenIds"))]
        com = _common(m, ev)
        q = m.get("question", "")
        if g.group(1) == "↑":      # YES iff max >= v
            ups += [Claim(toks[0], "UP", v, True, False, f"{q} YES", **com),
                    Claim(toks[1], "DN", v, False, False, f"{q} NO", **com)]
        else:                      # YES iff min <= v
            dns += [Claim(toks[0], "DN", v, True, False, f"{q} YES", **com),
                    Claim(toks[1], "UP", v, False, False, f"{q} NO", **com)]
    slug, title = ev.get("slug", ""), ev.get("title", "")
    out = []
    if len(ups) >= 4:
        out.append(Family(f"{slug}|hit_up", "hit_up", slug, title, ups))
    if len(dns) >= 4:
        out.append(Family(f"{slug}|hit_dn", "hit_dn", slug, title, dns))
    return out


# ---------- baskets ----------

def outcome_points(fam: Family) -> list[float]:
    cs = sorted({c.c for c in fam.claims})
    if fam.kind in ("total", "margin"):              # integer-valued quantities
        lo, hi = int(min(cs)) - 2, int(max(cs)) + 2
        pts = list(range(lo, hi + 1))
        if fam.kind == "total":
            pts = [p for p in pts if p >= 0] or [0]
        return [float(p) for p in pts]
    pts = [cs[0] - 1.0, cs[-1] + 1.0]                # continuous: every gap and every edge
    for a, b in zip(cs, cs[1:]):
        pts.append((a + b) / 2)
    for c in cs:
        pts += [c, c - 1e-3, c + 1e-3]
    return sorted(set(pts))


def payoff_vectors(fam: Family) -> dict[str, list[float]]:
    if fam.kind == "series":
        return fam.payoff
    pts = outcome_points(fam)
    return {c.token: [c.payoff_scalar(x) for x in pts] for c in fam.claims}


def find_baskets(fam: Family, max_legs: int = 3) -> list[tuple]:
    """Minimal sets of distinct markets' tokens whose summed payoff is >= 1 everywhere.
    Same-market YES+NO pairs are excluded (the radar's complete-set track covers them)."""
    vec = payoff_vectors(fam)
    claims = {c.token: c for c in fam.claims if c.token in vec}
    toks = sorted(claims)
    found = []
    for k in range(2, max_legs + 1):
        for combo in itertools.combinations(toks, k):
            mkts = [claims[t].market for t in combo]
            if len(set(mkts)) < k:
                continue
            if any(set(b) <= set(combo) for b in found):
                continue
            tot = [sum(vec[t][i] for t in combo) for i in range(len(vec[combo[0]]))]
            if min(tot) >= 1.0 - 1e-9:
                found.append(combo)
    return found


def discover(events: list[dict], max_legs: int = 3):
    """Return (families, baskets, stats). baskets: list of dicts with legs (Claim) and family."""
    stats, fams = {}, []
    for ev in events:
        fams += build_sports(ev, stats)
        fams += build_ladders(ev, stats)
    baskets, seen = [], set()
    # simple 1-D families first, so a basket shared with a series family keeps the simpler label
    for f in sorted(fams, key=lambda f: f.kind == "series"):
        cl = {c.token: c for c in f.claims}
        # 1-D families: any guaranteed triple contains a guaranteed pair (the lowest UP ray and the
        # highest DN ray already cover every point), so pairs are complete there. Series need triples.
        legs = max_legs if f.kind == "series" else 2
        for combo in find_baskets(f, legs):
            if frozenset(combo) in seen:
                continue
            seen.add(frozenset(combo))
            baskets.append({"id": f.key + "|" + "+".join(sorted(t[-8:] for t in combo)),
                            "family": f.key, "kind": f.kind, "event": f.event, "title": f.title,
                            "legs": [cl[t] for t in combo]})
    stats["families"] = len(fams)
    stats["baskets"] = len(baskets)
    return fams, baskets, stats
