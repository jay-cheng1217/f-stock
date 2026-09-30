# DIAG-004 TWII Rally Leadership Coverage Audit

- Generated at: `2026-05-09T16:22:34.180012+00:00`
- Window: `2026-04-01` to `2026-05-08`
- Contribution method: `start_close_x_trailing_20d_avg_volume_proxy_weight_times_close_return`
- Prediction files scanned: `26`
- Unified signal files scanned: `25`
- Two-Stage rank available in prediction files: `1` days

## Top 10 Leadership Coverage

| Rank | Ticker | Name | Sector | 4/1~5/8 return | Proxy contribution | In universe | Top75 days | Top30 days | Signals days | Paper positions | Root cause | Blocked / signal reason |
|---:|---:|---|---|---:|---:|:---:|---:|---:|---:|---:|---|---|
| 1 | 2330 | 台積電 | 半導體業 | 23.45% | 1.97% | Y | 2 | 1 | 0 | 0 | post_top30_gate_or_signal_filter | NO_BUY_SIGNAL_OR_LOW_EDGE x9; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x1 |
| 2 | 2454 | 聯發科 | 半導體業 | 147.78% | 1.82% | Y | 0 | 0 | 0 | 0 | stage1_scoring_absent | NO_BUY_SIGNAL_OR_LOW_EDGE x9; DISPOSITION_PERIOD x3 |
| 3 | 6274 | 台燿 | 電子零組件業 | 119.90% | 1.32% | Y | 14 | 4 | 0 | 0 | post_top30_gate_or_signal_filter | DISPOSITION_PERIOD x9; tradability_reason=OVERHEAT_RISK; production_gate_reason=20d_only_research_backfill x3; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x1 |
| 4 | 2308 | 台達電 | 電子零組件業 | 49.15% | 1.07% | Y | 10 | 2 | 0 | 0 | post_top30_gate_or_signal_filter | NO_BUY_SIGNAL_OR_LOW_EDGE x3; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x2 |
| 5 | 2383 | 台光電 | 電子零組件業 | 66.08% | 0.85% | Y | 6 | 3 | 0 | 0 | post_top30_gate_or_signal_filter | NO_BUY_SIGNAL_OR_LOW_EDGE x5; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x1; tradability_reason=OVERHEAT_RISK; production_gate_reason=20d_only_research_backfill x1; tradability_re |
| 6 | 2408 | 南亞科 | 半導體業 | 29.86% | 0.84% | Y | 10 | 3 | 0 | 0 | post_top30_gate_or_signal_filter | NO_BUY_SIGNAL_OR_LOW_EDGE x2; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x3 |
| 7 | 3443 | 創意 | 半導體業 | 121.23% | 0.75% | Y | 11 | 1 | 0 | 0 | post_top30_gate_or_signal_filter | DISPOSITION_PERIOD x9; NO_BUY_SIGNAL_OR_LOW_EDGE x1; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x1 |
| 8 | 8299 | 群聯 | 半導體業 | 47.27% | 0.71% | Y | 7 | 4 | 0 | 0 | post_top30_gate_or_signal_filter | CHIP_DIVERGENCE_GUARD x4; NO_BUY_SIGNAL_OR_LOW_EDGE x2; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x4 |
| 9 | 3037 | 欣興 | 電子零組件業 | 67.45% | 0.62% | Y | 14 | 3 | 0 | 0 | post_top30_gate_or_signal_filter | DISPOSITION_PERIOD x6; NO_BUY_SIGNAL_OR_LOW_EDGE x5; tradability_reason=LOW_LIQUIDITY; production_gate_reason=20d_only_research_backfill x1; tradability_reason=OVERHEAT_RISK; HIGH_BETA_RISK; production_gate_reason=20d_on |
| 10 | 4958 | 臻鼎-KY | 電子零組件業 | 82.69% | 0.61% | Y | 2 | 0 | 0 | 0 | top75_not_promoted_to_top30_or_signals | NO_BUY_SIGNAL_OR_LOW_EDGE x4; CHIP_DIVERGENCE_GUARD x3 |

## Root-Cause Summary

| Layer | Leaders | Proxy contribution sum |
|---|---:|---:|
| post_top30_gate_or_signal_filter | 8 | 8.13% |
| stage1_scoring_absent | 1 | 1.82% |
| top75_not_promoted_to_top30_or_signals | 1 | 0.61% |

## Interpretation

- `top75_days` uses the saved Two-Stage candidate flag when present; before that column existed, it falls back to the daily prediction score Top75.
- `top30_days` counts `unified_signals` rows with rank <= 30, including rows later marked `signal_type=NONE` by gates.
- `prediction_two_stage_top30_days` is available in the CSV artifact for exact saved Two-Stage ranks; most historical files in this window predate that column.
- `in_signals_days` means actionable final output only: `target_units > 0`, positive target weight, and `signal_type != NONE`.
- Contribution is a liquidity/impact proxy, not exact TWII market-cap contribution, because shares outstanding is not stored locally.

## Data Artifacts

- CSV: `F:\stock\ml\reports\diag004_twii_rally_leadership_20260509.csv`
- Root summary CSV: `F:\stock\ml\reports\diag004_twii_rally_leadership_20260509_root_summary.csv`
- JSON: `F:\stock\ml\reports\diag004_twii_rally_leadership_20260509.json`
