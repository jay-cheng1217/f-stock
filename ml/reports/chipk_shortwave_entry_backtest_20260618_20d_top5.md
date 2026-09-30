# ChipK Shortwave Entry Backtest

- status: `pass`
- production_decision: `eligible_for_shortwave_rerank_without_chipk`
- window: 2026-05-01 to latest
- horizon_days: 20
- top_n: 5
- prediction_files: 33

| Basket | Rows | Signal days | Zero-pick days | Avg return | Median | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| control_current_buy_topn | 60 | 33 | 2 | -0.00% | -1.86% | +41.67% |
| shortwave_treatment_topn | 60 | 33 | 2 | +4.69% | +1.24% | +56.67% |

- avg_return_delta: +4.70%
- failures: none
- warnings: chipk_snapshot_has_no_historical_backtest_do_not_use_chipk_in_gate

Note: current CMoney ChipK local file is a latest snapshot, not a historical series. This report does not allow ChipK snapshot values to alter production gates.
