# RD-005 Factor Exposure Mock (2026-05-17)

## Scope
- Window: 2026-04-23 to 2026-04-29
- Production impact: none; offline mock only.
- Status: `pass_for_review`

## Data Checks
- Holding-day observations: 95
- Effective observations: 95
- Positions / tickers / sectors: 51 / 30 / 10
- Parameter count / limit: 6 / 19
- Missing market factor rows: 0
- Missing sector factor rows: 0

## Mock Result
- Weighted holding return: -11.70%
- Weighted factor return: -0.45%
- Residual after factor: -11.25%
- Bootstrap residual median: -11.25%
- Bootstrap residual 90% interval: -17.31% to -5.85%
- Bootstrap residual sign stability: 100.0%

## Decision
- Can distinguish factor exposure from residual: `True`
- Kill reasons: none

## Top Ticker Residuals
| ticker | weighted residual |
|---|---:|
| 3491 | -1.78% |
| 6693 | -1.18% |
| 6510 | -1.08% |
| 6226 | -0.62% |
| 3498 | -0.60% |
| 2485 | -0.55% |
| 1528 | -0.46% |
| 6640 | -0.45% |
| 6788 | -0.45% |
| 3131 | -0.44% |
