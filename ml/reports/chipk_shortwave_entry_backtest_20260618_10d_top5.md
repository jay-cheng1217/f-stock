# ChipK Shortwave Entry Backtest

- status: `pass`
- production_decision: `eligible_for_shortwave_rerank_without_chipk`
- window: 2026-05-01 to latest
- horizon_days: 10
- top_n: 5
- prediction_files: 33

| Basket | Rows | Signal days | Zero-pick days | Avg return | Median | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| control_current_buy_topn | 110 | 33 | 2 | -4.10% | -3.79% | +40.91% |
| shortwave_treatment_topn | 110 | 33 | 2 | +0.47% | +0.27% | +50.91% |

- avg_return_delta: +4.58%
- failures: none
- warnings: chipk_snapshot_has_no_historical_backtest_do_not_use_chipk_in_gate

Note: current CMoney ChipK local file is a latest snapshot, not a historical series. This report does not allow ChipK snapshot values to alter production gates.
