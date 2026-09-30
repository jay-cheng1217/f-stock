# ChipK Shortwave Entry Backtest

- status: `fail`
- production_decision: `hold`
- window: 2026-05-01 to latest
- horizon_days: 5
- top_n: 10
- prediction_files: 33

| Basket | Rows | Signal days | Zero-pick days | Avg return | Median | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| control_current_buy_topn | 270 | 33 | 2 | -1.03% | -3.09% | +40.37% |
| shortwave_treatment_topn | 262 | 33 | 2 | -0.45% | -1.18% | +41.60% |

- avg_return_delta: +0.58%
- failures: treatment_avg_return_not_positive
- warnings: none

Note: current CMoney ChipK local file is a latest snapshot, not a historical series. This report does not allow ChipK snapshot values to alter production gates.
