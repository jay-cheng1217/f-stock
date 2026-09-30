# RD-005 Residual Deepdive Method Lock (2026-05-17)

## Purpose

This document pre-registers the RD-005 formal closure-window analysis after PM sign-off on commit `d0186212`.

It freezes the method used by `scripts/rd005_factor_mock.py` for the 2026-05-11 to 2026-05-15 formal run. RD must not change the model specification, factor construction, bootstrap procedure, or analysis window without a new PM ticket.

## Formal Window

- Start date: 2026-05-11
- End date: 2026-05-15
- Window expansion is forbidden. Do not include 2026-05-04 to 2026-05-15 or any other adjacent dates.

## Method Lock

### Input Frame

- Source DB: `paper_portfolio_v2_champion.db`
- Tables: `unified_positions`, `unified_marks`
- Unit of analysis: holding-day row
- Return field:
  - Use mark-to-mark `close_return_pct` daily delta for normal rows.
  - Use `realized_return_pct` on exit rows when available.
  - Convert to daily holding return by subtracting the prior effective return for the same `position_id`.
- Weight field: `target_weight`

### Factor Specification

The factor return is locked as:

```text
factor_return_pct = 0.5 * market_return_pct + 0.5 * sector_return_pct
```

Where:

- `market_return_pct` is TWII daily return from `index_TWII.csv`.
- `sector_return_pct` is the equal-weight daily return of held tickers in the same sector, built from daily K close returns.
- Sector grouping uses the top 3 sectors by absolute target-weight exposure, plus `OTHER`.

No free-form sector dummy expansion, model retraining, alternative factor library, beta refit, or additional explanatory factor is allowed in this formal run.

### Residual Definition

```text
factor_residual_pct = holding_return_pct - factor_return_pct
weighted_factor_residual_pct = target_weight * factor_residual_pct
```

Formal output must report:

- weighted holding return
- weighted factor return
- residual after factor
- market factor contribution
- sector factor contribution
- sector-group contributions
- top ticker residuals

### Bootstrap Procedure

- Draws: 1,000
- Seed: 20260517
- Procedure: same clustered bootstrap as the mock.
  - Even-numbered draws resample by date.
  - Odd-numbered draws resample by sector group.
- Required statistics:
  - median
  - p05
  - p95
  - sign stability

Do not change draw count, seed, cluster dimensions, or quantile definitions.

## Result Specification

RD must produce:

- `ml/reports/rd005_residual_deepdive_20260517.md`
- `ml/reports/rd005_residual_deepdive_20260517.json`

Required fields:

- `residual_after_factor`
- bootstrap 90% CI
- sign stability
- every factor contribution in pp
- active exposure by factor
- top ticker residual contribution
- daily contribution table
- explicit statement that the report is diagnostic only

## Stop / Escalation Conditions

Stop and return to SA -> PM if:

- any market factor row is missing
- any sector factor row is missing
- effective holding-day observations fall below 20
- parameter count exceeds one fifth of effective observations
- formal run needs a method change to complete

If formal `abs(residual_after_factor) >= 5.0pp`, SA must return to PM before phase B/C timing work.

## Prohibited Conclusions

The formal RD report must not recommend:

- production weight changes
- sector cap changes
- 14 guardrail threshold changes
- model retraining
- Stage B / Stage C promotion

Any action conclusion requires a separate PM ticket.

