# V2 Gate Funnel Sector Bias Audit

## Scope

- Generated at: 2026-05-02T17:15:09.621538+00:00
- Dates: 2026-04-09 to 2026-04-30 (16 canonical days)
- Top N: 30
- Stages: raw Top30, sector-capped rank20, pre-market-regime survivors, active target.

## Key Findings

- Active rows min/p25/median/p75/max: 0 / 4.00 / 5.50 / 15.00 / 21
- Active rows < 10: 10 / 16 (62.50%)
- Blocked reason counts: OVERHEAT_RISK=158, MARKET_REGIME_CAUTION=70, HIGH_BETA_RISK=21, LOW_LIQUIDITY=10, DISPOSITION_PERIOD=10
- Why communication survives: 通信網路不是 rank20 供給最多的族群，但 rank20_to_active survival 41.54% 是四個 focus sectors 最高；它在 CAUTION Top10 與 tradability gates 後留下的比例高，所以 target denominator 縮小時被動放大。
- Is semiconductor low at selection: 否。半導體 rank20 count 73 高於通信網路 65，且 raw score medians 沒有明顯劣勢；半導體 active count 24 略低於通信 27，主因是 gate kill rate 較高。
- Interpretation: Active breadth collapse is driven mainly after sector-capped rank20, especially market-regime CAUTION Top10 pruning and tradability gates. Treat model-level sector score bias as secondary until gate funnel is fully reviewed.

## Focus Sector Survival

| sector | rank20 count | active count | killed | total survival | mean daily survival |
| --- | ---: | ---: | ---: | ---: | ---: |
| 其他電子業 | 68 | 16 | 52 | 23.53% | 21.56% |
| 半導體業 | 73 | 24 | 49 | 32.88% | 30.56% |
| 通信網路業 | 65 | 27 | 38 | 41.54% | 36.89% |
| 電子零組件業 | 63 | 11 | 52 | 17.46% | 20.31% |

## Semiconductor Vs Communication Score Medians

All rows in the available prediction artifacts, not only Top30.

| score | 半導體 median | 通信 median | diff 半導體-通信 |
| --- | ---: | ---: | ---: |
| pred_return_20d | -0.69% | -0.70% | +0.02% |
| prob_edge | -5.64% | -6.18% | +0.54% |
| leaderboard_score | -0.18% | -0.20% | +0.02% |

## Daily Funnel

| date | rank20 | pre-regime | active | max active sector | max name | regime action |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| 2026-04-09 | 27 | 17 | 5 | 電子零組件業 60.00% | 20.00% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-10 | 8 | 4 | 4 | 金融保險業 75.00% | 25.00% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-13 | 30 | 18 | 18 | 金融保險業 27.78% | 5.56% | ALLOW_NEW_POSITIONS |
| 2026-04-14 | 5 | 1 | 1 | 光電業 100.00% | 100.00% | ALLOW_NEW_POSITIONS |
| 2026-04-15 | 9 | 4 | 4 | 光電業 25.00% | 25.00% | ALLOW_NEW_POSITIONS |
| 2026-04-16 | 7 | 0 | 0 | None 0.00% | 0.00% | ALLOW_NEW_POSITIONS |
| 2026-04-17 | 30 | 14 | 14 | 光電業 21.43% | 7.14% | ALLOW_NEW_POSITIONS |
| 2026-04-20 | 30 | 18 | 18 | 光電業 16.67% | 5.56% | ALLOW_NEW_POSITIONS |
| 2026-04-21 | 30 | 18 | 18 | 光電業 22.22% | 5.56% | ALLOW_NEW_POSITIONS |
| 2026-04-22 | 30 | 14 | 14 | 光電業 21.43% | 7.14% | ALLOW_NEW_POSITIONS |
| 2026-04-23 | 30 | 21 | 21 | 其他電子業 23.81% | 4.76% | ALLOW_NEW_POSITIONS |
| 2026-04-24 | 30 | 16 | 4 | 通信網路業 50.00% | 25.00% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-27 | 30 | 16 | 5 | 通信網路業 60.00% | 20.00% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-28 | 30 | 17 | 5 | 半導體業 40.00% | 20.00% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-29 | 30 | 19 | 7 | 通信網路業 42.86% | 14.29% | LIMIT_20D_TOP10_AND_BLOCK_T1 |
| 2026-04-30 | 30 | 16 | 6 | 半導體業 50.00% | 16.67% | LIMIT_20D_TOP10_AND_BLOCK_T1 |

## Focus Sector Daily Kill Rate

| date | sector | rank20 | active | kill rate |
| --- | --- | ---: | ---: | ---: |
| 2026-04-09 | 其他電子業 | 6 | 0 | 100.00% |
| 2026-04-09 | 半導體業 | 6 | 2 | 66.67% |
| 2026-04-09 | 通信網路業 | 5 | 0 | 100.00% |
| 2026-04-09 | 電子零組件業 | 6 | 3 | 50.00% |
| 2026-04-10 | 其他電子業 | 2 | 1 | 50.00% |
| 2026-04-10 | 半導體業 | 1 | 0 | 100.00% |
| 2026-04-10 | 通信網路業 | 1 | 0 | 100.00% |
| 2026-04-10 | 電子零組件業 | 1 | 0 | 100.00% |
| 2026-04-13 | 其他電子業 | 6 | 1 | 83.33% |
| 2026-04-13 | 半導體業 | 6 | 3 | 50.00% |
| 2026-04-13 | 通信網路業 | 4 | 2 | 50.00% |
| 2026-04-13 | 電子零組件業 | 2 | 2 | 0.00% |
| 2026-04-14 | 其他電子業 | 1 | 0 | 100.00% |
| 2026-04-14 | 電子零組件業 | 1 | 0 | 100.00% |
| 2026-04-15 | 其他電子業 | 1 | 0 | 100.00% |
| 2026-04-15 | 半導體業 | 2 | 1 | 50.00% |
| 2026-04-15 | 通信網路業 | 1 | 0 | 100.00% |
| 2026-04-15 | 電子零組件業 | 2 | 1 | 50.00% |
| 2026-04-16 | 半導體業 | 2 | 0 | 100.00% |
| 2026-04-16 | 通信網路業 | 2 | 0 | 100.00% |
| 2026-04-16 | 電子零組件業 | 3 | 0 | 100.00% |
| 2026-04-17 | 其他電子業 | 5 | 1 | 80.00% |
| 2026-04-17 | 半導體業 | 6 | 3 | 50.00% |
| 2026-04-17 | 通信網路業 | 5 | 2 | 60.00% |
| 2026-04-17 | 電子零組件業 | 5 | 0 | 100.00% |
| 2026-04-20 | 其他電子業 | 3 | 0 | 100.00% |
| 2026-04-20 | 半導體業 | 6 | 3 | 50.00% |
| 2026-04-20 | 通信網路業 | 6 | 3 | 50.00% |
| 2026-04-20 | 電子零組件業 | 4 | 2 | 50.00% |
| 2026-04-21 | 其他電子業 | 3 | 1 | 66.67% |
| 2026-04-21 | 半導體業 | 4 | 2 | 50.00% |
| 2026-04-21 | 通信網路業 | 5 | 2 | 60.00% |
| 2026-04-21 | 電子零組件業 | 3 | 0 | 100.00% |
| 2026-04-22 | 其他電子業 | 5 | 1 | 80.00% |
| 2026-04-22 | 半導體業 | 6 | 0 | 100.00% |
| 2026-04-22 | 通信網路業 | 6 | 3 | 50.00% |
| 2026-04-22 | 電子零組件業 | 4 | 1 | 75.00% |
| 2026-04-23 | 其他電子業 | 6 | 5 | 16.67% |
| 2026-04-23 | 半導體業 | 4 | 1 | 75.00% |
| 2026-04-23 | 通信網路業 | 6 | 3 | 50.00% |
| 2026-04-23 | 電子零組件業 | 4 | 2 | 50.00% |
| 2026-04-24 | 其他電子業 | 6 | 1 | 83.33% |
| 2026-04-24 | 半導體業 | 6 | 1 | 83.33% |
| 2026-04-24 | 通信網路業 | 6 | 2 | 66.67% |
| 2026-04-24 | 電子零組件業 | 5 | 0 | 100.00% |
| 2026-04-27 | 其他電子業 | 6 | 1 | 83.33% |
| 2026-04-27 | 半導體業 | 6 | 1 | 83.33% |
| 2026-04-27 | 通信網路業 | 6 | 3 | 50.00% |
| 2026-04-27 | 電子零組件業 | 6 | 0 | 100.00% |
| 2026-04-28 | 其他電子業 | 6 | 1 | 83.33% |
| 2026-04-28 | 半導體業 | 6 | 2 | 66.67% |
| 2026-04-28 | 通信網路業 | 5 | 2 | 60.00% |
| 2026-04-28 | 電子零組件業 | 6 | 0 | 100.00% |
| 2026-04-29 | 其他電子業 | 6 | 2 | 66.67% |
| 2026-04-29 | 半導體業 | 6 | 2 | 66.67% |
| 2026-04-29 | 通信網路業 | 3 | 3 | 0.00% |
| 2026-04-29 | 電子零組件業 | 6 | 0 | 100.00% |
| 2026-04-30 | 其他電子業 | 6 | 1 | 83.33% |
| 2026-04-30 | 半導體業 | 6 | 3 | 50.00% |
| 2026-04-30 | 通信網路業 | 4 | 2 | 50.00% |
| 2026-04-30 | 電子零組件業 | 5 | 0 | 100.00% |

## Blocked Reasons By Sector

| sector | reason | count |
| --- | --- | ---: |
| 電子零組件業 | OVERHEAT_RISK | 42 |
| 半導體業 | OVERHEAT_RISK | 35 |
| 其他電子業 | OVERHEAT_RISK | 33 |
| 通信網路業 | OVERHEAT_RISK | 26 |
| 其他電子業 | MARKET_REGIME_CAUTION | 19 |
| 電子零組件業 | HIGH_BETA_RISK | 12 |
| 電機機械 | MARKET_REGIME_CAUTION | 10 |
| 半導體業 | MARKET_REGIME_CAUTION | 9 |
| 通信網路業 | MARKET_REGIME_CAUTION | 9 |
| 電子零組件業 | MARKET_REGIME_CAUTION | 8 |
| 光電業 | MARKET_REGIME_CAUTION | 7 |
| 化學工業 | OVERHEAT_RISK | 6 |
| 半導體業 | HIGH_BETA_RISK | 6 |
| 半導體業 | DISPOSITION_PERIOD | 5 |
| 半導體業 | LOW_LIQUIDITY | 4 |
| 汽車工業 | OVERHEAT_RISK | 4 |
| 光電業 | OVERHEAT_RISK | 3 |
| 其他電子業 | HIGH_BETA_RISK | 3 |
| 化學工業 | MARKET_REGIME_CAUTION | 3 |
| 電機機械 | OVERHEAT_RISK | 3 |
| 電腦及週邊設備業 | OVERHEAT_RISK | 3 |
| 食品工業 | OVERHEAT_RISK | 3 |
| 塑膠工業 | MARKET_REGIME_CAUTION | 2 |
| 生技醫療業 | LOW_LIQUIDITY | 2 |
| 通信網路業 | LOW_LIQUIDITY | 2 |
| 電子零組件業 | DISPOSITION_PERIOD | 2 |
| 其他業 | MARKET_REGIME_CAUTION | 1 |
| 其他電子業 | DISPOSITION_PERIOD | 1 |
| 居家生活 | LOW_LIQUIDITY | 1 |
| 通信網路業 | DISPOSITION_PERIOD | 1 |

## Notes

- `pre_market_regime_survivors` is an approximation from final unified rows: active rows plus rows whose only block reason is market-regime CAUTION.
- `rank20_to_active_total` is the decisive production symptom: sector-capped supply exists, but active target rows collapse after downstream gates.
- This report is research-only and does not change gates, model scoring, sector cap, or entry filters.
