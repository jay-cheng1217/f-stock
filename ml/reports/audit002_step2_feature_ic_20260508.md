# AUDIT-002 Step 2 - Feature IC Cleanup

- Generated at: 2026-05-08 21:30:41
- Low IC threshold: abs(mean feature IC) < 0.010
- Kept features: 113
- Removed features: 132
- Feature IC table: `F:\stock\ml\reports\audit002_step2_feature_ic_20260508_features.csv`
- Removed feature list: `F:\stock\ml\reports\audit002_step2_feature_ic_20260508_removed_features.csv`
- Calibration chart: `F:\stock\ml\reports\audit002_step2_feature_ic_20260508_calibration.png`

## Before / After

| Model | Universe IC | Universe t | Top30 IC | Top30 t | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Before_baseline | 0.0431 | 2.43 | -0.0351 | -0.70 | 4/10 (40%) | 4 | 0.4061 | +0.14% | FAIL |
| After_feature_cleanup | 0.0125 | 0.67 | -0.0116 | -0.29 | 5/10 (50%) | 2 | 0.3576 | +0.37% | FAIL |

## Removed Feature Sample

| # | Feature |
|---:|---|
| 1 | `ma_death_cross` |
| 2 | `trust_cumsum_20d_norm` |
| 3 | `three_white_soldiers` |
| 4 | `stoch_rsi_d` |
| 5 | `inst_total_5d` |
| 6 | `rsi_bullish_div` |
| 7 | `stoch_rsi_k` |
| 8 | `return_3d` |
| 9 | `foreign_cumsum_1d` |
| 10 | `has_ann_30d` |
| 11 | `vol_zscore` |
| 12 | `trust_cumsum_20d` |
| 13 | `ma_slope_5` |
| 14 | `inst_buy_ratio_20d` |
| 15 | `trust_cumsum_1d_norm` |
| 16 | `foreign_cumsum_1d_norm` |
| 17 | `cci_20` |
| 18 | `ann_count_30d` |
| 19 | `ma5_bounce` |
| 20 | `sector_relative_return_20d` |
| 21 | `hanging_man` |
| 22 | `trust_cumsum_1d` |
| 23 | `bb_position` |
| 24 | `eps_qoq` |
| 25 | `price_vol_divergence` |
| 26 | `industry_fund_flow_20d` |
| 27 | `revenue_yoy_q` |
| 28 | `kd_golden_cross` |
| 29 | `retail_pct_chg` |
| 30 | `morning_star` |
| 31 | `upper_wick_5d_avg` |
| 32 | `price_vs_ma10` |
| 33 | `ann_surprise` |
| 34 | `debt_ratio` |
| 35 | `whale_retail_diverge` |
| 36 | `macd_turn_positive` |
| 37 | `whale_acc_weeks` |
| 38 | `gross_margin_trend` |
| 39 | `whale_pct_chg` |
| 40 | `squeeze` |
| ... | 92 more in CSV |

## Calibration Deciles - Before

| Decile | N | Pred | Actual excess | Actual raw |
|---:|---:|---:|---:|---:|
| 1 | 102 | -0.67% | -2.57% | +3.53% |
| 2 | 72 | -0.03% | -1.39% | -0.46% |
| 3 | 78 | +0.33% | -3.20% | +1.22% |
| 4 | 84 | +0.69% | -0.00% | +3.39% |
| 5 | 85 | +0.97% | +3.83% | +6.02% |
| 6 | 83 | +1.49% | +4.03% | +7.53% |
| 7 | 84 | +2.12% | -3.14% | +0.83% |
| 8 | 84 | +3.08% | -2.72% | +0.42% |
| 9 | 84 | +4.59% | +1.01% | +4.10% |
| 10 | 84 | +8.54% | +5.66% | +15.54% |

## Calibration Deciles - After

| Decile | N | Pred | Actual excess | Actual raw |
|---:|---:|---:|---:|---:|
| 1 | 92 | -0.54% | -3.80% | +1.70% |
| 2 | 76 | -0.06% | -3.34% | -0.47% |
| 3 | 84 | +0.43% | +1.01% | +1.93% |
| 4 | 87 | +0.78% | +3.79% | +9.13% |
| 5 | 81 | +1.08% | -4.27% | +5.71% |
| 6 | 84 | +1.37% | +6.34% | +9.27% |
| 7 | 84 | +1.97% | -0.28% | +3.92% |
| 8 | 84 | +3.10% | -0.10% | +2.81% |
| 9 | 84 | +4.34% | +0.64% | +1.02% |
| 10 | 84 | +7.97% | +3.43% | +9.77% |

## Verdict

Feature cleanup did not turn Top30 IC positive. Recommendation: move to Step 3 and evaluate LambdaRank / pairwise ranking objective or a two-stage screen-then-rank architecture.
