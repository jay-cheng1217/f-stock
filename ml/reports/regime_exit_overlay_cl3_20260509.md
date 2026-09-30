# REQ-021 Market-Regime Exit Overlay CL3

- Generated at: `2026-05-09T15:24:44.464439+00:00`
- Fold artifact: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- Window: `2024-01` to `2026-04`
- TWII regime source: `csv:F:\stock\大盤指數\index_TWII.csv`
- Simulated trades: `4200`; skipped entries: `0`
- Control: current `asymmetric_v2`.
- Variant D keeps HWM 8% while disabling MA5_BREAK in strong TWII regime.

## CL3 Summary

| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | Regime routes | MA exits | HWM exits | Stops | 20D timeout |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Control_AsymmetricV2 | 2.30% | 1.22% | -7.69% | 2.121 | 4.068 | 4.89 | 0 | 704 | 70 | 103 | 33 |
| Variant_A_regime_ma20_3pct | 2.61% | 1.00% | -7.69% | 2.371 | 4.702 | 6.19 | 120 | 618 | 52 | 124 | 98 |
| Variant_B_regime_ma20_5pct | 2.53% | 1.09% | -7.69% | 2.337 | 4.540 | 5.83 | 90 | 642 | 54 | 118 | 80 |
| Variant_C_regime_20d_return_8pct | 2.50% | 1.52% | -10.96% | 2.197 | 3.140 | 6.68 | 150 | 577 | 58 | 151 | 112 |
| Variant_D_regime_ma20_5pct_hwm | 2.23% | 0.99% | -7.69% | 2.054 | 3.939 | 5.32 | 90 | 642 | 110 | 152 | 46 |

## CL3 Acceptance

| Variant | Alpha sacrifice | MDD delta | Sharpe regression | Calmar regression | Overall |
|---|---:|---:|---:|---:|:---:|
| Variant_A_regime_ma20_3pct | 0.22% PASS | 0.00% FAIL | 0.00% PASS | 0.00% PASS | FAIL |
| Variant_B_regime_ma20_5pct | 0.13% PASS | 0.00% FAIL | 0.00% PASS | 0.00% PASS | FAIL |
| Variant_C_regime_20d_return_8pct | 0.00% PASS | -3.27% FAIL | 0.00% PASS | 22.82% FAIL | FAIL |
| Variant_D_regime_ma20_5pct_hwm | 0.23% PASS | 0.00% FAIL | 3.16% PASS | 3.17% PASS | FAIL |

## Strong / Weak Month Subsets

| Variant | Strong months | Strong month avg alpha | Weak months | Weak month MDD | Weak month avg return |
|---|---:|---:|---:|---:|---:|
| Control_AsymmetricV2 | 14 | 1.46% | 5 | -4.72% | 0.84% |
| Variant_A_regime_ma20_3pct | 14 | 1.09% | 5 | -3.19% | 1.15% |
| Variant_B_regime_ma20_5pct | 14 | 1.28% | 5 | -3.19% | 1.15% |
| Variant_C_regime_20d_return_8pct | 14 | 2.27% | 5 | -4.72% | 0.84% |
| Variant_D_regime_ma20_5pct_hwm | 14 | 1.08% | 5 | -3.45% | 1.10% |

## 2026-04 Replay

| Variant | April trades | Control MA5 exits | Rescued count | Rescued avg delta return |
|---|---:|---:|---:|---:|
| Variant_A_regime_ma20_3pct | 30 | 22 | 22 | -0.16% |
| Variant_B_regime_ma20_5pct | 30 | 22 | 22 | -0.16% |
| Variant_C_regime_20d_return_8pct | 30 | 22 | 0 | 0.00% |
| Variant_D_regime_ma20_5pct_hwm | 30 | 22 | 22 | -0.99% |

### Top Rescued Positions

| Variant | Ticker | Rank | Control exit | Control return | Variant exit | Variant return | Delta | Route |
|---|---:|---:|---|---:|---|---:|---:|---|
| Variant_B_regime_ma20_5pct | 2426 | 30 | 2026-04-23 MA5_BREAK | 12.05% | 2026-05-08 TIME_20D | 42.71% | 30.66% | REGIME_MA20_5PCT |
| Variant_A_regime_ma20_3pct | 2426 | 30 | 2026-04-23 MA5_BREAK | 12.05% | 2026-05-08 TIME_20D | 42.71% | 30.66% | REGIME_MA20_3PCT |
| Variant_B_regime_ma20_5pct | 4973 | 19 | 2026-04-17 MA5_BREAK | 1.90% | 2026-05-08 TIME_20D | 30.28% | 28.38% | REGIME_MA20_5PCT |
| Variant_A_regime_ma20_3pct | 4973 | 19 | 2026-04-17 MA5_BREAK | 1.90% | 2026-05-08 TIME_20D | 30.28% | 28.38% | REGIME_MA20_3PCT |
| Variant_B_regime_ma20_5pct | 3006 | 21 | 2026-04-16 MA5_BREAK | -3.10% | 2026-05-08 TIME_20D | 25.08% | 28.17% | REGIME_MA20_5PCT |
| Variant_A_regime_ma20_3pct | 3006 | 21 | 2026-04-16 MA5_BREAK | -3.10% | 2026-05-08 TIME_20D | 25.08% | 28.17% | REGIME_MA20_3PCT |
| Variant_D_regime_ma20_5pct_hwm | 7751 | 16 | 2026-04-15 MA5_BREAK | 1.01% | 2026-05-04 TRAILING_STOP_HWM_8PCT | 20.51% | 19.49% | REGIME_MA20_5PCT_HWM |
| Variant_A_regime_ma20_3pct | 3260 | 1 | 2026-04-14 MA5_BREAK | -1.45% | 2026-05-08 TIME_20D | 17.87% | 19.32% | REGIME_MA20_3PCT |
| Variant_B_regime_ma20_5pct | 3260 | 1 | 2026-04-14 MA5_BREAK | -1.45% | 2026-05-08 TIME_20D | 17.87% | 19.32% | REGIME_MA20_5PCT |
| Variant_D_regime_ma20_5pct_hwm | 3260 | 1 | 2026-04-14 MA5_BREAK | -1.45% | 2026-05-04 TRAILING_STOP_HWM_8PCT | 17.35% | 18.79% | REGIME_MA20_5PCT_HWM |
| Variant_B_regime_ma20_5pct | 6443 | 28 | 2026-04-13 MA5_BREAK | -3.53% | 2026-05-08 TIME_20D | 8.56% | 12.09% | REGIME_MA20_5PCT |
| Variant_A_regime_ma20_3pct | 6443 | 28 | 2026-04-13 MA5_BREAK | -3.53% | 2026-05-08 TIME_20D | 8.56% | 12.09% | REGIME_MA20_3PCT |
| Variant_D_regime_ma20_5pct_hwm | 3006 | 21 | 2026-04-16 MA5_BREAK | -3.10% | 2026-05-04 TRAILING_STOP_HWM_8PCT | 8.98% | 12.07% | REGIME_MA20_5PCT_HWM |
| Variant_A_regime_ma20_3pct | 6770 | 8 | 2026-04-14 MA5_BREAK | -0.80% | 2026-05-08 TIME_20D | 9.74% | 10.55% | REGIME_MA20_3PCT |
| Variant_B_regime_ma20_5pct | 6770 | 8 | 2026-04-14 MA5_BREAK | -0.80% | 2026-05-08 TIME_20D | 9.74% | 10.55% | REGIME_MA20_5PCT |
| Variant_D_regime_ma20_5pct_hwm | 4973 | 19 | 2026-04-17 MA5_BREAK | 1.90% | 2026-04-24 TRAILING_STOP_HWM_8PCT | 8.83% | 6.93% | REGIME_MA20_5PCT_HWM |
| Variant_D_regime_ma20_5pct_hwm | 3508 | 4 | 2026-04-13 MA5_BREAK | -9.89% | 2026-04-15 TRAILING_STOP_HWM_8PCT | -5.26% | 4.63% | REGIME_MA20_5PCT_HWM |
| Variant_D_regime_ma20_5pct_hwm | 6443 | 28 | 2026-04-13 MA5_BREAK | -3.53% | 2026-04-27 TRAILING_STOP_HWM_8PCT | -0.54% | 2.99% | REGIME_MA20_5PCT_HWM |
| Variant_A_regime_ma20_3pct | 3535 | 20 | 2026-04-17 MA5_BREAK | 9.57% | 2026-05-08 TIME_20D | 11.74% | 2.17% | REGIME_MA20_3PCT |
| Variant_B_regime_ma20_5pct | 3535 | 20 | 2026-04-17 MA5_BREAK | 9.57% | 2026-05-08 TIME_20D | 11.74% | 2.17% | REGIME_MA20_5PCT |

## Verdict

No variant passed all CL3 gates; regime overlay should remain research-only.
