# RD-005 Residual Deepdive Formal Closure Analysis (2026-05-17)

## Scope
- Window: 2026-05-11 to 2026-05-15
- Production impact: none; diagnostic-only formal closure-window analysis
- Status: `pass_for_review`
- Method lock: fixed 50/50 market-plus-sector constrained holding-level factor attribution.
- Action conclusion: none; PM must open a separate ticket for any production action.

## Data Checks
- Holding-day observations: 132
- Effective observations: 132
- Positions / tickers / sectors: 51 / 45 / 8
- Parameter count / limit: 6 / 26
- Missing market factor rows: 0
- Missing sector factor rows: 0

## Mock Result
- Weighted holding return: +5.90%
- Weighted factor return: -0.14%
- Residual after factor: +6.04%
- Bootstrap residual median: +6.04%
- Bootstrap residual 90% interval: +2.98% to +9.58%
- Bootstrap residual sign stability: 100.0%

## Factor Contributions
| factor | contribution | active exposure | description |
|---|---:|---:|---|
| market_0_5_load | -0.51% | +47.35% | 0.5 * TWII daily return |
| sector_total_0_5_load | +0.38% | +47.35% | 0.5 * held-ticker equal-weight sector proxy |
| sector_group_OTHER | +0.48% | +21.66% | sector component contribution for this locked sector group |
| sector_group_光電業 | +0.46% | +8.33% | sector component contribution for this locked sector group |
| sector_group_半導體業 | -0.22% | +9.31% | sector component contribution for this locked sector group |
| sector_group_電子零組件業 | -0.34% | +8.05% | sector component contribution for this locked sector group |

## Daily Contributions
| date | holding return | factor return | market contribution | sector contribution | residual |
|---|---:|---:|---:|---:|---:|
| 2026-05-11 | +1.70% | +1.56% | +0.22% | +1.34% | +0.14% |
| 2026-05-12 | +1.82% | +1.16% | +0.10% | +1.05% | +0.66% |
| 2026-05-13 | +1.47% | -1.37% | -0.60% | -0.77% | +2.84% |
| 2026-05-14 | +2.08% | +0.46% | +0.45% | +0.01% | +1.63% |
| 2026-05-15 | -1.17% | -1.95% | -0.69% | -1.26% | +0.78% |

## Decision
- Can distinguish factor exposure from residual: `True`
- Kill reasons: none
- Production recommendation: none.

## Top Ticker Residuals
| ticker | weighted residual |
|---|---:|
| 6419 | +3.40% |
| 8027 | +1.18% |
| 3006 | +0.84% |
| 5228 | +0.80% |
| 5475 | +0.58% |
| 4540 | +0.36% |
| 6426 | -0.26% |
| 6903 | -0.23% |
| 4768 | +0.20% |
| 3576 | -0.20% |
