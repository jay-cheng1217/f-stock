# REQ-019 Three-Way MA5 Entry Exit CL3 Backtest

- Generated at: `2026-05-09T09:38:30.577761+00:00`
- Fold artifact: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- Window: `2024-01` to `2026-04`
- Treatment rule: Group B `-5% < price_vs_ma5_at_entry < 0` routes to `stop_only_20d`; Group A/C keep `asymmetric_v2`.
- Simulated trades: `1680`; skipped entries: `0`

## CL3 Summary

| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | MA exits | HWM exits | Stops | 20D timeout | Stop-only routes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Control_AsymmetricV2 | 2.30% | 1.22% | -7.69% | 2.121 | 4.068 | 4.89 | 704 | 70 | 103 | 33 | 0 |
| Treatment_GroupB_StopOnly | 3.73% | 1.89% | -10.90% | 2.013 | 5.063 | 9.77 | 413 | 48 | 175 | 252 | 319 |

## Acceptance Checks

| Check | Value | Threshold | Pass |
|---|---:|---:|:---:|
| alpha_sacrifice | 0.00% | 0.50% | PASS |
| mdd_delta | -3.20% | 0.10% | FAIL |
| sharpe_regression | 5.06% | 5.00% | FAIL |
| calmar_regression | 0.00% | 5.00% | PASS |

**Overall:** `FAIL`

## Group A/B/C Comparison

| Group | Variant | Samples | Avg hold days | Avg return | Win rate | MA5 exit % | Stop-only route % | Stop exit % | 20D timeout % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A_price_vs_ma5_at_entry_gt_0 | Control_AsymmetricV2 | 441 | 6.29 | 3.22% | 41.50% | 76.42% | 0.00% | 17.23% | 6.35% |
| A_price_vs_ma5_at_entry_gt_0 | Treatment_GroupB_StopOnly | 441 | 6.29 | 3.22% | 41.50% | 76.42% | 0.00% | 17.23% | 6.35% |
| B_minus5pct_to_0 | Control_AsymmetricV2 | 319 | 3.58 | 1.32% | 46.08% | 91.22% | 0.00% | 7.21% | 1.57% |
| B_minus5pct_to_0 | Treatment_GroupB_StopOnly | 319 | 16.45 | 5.09% | 55.17% | 0.00% | 100.00% | 29.78% | 70.22% |
| C_le_minus5pct | Control_AsymmetricV2 | 80 | 2.33 | 1.09% | 58.75% | 95.00% | 0.00% | 5.00% | 0.00% |
| C_le_minus5pct | Treatment_GroupB_StopOnly | 80 | 2.33 | 1.09% | 58.75% | 95.00% | 0.00% | 5.00% | 0.00% |

## Notes

- This is a same-snapshot A/B over the Two-Stage N=75 OOF fold artifact.
- Treatment changes exit routing only; entries and allocation count are unchanged.
- Production deployment is blocked unless all CL3 checks pass.
