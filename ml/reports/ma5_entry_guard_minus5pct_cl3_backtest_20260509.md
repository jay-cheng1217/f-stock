# REQ-016 MA5 Entry Guard CL3 Backtest

- Generated at: `2026-05-09T14:47:16.034057+00:00`
- Source trades: `F:\stock\ml\reports\exit_v2_cl3_backtest_20260509_trades.csv`
- Threshold: `price_vs_ma5_at_entry >= -5.00%`
- Window: `2024-01` to `2026-04`
- Simulated baseline trades: `840`; removed by guard: `81`

## CL3 Summary

| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | Trades | Removed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Control_AsymmetricV2 | 2.30% | 1.22% | -7.69% | 2.121 | 4.068 | 4.89 | 840 | 0 |
| Treatment_MA5EntryGuard | 2.28% | 1.28% | -9.79% | 1.818 | 3.180 | 5.08 | 759 | 81 |

## Acceptance Checks

| Check | Value | Threshold | Pass |
|---|---:|---:|:---:|
| alpha_sacrifice | 0.01% | 0.50% | PASS |
| mdd_delta | -2.09% | 0.10% | FAIL |
| sharpe_regression | 14.27% | 5.00% | FAIL |
| calmar_regression | 21.83% | 5.00% | FAIL |

**Overall:** `FAIL`

## Entry MA5 Group Analysis

| Group | Samples | Kept | Removed | Removed % | Baseline avg return | Kept avg return | Removed avg return | MA5_BREAK exit % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A_price_vs_ma5_at_entry_gt_0 | 441 | 441 | 0 | 0.00% | 3.22% | 3.22% | n/a | 76.42% |
| B_minus5pct_to_0 | 318 | 318 | 0 | 0.00% | 1.33% | 1.33% | n/a | 91.19% |
| C_le_minus5pct | 81 | 0 | 81 | 100.00% | 1.04% | n/a | 1.04% | 95.06% |

## Notes

- This is a same-snapshot A/B over the existing REQ-013 asymmetric_v2 OOF trade artifact.
- Treatment removes weak MA5 entries and re-normalizes among surviving names within each month.
- If a month has no surviving names, that month is treated as cash return 0.
