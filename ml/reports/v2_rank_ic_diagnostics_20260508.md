# V2 Rank IC Diagnostics

- Generated at: 2026-05-08 20:59:17
- Monthly IC source: `ml\reports\v2_backtest_20260508_200128.csv`
- Calibration source: `ml\reports\v2_fold_top30_20260508_200128.csv`
- IC scope: all-universe monthly last snapshot, target = `trade_excess_return_20d`.
- Calibration scope: fold Top30 only, because the persisted fold artifact does not contain full-universe predictions.

## IC Summary

| Metric | Value |
|---|---:|
| Months | 28 |
| Mean IC | 0.0431 |
| IC Std | 0.0940 |
| IC t-stat | 2.43 |
| IC Sharpe | 0.46 |
| Positive months | 19/28 (68%) |
| Significant monthly IC (abs t >= 2) | 14/28 (50%) |
| Mean IC > 0.05 | FAIL |
| t-stat > 2 | PASS |

## Interpretation

- Verdict: IC is statistically positive, but mean IC is below the 0.05 hurdle. Treat the model as weak-positive rather than strongly reliable.
- Top30 internal rank IC mean: -0.0351 (t-stat -0.70). This is weaker than all-universe IC.

## Yearly IC

| Year | Months | Mean IC |
|---|---:|---:|
| 2024 | 12 | 0.0572 |
| 2025 | 12 | -0.0056 |
| 2026 | 4 | 0.1470 |

## Monthly IC

| Month | N | IC | t-stat | Significant? |
|---|---:|---:|---:|---|
| 2024-01 | 1094 | 0.0551 | 1.82 | NO |
| 2024-02 | 1095 | 0.1406 | 4.69 | YES |
| 2024-03 | 1097 | 0.0853 | 2.83 | YES |
| 2024-04 | 1098 | -0.0322 | -1.07 | NO |
| 2024-05 | 1099 | 0.0696 | 2.31 | YES |
| 2024-06 | 1101 | 0.0717 | 2.38 | YES |
| 2024-07 | 1101 | 0.0836 | 2.78 | YES |
| 2024-08 | 1102 | 0.0795 | 2.65 | YES |
| 2024-09 | 1103 | 0.1267 | 4.24 | YES |
| 2024-10 | 1105 | -0.0113 | -0.37 | NO |
| 2024-11 | 1106 | 0.0041 | 0.14 | NO |
| 2024-12 | 1107 | 0.0136 | 0.45 | NO |
| 2025-01 | 1107 | -0.0096 | -0.32 | NO |
| 2025-02 | 1107 | 0.0060 | 0.20 | NO |
| 2025-03 | 1107 | -0.0740 | -2.47 | YES |
| 2025-04 | 1107 | -0.0379 | -1.26 | NO |
| 2025-05 | 1109 | 0.1426 | 4.79 | YES |
| 2025-06 | 1111 | 0.0166 | 0.55 | NO |
| 2025-07 | 1112 | 0.0104 | 0.35 | NO |
| 2025-08 | 1113 | 0.0621 | 2.07 | YES |
| 2025-09 | 1113 | 0.0521 | 1.74 | NO |
| 2025-10 | 1113 | -0.0179 | -0.60 | NO |
| 2025-11 | 1113 | -0.1347 | -4.53 | YES |
| 2025-12 | 1113 | -0.0824 | -2.76 | YES |
| 2026-01 | 1113 | -0.0122 | -0.41 | NO |
| 2026-02 | 1113 | 0.0447 | 1.49 | NO |
| 2026-03 | 1113 | 0.2335 | 8.01 | YES |
| 2026-04 | 1113 | 0.3219 | 11.33 | YES |

## Calibration Deciles

| Decile | N | Pred excess | Actual excess | Actual raw | Positive rate |
|---:|---:|---:|---:|---:|---:|
| 1 | 102 | -0.67% | -2.57% | +3.53% | 52% |
| 2 | 72 | -0.03% | -1.39% | -0.46% | 39% |
| 3 | 78 | +0.33% | -3.20% | +1.22% | 47% |
| 4 | 84 | +0.69% | -0.00% | +3.39% | 58% |
| 5 | 85 | +0.97% | +3.83% | +6.02% | 62% |
| 6 | 83 | +1.49% | +4.03% | +7.53% | 60% |
| 7 | 84 | +2.12% | -3.14% | +0.83% | 51% |
| 8 | 84 | +3.08% | -2.72% | +0.42% | 40% |
| 9 | 84 | +4.59% | +1.01% | +4.10% | 45% |
| 10 | 84 | +8.54% | +5.66% | +15.54% | 68% |

## Charts

- Rank IC by month: `F:\stock\ml\reports\v2_rank_ic_diagnostics_20260508_rank_ic_by_month.svg`
- Prediction calibration deciles: `F:\stock\ml\reports\v2_rank_ic_diagnostics_20260508_calibration_deciles.svg`
- IC stability: `F:\stock\ml\reports\v2_rank_ic_diagnostics_20260508_ic_stability.svg`
