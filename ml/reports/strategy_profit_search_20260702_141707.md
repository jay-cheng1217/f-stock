# Strategy Profit Search

## Scope
- Dataset: `F:\stock\ml\models\dataset_cache_20260702.parquet`
- Start date: `2024-01-01`
- Holdout start month: `2025-07`
- Friction: `0.40%` round trip
- Profit hurdle: `70.0%` cumulative net raw return
- Strategies evaluated: `3785`

## Best Passing Method
- Strategy: `chip_momentum_high_top5_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all`
- Full cumulative net return: `+322.1%`
- Full cumulative net excess return: `+40.1%`
- Train cumulative net return: `+108.5%`
- Holdout cumulative net return: `+102.4%`
- Max drawdown: `-5.2%`
- Positive months: `24/30`
- Average pick win rate: `+57.3%`

Selection rule: require the 70% full-period hurdle, positive train and holdout raw returns, and positive full-period excess return; then prefer lower drawdown before higher raw return. Higher-return rows below are more aggressive candidates.

This is a research candidate, not a production promotion. Production use still needs separate slippage, capacity, overlap, and same-snapshot validation.

## Top Search Results
| rank | strategy | full net | full excess | train net | holdout net | max DD | pos months |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | `trend_strength_high_top3_none_all` | +1123.5% | +361.1% | +43.8% | +750.6% | -31.5% | 23/30 |
| 2 | `trend_strength_high_top3_none_risk_on_breadth_ma60` | +1123.5% | +361.1% | +43.8% | +750.6% | -31.5% | 23/30 |
| 3 | `trend_strength_high_top3_none_taiex_20d_positive` | +1123.5% | +361.1% | +43.8% | +750.6% | -31.5% | 23/30 |
| 4 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all` | +975.2% | +295.5% | +26.4% | +750.6% | -26.8% | 22/30 |
| 5 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_risk_on_breadth_ma60` | +975.2% | +295.5% | +26.4% | +750.6% | -26.8% | 22/30 |
| 6 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_taiex_20d_positive` | +975.2% | +295.5% | +26.4% | +750.6% | -26.8% | 22/30 |
| 7 | `trend_strength_high_top3_no_penny_open_ge_10_all` | +957.4% | +292.5% | +24.3% | +750.6% | -31.5% | 23/30 |
| 8 | `trend_strength_high_top3_no_penny_open_ge_10_risk_on_breadth_ma60` | +957.4% | +292.5% | +24.3% | +750.6% | -31.5% | 23/30 |
| 9 | `trend_strength_high_top3_no_penny_open_ge_10_taiex_20d_positive` | +957.4% | +292.5% | +24.3% | +750.6% | -31.5% | 23/30 |
| 10 | `price_vs_ma60_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all` | +670.5% | +181.7% | +30.7% | +489.5% | -33.5% | 21/30 |
| 11 | `price_vs_ma60_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_risk_on_breadth_ma60` | +670.5% | +181.7% | +30.7% | +489.5% | -33.5% | 21/30 |
| 12 | `price_vs_ma60_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_taiex_20d_positive` | +670.5% | +181.7% | +30.7% | +489.5% | -33.5% | 21/30 |
| 13 | `Open_low_top5_none_all` | +577.7% | +116.7% | +3.0% | +557.6% | -22.2% | 17/30 |
| 14 | `Open_low_top5_none_risk_on_breadth_ma60` | +577.7% | +116.7% | +3.0% | +557.6% | -22.2% | 17/30 |
| 15 | `Open_low_top5_none_taiex_20d_positive` | +577.7% | +116.7% | +3.0% | +557.6% | -22.2% | 17/30 |

## Best Method Monthly Returns
| month | picks | net return | net excess | pick win |
|---|---:|---:|---:|---:|
| 2024-01 | 5 | +20.9% | +10.6% | +100.0% |
| 2024-02 | 5 | +7.1% | +0.9% | +60.0% |
| 2024-03 | 5 | +6.4% | +5.9% | +60.0% |
| 2024-04 | 5 | +3.5% | -2.7% | +60.0% |
| 2024-05 | 5 | +5.6% | -3.3% | +60.0% |
| 2024-06 | 5 | +4.0% | +7.6% | +80.0% |
| 2024-07 | 5 | +2.1% | +1.3% | +60.0% |
| 2024-08 | 5 | +0.8% | +1.0% | +60.0% |
| 2024-09 | 5 | -5.2% | -7.7% | +20.0% |
| 2024-10 | 5 | +1.5% | +3.8% | +60.0% |
| 2024-11 | 5 | +7.9% | +3.4% | +60.0% |
| 2024-12 | 5 | +3.6% | +1.7% | +40.0% |
| 2025-01 | 5 | +0.5% | +3.8% | +60.0% |
| 2025-02 | 5 | -3.4% | +2.9% | +20.0% |
| 2025-03 | 5 | +0.0% | +2.2% | +60.0% |
| 2025-04 | 5 | +4.3% | -1.2% | +40.0% |
| 2025-05 | 5 | +7.4% | +1.6% | +80.0% |
| 2025-06 | 5 | +10.5% | +5.3% | +80.0% |
| 2025-07 | 5 | +24.3% | +21.3% | +80.0% |
| 2025-08 | 5 | +0.8% | -4.7% | +60.0% |
| 2025-09 | 5 | +8.0% | -1.4% | +100.0% |
| 2025-10 | 5 | -2.2% | -0.1% | +40.0% |
| 2025-11 | 5 | -1.1% | -5.4% | +20.0% |
| 2025-12 | 5 | -1.1% | -13.4% | +60.0% |
| 2026-01 | 5 | +0.1% | -6.3% | +60.0% |
| 2026-02 | 5 | +4.9% | +11.4% | +40.0% |
| 2026-03 | 5 | -0.9% | -23.6% | +40.0% |
| 2026-04 | 5 | +37.0% | +22.1% | +80.0% |
| 2026-05 | 5 | +1.9% | +1.3% | +40.0% |
| 2026-06 | 5 | +7.7% | +7.1% | +40.0% |

## Artifacts
- Results CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_141707.csv`
- Best monthly CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_141707_best_monthly.csv`
- Best picks CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_141707_best_picks.csv`
- Summary JSON: `F:\stock\ml\reports\strategy_profit_search_20260702_141707.json`
