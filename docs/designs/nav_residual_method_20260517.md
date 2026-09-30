# SA Design: NAV Unexplained Residual Method (2026-05-17)

## Decision Status

- PM dispatch accepted. No need to return this ticket to PM for rewrite.
- Priority: P0, handle before Stage B production work.
- RD may start the bounded mock only. Formal analysis still waits for mock result review.
- Production model, production gates, attribution bucket schema, sector caps, and 14 guardrail thresholds are out of scope.

## Business Problem

RD-005 split 2026-05-11 to 2026-05-15 NAV residual into:

| Item | Contribution |
|---|---:|
| Execution effect | +1.07pp |
| Exit policy effect | +0.43pp |
| Unexplained residual | -5.64pp |

The unexplained component is larger than the known execution and exit-policy components combined. It must not be labeled as noise without evidence.

## SA Decisions

### 1. Factor Model

Use a constrained holding-level factor attribution, not a portfolio-level unrestricted regression.

Rationale:

- The closure window has only 5 trading days, so portfolio-level CAPM or multi-factor OLS has too little freedom to support a reliable conclusion.
- Holding-level daily rows provide more observations, but they are correlated by date and sector. The method must therefore be bounded and reported with cluster/bootstrap uncertainty.
- A pure single-beta CAPM is too coarse for Champion because prior work shows strategy-fit and sector effects matter.

Selected structure:

1. Market factor: TWII daily return.
2. Strategy-fit / sector factor: same sector proxy used by Champion NAV attribution when available.
3. Holding-specific residual: weighted ticker-level return minus factor-explained return.
4. Optional beta_60 / liquidity exposure only as diagnostics, not as first-pass fitted parameters.

No free sector dummy regression with many sectors is allowed in the 5-day closure window. If RD groups sectors, group them into top exposed sectors plus `OTHER`, and report effective degrees of freedom.

### 2. Sample-Size Policy

Use two windows with different meanings:

| Window | Use |
|---|---|
| 2026-04-23 to 2026-04-29 | Mock validation of method and data availability |
| 2026-05-11 to 2026-05-15 | Closure target window for RD-005 residual |

If the mock window lacks enough holdings, daily marks, or factor return series, RD must stop and report `current data insufficient`. Do not force the method onto the closure window.

### 3. Timing Split

Timing is phase B, after factor exposure.

Only proceed to timing split if phase A leaves `abs(residual_after_factor) >= 2.0pp` or if a single date / ticker cluster explains more than 50% of the remaining residual.

Timing buckets:

1. Overnight: prior close to open.
2. Intraday: open to close.
3. Rebalance / mark timing: position weight changes and stale mark effects.

If open prices or fill timestamps are missing, report timing as data-insufficient rather than inventing a counterfactual.

### 4. OOS Confidence

Use clustered bootstrap as the primary uncertainty method:

- Resample by date and by holding/sector cluster.
- At least 1,000 bootstrap draws for the formal run.
- Report median, 5th percentile, 95th percentile, and sign stability.

Analytic OLS confidence intervals are not sufficient here because the sample is small and correlated.

### 5. Noise Threshold

SA may call the remainder noise only if all conditions hold:

1. `abs(residual_after_factor_timing) < 1.0pp`.
2. Bootstrap 90% interval crosses zero.
3. No single ticker, sector, or date contributes more than 50% of the remaining residual.
4. Reconciliation error is within the existing attribution tolerance.

If the remaining residual is between 1.0pp and 2.0pp, label it `inconclusive / noise-like`, not closed. If it remains >= 2.0pp, continue RCA or return to PM with a data-extension request.

### 6. Dependency Order

Proceed in this order:

1. Factor exposure mock on 2026-04-23 to 2026-04-29.
2. Factor exposure formal run on 2026-05-11 to 2026-05-15 only if mock passes.
3. Timing split only if phase A leaves material residual.
4. OOS/noise assessment only after factor and timing are complete.

If phase A formal result leaves `abs(residual_after_factor) >= 5.0pp`, return to PM before spending time on phase B/C, as requested in the dispatch.

## RD Dispatch

RD is cleared to do mock only.

Expected output:

- `ml/reports/rd005_factor_mock_20260517.md`
- `ml/reports/rd005_factor_mock_20260517.json`
- Supporting CSVs only if needed for factor rows or ticker contributions.

RD task list:

1. Load existing RD-005 NAV attribution artifacts.
2. Build the 2026-04-23 to 2026-04-29 mock factor frame.
3. Verify factor return series availability.
4. Report observation count, parameter count, effective degrees of freedom, and missing data.
5. Report whether the selected model can distinguish factor exposure from residual.
6. Stop after mock and wait for PM/SA review.

Kill criteria:

- Missing factor series.
- Fewer than 20 effective holding-day observations after filtering.
- Parameter count above one fifth of effective observations.
- Bootstrap sign stability below 60% for all tested factor components.

