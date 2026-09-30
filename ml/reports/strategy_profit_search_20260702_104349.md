# Strategy Profit Search

## Scope
- Dataset: `F:\stock\ml\models\dataset_cache_20260614.parquet`
- Start date: `2024-01-01`
- Holdout start month: `2025-07`
- Friction: `0.40%` round trip
- Profit hurdle: `70.0%` cumulative net raw return
- Strategies evaluated: `3785`

## Best Passing Method
- Strategy: `chip_momentum_high_top5_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all`
- Full cumulative net return: `+320.5%`
- Full cumulative net excess return: `+33.1%`
- Train cumulative net return: `+129.0%`
- Holdout cumulative net return: `+83.6%`
- Max drawdown: `-5.0%`
- Positive months: `24/29`
- Average pick win rate: `+62.1%`

Selection rule: require the 70% full-period hurdle, positive train and holdout raw returns, and positive full-period excess return; then prefer lower drawdown before higher raw return. Higher-return rows below are more aggressive candidates.

This is a research candidate, not a production promotion. Production use still needs separate slippage, capacity, overlap, and same-snapshot validation.

## Top Search Results
| rank | strategy | full net | full excess | train net | holdout net | max DD | pos months |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | `trend_strength_high_top3_none_all` | +703.4% | +184.5% | +25.8% | +538.4% | -33.8% | 22/29 |
| 2 | `trend_strength_high_top3_none_risk_on_breadth_ma60` | +703.4% | +184.5% | +25.8% | +538.4% | -33.8% | 22/29 |
| 3 | `trend_strength_high_top3_none_taiex_20d_positive` | +703.4% | +184.5% | +25.8% | +538.4% | -33.8% | 22/29 |
| 4 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all` | +603.3% | +143.2% | +10.2% | +538.4% | -29.5% | 21/29 |
| 5 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_risk_on_breadth_ma60` | +603.3% | +143.2% | +10.2% | +538.4% | -29.5% | 21/29 |
| 6 | `trend_strength_high_top3_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_taiex_20d_positive` | +603.3% | +143.2% | +10.2% | +538.4% | -29.5% | 21/29 |
| 7 | `trend_strength_high_top3_no_penny_open_ge_10_all` | +594.3% | +142.2% | +8.8% | +538.4% | -33.8% | 22/29 |
| 8 | `trend_strength_high_top3_no_penny_open_ge_10_risk_on_breadth_ma60` | +594.3% | +142.2% | +8.8% | +538.4% | -33.8% | 22/29 |
| 9 | `trend_strength_high_top3_no_penny_open_ge_10_taiex_20d_positive` | +594.3% | +142.2% | +8.8% | +538.4% | -33.8% | 22/29 |
| 10 | `Open_low_top3_none_all` | +495.0% | +93.7% | +30.6% | +355.7% | -21.1% | 17/29 |
| 11 | `Open_low_top3_none_risk_on_breadth_ma60` | +495.0% | +93.7% | +30.6% | +355.7% | -21.1% | 17/29 |
| 12 | `Open_low_top3_none_taiex_20d_positive` | +495.0% | +93.7% | +30.6% | +355.7% | -21.1% | 17/29 |
| 13 | `return_60d_high_top3_none_all` | +538.1% | +97.9% | +189.1% | +120.7% | -21.9% | 20/29 |
| 14 | `return_60d_high_top3_none_risk_on_breadth_ma60` | +538.1% | +97.9% | +189.1% | +120.7% | -21.9% | 20/29 |
| 15 | `return_60d_high_top3_none_taiex_20d_positive` | +538.1% | +97.9% | +189.1% | +120.7% | -21.9% | 20/29 |

## Best Method Monthly Returns
| month | picks | net return | net excess | pick win |
|---|---:|---:|---:|---:|
| 2024-01 | 5 | +20.9% | +10.6% | +100.0% |
| 2024-02 | 5 | +9.2% | +3.0% | +80.0% |
| 2024-03 | 5 | +6.4% | +5.9% | +60.0% |
| 2024-04 | 5 | +3.5% | -2.7% | +60.0% |
| 2024-05 | 5 | +6.7% | -2.2% | +60.0% |
| 2024-06 | 5 | +4.8% | +8.3% | +80.0% |
| 2024-07 | 5 | +2.1% | +1.3% | +60.0% |
| 2024-08 | 5 | +0.8% | +1.0% | +60.0% |
| 2024-09 | 5 | -4.7% | -7.2% | +20.0% |
| 2024-10 | 5 | -0.3% | +2.0% | +40.0% |
| 2024-11 | 5 | +5.2% | +0.7% | +60.0% |
| 2024-12 | 5 | +3.6% | +1.7% | +40.0% |
| 2025-01 | 5 | +5.5% | +8.8% | +80.0% |
| 2025-02 | 5 | +0.1% | +6.4% | +40.0% |
| 2025-03 | 5 | +0.0% | +2.2% | +60.0% |
| 2025-04 | 5 | +3.9% | -1.6% | +40.0% |
| 2025-05 | 5 | +7.4% | +1.6% | +80.0% |
| 2025-06 | 5 | +12.2% | +7.0% | +100.0% |
| 2025-07 | 5 | +7.2% | +4.3% | +80.0% |
| 2025-08 | 5 | +0.3% | -5.3% | +40.0% |
| 2025-09 | 5 | +8.2% | -1.1% | +100.0% |
| 2025-10 | 5 | -2.2% | -0.1% | +40.0% |
| 2025-11 | 5 | +1.5% | -2.8% | +40.0% |
| 2025-12 | 5 | -1.1% | -13.4% | +60.0% |
| 2026-01 | 5 | +4.0% | -2.4% | +80.0% |
| 2026-02 | 5 | +0.1% | +6.6% | +20.0% |
| 2026-03 | 5 | -0.1% | -22.8% | +60.0% |
| 2026-04 | 5 | +48.9% | +34.0% | +100.0% |
| 2026-05 | 5 | +3.8% | -3.5% | +60.0% |

## Artifacts
- Results CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_104349.csv`
- Best monthly CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_104349_best_monthly.csv`
- Best picks CSV: `F:\stock\ml\reports\strategy_profit_search_20260702_104349_best_picks.csv`
- Summary JSON: `F:\stock\ml\reports\strategy_profit_search_20260702_104349.json`
