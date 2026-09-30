# W1 StopOnly CL3 Backtest

- Generated at: `2026-05-08T12:44:19.498067+00:00`
- Fold artifact: `ml/reports/v2_fold_top30_20260508_200128.csv`
- Window: `2024-01` to `2026-04`
- Entry snapshots: `28` months x Top30

## Performance

| Variant | Monthly ret | Alpha | MDD | Sharpe | Calmar | Avg hold | MA exits | HWM exits | Stop exits | 20D |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline_MA5_HWM8 | +0.62% | -0.17% | -8.72% | 0.712 | 0.887 | 3.82 | 805 | 13 | 34 | 1 |
| Test_StopOnly | +1.59% | -1.00% | -25.19% | 0.774 | 0.827 | 14.70 | 0 | 0 | 352 | 488 |

## CL3

| Check | Value | Threshold | Pass |
|---|---:|---:|---|
| alpha_sacrifice | +0.00% | +0.50% | yes |
| mdd_delta | -16.47% | +0.10% | no |
| sharpe_regression | +0.00% | +5.00% | yes |
| calmar_regression | +6.75% | +5.00% | no |

Overall: FAIL

Decision: rollback production default to `ma5_break_plus_hwm_8pct`.

## Notes

- Baseline explicitly enables legacy MA-break exits; the failed CL3 result restored this as the production default.
- Both branches share identical regenerated walk-forward fold Top30 entry snapshots.
- Stop-loss simulation uses production `simulate_intraday_stop` behavior.
