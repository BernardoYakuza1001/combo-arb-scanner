# combo-arb-scanner

Read-only research tool. **It never places orders.** It measures whether Polymarket's logically
linked contracts (alternate totals and handicap lines, esports best-of-N series markets, price-hit
ladders) are ever priced so that a basket of 2-3 legs costs less than the $1 it is guaranteed to
pay in every outcome, and how long such gaps last.

- `combo_arb/relations.py` turns markets into threshold claims and finds guaranteed baskets.
- `combo_arb/pricing.py` prices baskets against real order-book depth after taker fees.
- `combo_arb/collector.py` polls books every ~2 s and logs each gap ("episode").
- `REGISTRATION.md` holds the pass/fail gate, fixed before any data.
- `data/report.md` is regenerated after every cloud run (GitHub Actions, every 6 hours).

Background: Shi & Zhai, "Combinatorial Arbitrage in Prediction Markets", arXiv 2610.23517 (2026).
