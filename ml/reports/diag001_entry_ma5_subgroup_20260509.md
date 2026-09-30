# DIAG-001 Entry MA5 Subgroup Analysis

- Generated at: `2026-05-09T17:04:29`
- Source trades: `F:\stock\ml\reports\exit_v2_cl3_backtest_20260509_trades.csv`
- Variant: `Treatment_AsymmetricV2`
- Window: `2024-01` to `2026-04`
- Grouping key: actual entry open vs previous trading day's MA5 (`price_vs_ma5_at_entry`).
- `signal_close_vs_ma5` is included as a sanity check for the signal-date technical state.

| Subgroup | Definition | Samples | Avg hold days | Avg realized return | Win rate | MA5_BREAK exit % | Avg price vs MA5 at entry | Avg signal close vs MA5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A_price_vs_ma5_at_entry_gt_0 | price_vs_ma5_at_entry > 0 | 441 | 6.29 | 3.22% | 41.50% | 76.42% | 3.71% | 2.57% |
| B_minus5pct_to_0 | -5% < price_vs_ma5_at_entry <= 0 | 319 | 3.58 | 1.32% | 46.08% | 91.22% | -1.81% | -1.70% |
| C_le_minus5pct | price_vs_ma5_at_entry <= -5% | 80 | 2.33 | 1.09% | 58.75% | 95.00% | -8.02% | -6.12% |
