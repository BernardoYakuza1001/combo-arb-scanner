# Carry selection study (MEXC, same-exchange spot + perp)

Symbols: 174. Rebalances: 11 (every 30 days, top 3, equal weight).
APR is on hedged notional, after taker fees on changed coins. No price, borrow or collateral effects.

| Rule | Net APR | Gross APR | Fees (sum) | Realised / trailing-30 | Positive periods |
|---|---|---|---|---|---|
| f30 | 29.4% | 31.5% | 1.91% | 45.6% | 10/11 |
| f90 | 28.8% | 30.6% | 1.60% | 50.9% | 10/11 |
| min3 | 27.4% | 28.7% | 1.19% | 60.8% | 10/11 |
| stable | 27.4% | 28.7% | 1.19% | 60.8% | 10/11 |

Rank correlation of each feature with the next 30 days' funding (mean over rebalances):

- f30: 0.68
- f90: 0.60
- min3: 0.55
- pos90: 0.63

## Picks per period

### f30
- 2025-12-18: UB_USDT, AKE_USDT, FHE_USDT  next-30d 2.21%, fees 0.16%
- 2026-01-17: TRADOOR_USDT, KGEN_USDT, AKE_USDT  next-30d 2.18%, fees 0.21%
- 2026-02-16: SIREN_USDT, TRADOOR_USDT, B2_USDT  next-30d 0.66%, fees 0.21%
- 2026-03-18: ARCSOL_USDT, SKYAI_USDT, AKE_USDT  next-30d 5.31%, fees 0.31%
- 2026-04-17: SKYAI_USDT, AKE_USDT, CLO_USDT  next-30d 6.46%, fees 0.10%
- 2026-05-17: ARIA_USDT, SKYAI_USDT, FOLKS_USDT  next-30d 3.39%, fees 0.21%
- 2026-06-16: CLO_USDT, FOLKS_USDT, SKYAI_USDT  next-30d 2.07%, fees 0.10%
- 2026-07-16: SIREN_USDT, AKE_USDT, CLO_USDT  next-30d 3.24%, fees 0.21%
- 2026-08-15: BTW_USDT, SIREN_USDT, ARCSOL_USDT  next-30d 6.12%, fees 0.21%
- 2026-09-14: BTW_USDT, SIREN_USDT, CLO_USDT  next-30d 4.40%, fees 0.10%
- 2026-10-14: SIREN_USDT, BTW_USDT, KOMA_USDT  next-30d -7.57%, fees 0.10%

### f90
- 2025-12-18: B2_USDT, SIREN_USDT, AKE_USDT  next-30d 2.62%, fees 0.16%
- 2026-01-17: B2_USDT, 4_USDT, KGEN_USDT  next-30d 2.07%, fees 0.21%
- 2026-02-16: 4_USDT, AKE_USDT, UB_USDT  next-30d 2.16%, fees 0.21%
- 2026-03-18: ARCSOL_USDT, SKYAI_USDT, TRADOOR_USDT  next-30d 4.19%, fees 0.31%
- 2026-04-17: ARCSOL_USDT, SKYAI_USDT, AKE_USDT  next-30d 6.23%, fees 0.10%
- 2026-05-17: SKYAI_USDT, AKE_USDT, ARCSOL_USDT  next-30d 2.36%, fees 0.00%
- 2026-06-16: SKYAI_USDT, CLO_USDT, AKE_USDT  next-30d 2.78%, fees 0.10%
- 2026-07-16: SIREN_USDT, SKYAI_USDT, ARIA_USDT  next-30d 3.52%, fees 0.21%
- 2026-08-15: SIREN_USDT, BTW_USDT, CLO_USDT  next-30d 6.58%, fees 0.21%
- 2026-09-14: SIREN_USDT, BTW_USDT, BULLA_USDT  next-30d 4.65%, fees 0.10%
- 2026-10-14: BTW_USDT, SIREN_USDT, BULLA_USDT  next-30d -9.54%, fees 0.00%

### min3
- 2025-12-18: AKE_USDT, SIREN_USDT, B2_USDT  next-30d 2.62%, fees 0.16%
- 2026-01-17: 4_USDT, AKE_USDT, B2_USDT  next-30d 2.46%, fees 0.10%
- 2026-02-16: AKE_USDT, 4_USDT, B2_USDT  next-30d 2.30%, fees 0.00%
- 2026-03-18: ARCSOL_USDT, SKYAI_USDT, AKE_USDT  next-30d 5.31%, fees 0.21%
- 2026-04-17: SKYAI_USDT, ARCSOL_USDT, AKE_USDT  next-30d 6.23%, fees 0.00%
- 2026-05-17: SKYAI_USDT, AKE_USDT, ARCSOL_USDT  next-30d 2.36%, fees 0.00%
- 2026-06-16: CLO_USDT, SKYAI_USDT, JCT_USDT  next-30d 2.19%, fees 0.21%
- 2026-07-16: CLO_USDT, JCT_USDT, TRIA_USDT  next-30d 2.83%, fees 0.10%
- 2026-08-15: CLO_USDT, JCT_USDT, TRIA_USDT  next-30d 3.36%, fees 0.00%
- 2026-09-14: SIREN_USDT, BTW_USDT, BULLA_USDT  next-30d 4.65%, fees 0.31%
- 2026-10-14: SIREN_USDT, BTW_USDT, ZEST_USDT  next-30d -8.35%, fees 0.10%

### stable
- 2025-12-18: AKE_USDT, SIREN_USDT, B2_USDT  next-30d 2.62%, fees 0.16%
- 2026-01-17: 4_USDT, AKE_USDT, B2_USDT  next-30d 2.46%, fees 0.10%
- 2026-02-16: AKE_USDT, 4_USDT, B2_USDT  next-30d 2.30%, fees 0.00%
- 2026-03-18: ARCSOL_USDT, SKYAI_USDT, AKE_USDT  next-30d 5.31%, fees 0.21%
- 2026-04-17: SKYAI_USDT, ARCSOL_USDT, AKE_USDT  next-30d 6.23%, fees 0.00%
- 2026-05-17: SKYAI_USDT, AKE_USDT, ARCSOL_USDT  next-30d 2.36%, fees 0.00%
- 2026-06-16: CLO_USDT, SKYAI_USDT, JCT_USDT  next-30d 2.19%, fees 0.21%
- 2026-07-16: CLO_USDT, JCT_USDT, TRIA_USDT  next-30d 2.83%, fees 0.10%
- 2026-08-15: CLO_USDT, JCT_USDT, TRIA_USDT  next-30d 3.36%, fees 0.00%
- 2026-09-14: SIREN_USDT, BTW_USDT, BULLA_USDT  next-30d 4.65%, fees 0.31%
- 2026-10-14: SIREN_USDT, BTW_USDT, ZEST_USDT  next-30d -8.35%, fees 0.10%

## Limits

- Survivorship: only currently listed perps are fetched; delisted coins (often the worst carry trades) are missing.
- Collateral ratios are today's, not historical; the paper trade's collateral filter is not applied here.
- Funding only: spot/perp basis moves, borrow costs and liquidation risk are not modelled.
