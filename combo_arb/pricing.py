"""Price a basket against real order books: buy one share of every leg per set, cheapest asks
first, while each extra set costs less than the guaranteed $1. Taker fee per share is
rate * p * (1 - p) (Polymarket fee schedule, exponent 1, taker only)."""
from __future__ import annotations


def fee(p: float, rate: float) -> float:
    return rate * p * (1.0 - p)


def top_edge(legs, books) -> float | None:
    """1 - cost of one set at the best asks (incl. fees). None if any leg has no ask."""
    cost = 0.0
    for c in legs:
        asks = (books.get(c.token) or {}).get("asks") or []
        if not asks:
            return None
        p = asks[0][0]
        cost += p + fee(p, c.fee_rate)
    return 1.0 - cost


def walk(legs, books, payoff: float = 1.0):
    """Return (sets, cost, profit) for the largest fill that stays profitable at the margin."""
    lv = []
    for c in legs:
        asks = (books.get(c.token) or {}).get("asks") or []
        if not asks:
            return 0.0, 0.0, 0.0
        lv.append(asks)
    idx = [0] * len(lv)
    rem = [b[0][1] for b in lv]
    sets = cost = 0.0
    while True:
        prices = [lv[i][idx[i]][0] for i in range(len(lv))]
        marginal = sum(p + fee(p, legs[i].fee_rate) for i, p in enumerate(prices))
        if marginal >= payoff - 1e-12:
            break
        q = min(rem)
        if q <= 0:
            break
        sets += q
        cost += marginal * q
        end = False
        for i in range(len(lv)):
            rem[i] -= q
            if rem[i] <= 1e-12:
                idx[i] += 1
                if idx[i] >= len(lv[i]):
                    end = True
                else:
                    rem[i] = lv[i][idx[i]][1]
        if end:
            break
    min_size = max(c.min_size for c in legs)
    if sets < min_size:
        return 0.0, 0.0, 0.0
    return sets, cost, sets * payoff - cost
