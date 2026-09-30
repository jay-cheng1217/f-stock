# AUDIT-002 Step 1 - Label Excess Return Audit

- Generated at: 2026-05-08 21:05
- Scope: verify whether V2 is still trained on absolute 20D return, or already trained on TWII excess 20D return.
- Verdict: Step 1 is already implemented in current V2 training. The weak IC is **not** caused by using absolute-return labels.

## Evidence

| Check | Evidence | Result |
|---|---|---|
| Target builder | `ml/target.py` defines `trade_return_20d` and `trade_excess_return_20d = trade_return_20d - twii_20d_return` | PASS |
| Training target | `scripts/train_v2.py` line 85 sets `target_col = "trade_excess_return_20d"` | PASS |
| Latest model meta | `ml/models/lgbm_v2_20260508_200128_meta.json` has `"target": "trade_excess_return_20d"` | PASS |
| Dataset formula check | implied TWII return std by date max = `2.94e-08`, so excess = raw return - date-level market return | PASS |
| Latest walk-forward run | `ml/reports/v2_backtest_20260508_200128.csv` / `v2_fold_top30_20260508_200128.csv` | Already rerun |

## Step 1 Result

| Metric | Value | PM Hurdle | Result |
|---|---:|---:|---|
| Universe mean IC | 0.0431 | > 0.0500 | FAIL |
| Universe IC t-stat | 2.43 | > 2.00 | PASS |
| Top30 internal IC | -0.0351 | > 0.0000 | FAIL |
| Calibration | Non-monotonic; decile 7/8 negative | Monotonic upward | FAIL |

## Interpretation

The PM hypothesis "label is absolute return, should be excess return" does not match current implementation. V2 already trains on `trade_excess_return_20d`.

This means the previous IC finding remains valid after the label audit:

- The model has weak but statistically positive all-universe screening power.
- Once inside Top30, the ranking is negative/noisy.
- Middle-high predicted deciles remain unreliable.

Important nuance: for a single prediction date, raw 20D return and TWII-excess 20D return have the same cross-sectional rank because the TWII return is a date-level constant. Switching from raw to excess mainly changes cross-date level effects, not the within-date ranking problem.

## PM Decision Implication

Do **not** spend time "changing label to excess return" as a fix. It is already true.

Proceed to AUDIT-002 Step 2:

1. Compute feature-level IC across the full walk-forward dataset.
2. Identify low/negative IC features.
3. Rerun walk-forward after removing weak features.
4. Only if Step 2 fails should ranking objective / two-stage architecture be evaluated.
