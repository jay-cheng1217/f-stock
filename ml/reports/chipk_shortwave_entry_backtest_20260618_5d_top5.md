# ChipK Shortwave Entry Backtest

- status: `fail`
- production_decision: `hold`
- window: 2026-05-01 to latest
- horizon_days: 5
- top_n: 5
- prediction_files: 33

| Basket | Rows | Signal days | Zero-pick days | Avg return | Median | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| control_current_buy_topn | 135 | 33 | 2 | -1.18% | -2.80% | +42.96% |
| shortwave_treatment_topn | 135 | 33 | 2 | -1.30% | -1.64% | +40.74% |

- avg_return_delta: -0.13%
- failures: treatment_avg_return_not_positive, treatment_win_rate_materially_below_control
- warnings: none

Note: current CMoney ChipK local file is a latest snapshot, not a historical series. This report does not allow ChipK snapshot values to alter production gates.
