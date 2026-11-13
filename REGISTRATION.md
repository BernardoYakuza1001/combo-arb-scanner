# P21 Combinatorial prediction-market arbitrage — pre-registration

Registered 13 Nov 2026, before any live data. Gates are not edited after data is seen.

## Hypothesis
Polymarket lists several logically linked contracts for the same quantity (alternate totals and
handicap lines, moneyline vs map handicap vs games total in esports series, price-hit ladders).
Because each is a separate order book, their asks can drift apart so that a basket of 2-3 legs
costs less than the $1 it is guaranteed to pay in every outcome. Source: Shi & Zhai,
"Combinatorial Arbitrage in Prediction Markets" (arXiv 2610.23517, Oct 2026): 2.2% guaranteed
profit on committed capital after spreads and fees in historical book replays. A secondary
summary we received claims median windows of ~6 s (sports) and ~31 s (semantic); not verified
from the paper itself.

## What counts as a guaranteed basket
Only relations read from structured metadata: `sportsMarketType` + `line` + outcomes (sports),
best-of-N score enumeration (esports series), and `↑/↓ price` rungs inside one event (ladders).
Every basket is checked over every possible outcome, including 50-50 resolutions (cancellations,
exact hits on whole-number lines). Cross-event relations (e.g. "hit in November" vs "hit in 2026")
are NOT used: different events can use different resolution sources or windows.

## Measurement
Books polled every ~2 s for baskets within 3% of profit, every ~6 cycles for the rest.
Cost per set = asks + taker fee rate * p * (1 - p) per leg (rate from each market's feeSchedule).
An episode is a run of polls where a set costs <= 0.999 and >= the 5-share minimum is fillable.
Duration "seen_s" = first to last poll at which it was open (a lower bound).

## Gate (all must hold, after >= 7 days of collection)
| Criterion | Threshold | Why |
|---|---|---|
| Qualifying episode | edge >= 1.0% after fees, >= $1 executable profit, seen open >= 10 s | 10 s is the least a simple script needs to send 2-3 sequential taker orders, including the 1 s in-play order delay; below 1% one adverse tick erases the edge |
| Count | >= 30 qualifying episodes | enough for a median and to rule out a one-off glitch |
| Breadth | >= 5 distinct events | not one broken market |
| Size | >= $70 per week of qualifying executable profit | ~$10/day: the least that justifies building and babysitting an executor |

## Known limits
- Legs fill one after another (no atomic multi-leg order): leg risk is real even inside a window.
- Polling misses windows shorter than the poll cycle, so short-window counts are understated.
- Executable profit at the best moment is an upper bound; competing bots may take it first.
- Capital is locked until resolution (hours for sports, weeks for ladders).
- Markets flagged `restricted` may be unavailable in some jurisdictions; check access before trading.
