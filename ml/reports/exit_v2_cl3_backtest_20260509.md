# REQ-013 Exit Policy v2 CL3 Backtest

- Generated at: `2026-05-08T18:45:23.849694+00:00`
- Fold artifact: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- Window: `2024-01` to `2026-04`
- Entry snapshots: `28` months x Top30
- Strong trend rule: `price_vs_ma20 > 5.00%` and `two_stage_rank <= 10`
- Simulated trades: `1680`; skipped entries: `0`

## CL3 Summary

| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | Strong no-MA5 | MA exits | HWM exits | Stops | 20D timeout |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Control_MA5_HWM8 | 1.57% | 0.71% | -8.57% | 1.660 | 2.397 | 4.01 | 0 | 806 | 10 | 33 | 1 |
| Treatment_AsymmetricV2 | 2.30% | 1.22% | -7.69% | 2.121 | 4.068 | 4.89 | 109 | 704 | 70 | 103 | 33 |

## Acceptance Checks

| Check | Value | Threshold | Pass |
|---|---:|---:|:---:|
| alpha_sacrifice | 0.00% | 0.50% | PASS |
| mdd_delta | 0.88% | 0.10% | PASS |
| sharpe_regression | 0.00% | 5.00% | PASS |
| calmar_regression | 0.00% | 5.00% | PASS |

**Overall:** `PASS`

## Notes

- Both branches share identical Two-Stage N=75 walk-forward OOF Top30 entries.
- Treatment keeps -10% intraday stop, 8% HWM trail, and 20D timeout for every position.
- Treatment disables MA5_BREAK only for positions satisfying the strong trend predicate.
- No threshold tuning was applied in this run.
