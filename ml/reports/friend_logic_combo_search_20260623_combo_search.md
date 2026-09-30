# Friend Logic Combination Search

- Generated: `20260623_combo_search`
- Daily data dir: `F:\stock\日K資料`
- Window: `2024-01-01` to `latest`
- Objective: maximize `60D` net return after `0.40%` friction
- Minimum trades for ranking: `200`
- Max overlays tested: `3`
- Max selected per signal day: `10`

## Max Avg 60D

| Rank | Base | Overlays | Trades | Avg 60D | Median | P10 | Win | Robust | Worst |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | friend_pullback_limit | macd_hist_positive,macd_improving,volume_quiet | 307 | +8.16% | -0.66% | -16.78% | +46.91% | +2.43% | -49.50% |
| 2 | friend_pullback_limit | flow_dealer_nonnegative,macd_hist_positive,macd_improving | 443 | +6.96% | -1.00% | -16.77% | +47.18% | +1.16% | -29.24% |
| 3 | friend_pullback_limit | kd_bullish,macd_hist_positive,volume_quiet | 290 | +6.55% | -0.95% | -18.17% | +47.24% | +0.41% | -40.04% |
| 4 | friend_pullback_limit | macd_3d_delta_positive,macd_hist_positive,macd_improving | 585 | +6.55% | -1.04% | -16.55% | +46.67% | +0.78% | -49.50% |
| 5 | friend_pullback_limit | kd_bullish,ma60_tight_support,macd_hist_positive | 526 | +6.33% | -1.39% | -16.48% | +46.77% | +0.39% | -47.87% |
| 6 | friend_pullback_limit | kd_bullish,macd_3d_delta_positive,macd_hist_positive | 557 | +6.30% | -0.63% | -16.50% | +47.94% | +0.90% | -47.87% |
| 7 | friend_pullback_limit | macd_hist_positive,macd_improving,rsi_mid | 731 | +6.29% | -1.38% | -16.79% | +46.10% | +0.21% | -49.50% |
| 8 | friend_pullback_limit | macd_hist_positive,macd_improving | 735 | +6.26% | -1.38% | -16.78% | +46.12% | +0.18% | -49.50% |
| 9 | friend_pullback_limit | ma20_not_far_below,macd_hist_positive,macd_improving | 735 | +6.26% | -1.38% | -16.78% | +46.12% | +0.18% | -49.50% |
| 10 | friend_pullback_limit | macd_hist_gt_neg1,macd_hist_positive,macd_improving | 735 | +6.26% | -1.38% | -16.78% | +46.12% | +0.18% | -49.50% |

## Robust 60D

| Rank | Base | Overlays | Trades | Avg 60D | Median | P10 | Win | Robust | Worst |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | friend_pullback_limit | macd_hist_positive,macd_improving,volume_quiet | 307 | +8.16% | -0.66% | -16.78% | +46.91% | +2.43% | -49.50% |
| 2 | friend_pullback_limit | flow_dealer_nonnegative,macd_hist_positive,macd_improving | 443 | +6.96% | -1.00% | -16.77% | +47.18% | +1.16% | -29.24% |
| 3 | friend_pullback_limit | kd_bullish,macd_3d_delta_positive,macd_hist_positive | 557 | +6.30% | -0.63% | -16.50% | +47.94% | +0.90% | -47.87% |
| 4 | friend_pullback_limit | macd_3d_delta_positive,macd_hist_positive,macd_improving | 585 | +6.55% | -1.04% | -16.55% | +46.67% | +0.78% | -49.50% |
| 5 | friend_pullback_limit | kd_bullish,macd_hist_positive,macd_improving | 527 | +6.17% | -1.12% | -16.35% | +46.87% | +0.49% | -47.87% |
| 6 | friend_pullback_limit | macd_hist_positive,macd_improving,volume_confirmed | 589 | +6.13% | -0.66% | -17.23% | +47.71% | +0.48% | -49.50% |
| 7 | friend_pullback_limit | kd_bullish,macd_hist_positive,volume_quiet | 290 | +6.55% | -0.95% | -18.17% | +47.24% | +0.41% | -40.04% |
| 8 | friend_pullback_limit | kd_bullish,ma60_tight_support,macd_hist_positive | 526 | +6.33% | -1.39% | -16.48% | +46.77% | +0.39% | -47.87% |
| 9 | friend_pullback_limit | macd_hist_positive,macd_improving,rsi_mid | 731 | +6.29% | -1.38% | -16.79% | +46.10% | +0.21% | -49.50% |
| 10 | friend_pullback_limit | macd_hist_positive,macd_improving | 735 | +6.26% | -1.38% | -16.78% | +46.12% | +0.18% | -49.50% |

## Baseline Rules

| Base Rule | Trades | Avg 60D | Median | P10 | Win | Robust | Worst |
|---|---:|---:|---:|---:|---:|---:|---:|
| friend_strict | 369 | +3.43% | -1.77% | -15.50% | +44.44% | -2.52% | -39.16% |
| friend_pullback_limit | 3602 | +3.28% | -2.42% | -20.26% | +43.45% | -4.62% | -69.18% |
| friend_base | 4828 | +2.96% | -1.59% | -16.31% | +45.30% | -2.98% | -76.94% |
| friend_loose | 5262 | +2.50% | -1.56% | -16.75% | +44.81% | -3.62% | -74.37% |

## SA Interpretation

- Best average and robust combo: `friend_pullback_limit+macd_hist_positive+macd_improving+volume_quiet`.
- The profitable combination is not generic ChipK chasing. It is a support-limit entry after a pullback, then only when MACD histogram is already positive, MACD is improving, and volume is quiet rather than crowded.
- Compared with the raw `friend_pullback_limit` baseline, the best combo trades far less often but materially improves average 60D return and robust score.
- Median 60D return remains slightly negative and tail loss is still meaningful, so this is a high-upside swing filter, not a standalone all-in production gate.

## Best Avg Combo Extremes

- Combo: `friend_pullback_limit+macd_hist_positive+macd_improving+volume_quiet`

### Top 10

| Name | Sector | Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD | Vol/20D |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 華新科 | 電子零組件業 | 2492 | 2026-03-19 | 2026-03-20 | 131.69 | 2026-06-16 | 494.00 | +274.73% | -774530 | -7785 | -781315 | 0.535 | 0.75 |
| 中探針 | 電子零組件業 | 6217 | 2025-12-22 | 2025-12-23 | 51.06 | 2026-03-30 | 181.50 | +255.08% | 1029004 | 1029004 | 2058008 | 0.057 | 0.79 |
| 鈺邦 | 電子零組件業 | 6449 | 2026-03-18 | 2026-03-19 | 163.17 | 2026-06-15 | 463.00 | +183.35% | -77000 | -5000 | -82000 | 0.392 | 0.60 |
| 廣穎 | 半導體業 | 4973 | 2025-12-12 | 2025-12-15 | 34.10 | 2026-03-20 | 88.40 | +158.81% | 385000 | 385000 | 770000 | 0.009 | 0.79 |
| 愛普* | 半導體業 | 6531 | 2026-03-02 | 2026-03-03 | 424.83 | 2026-05-28 | 1095.00 | +157.35% | 152152 | -116768 | 2384 | 0.958 | 0.65 |
| 金山電 | 電子零組件業 | 8042 | 2026-02-26 | 2026-03-02 | 59.00 | 2026-05-27 | 150.50 | +154.68% | 480773 | 480773 | 961546 | 0.156 | 0.79 |
| 鑫科 | 其他電子業 | 3663 | 2024-05-23 | 2024-05-24 | 43.42 | 2024-08-20 | 109.77 | +152.41% | -53000 | -53000 | -106000 | 0.140 | 0.77 |
| 晶豪科 | 半導體業 | 3006 | 2025-12-12 | 2025-12-15 | 80.95 | 2026-03-20 | 195.50 | +141.11% | 1704000 | 199499 | 1903499 | 0.229 | 0.65 |
| 誠美材 | 光電業 | 4960 | 2026-02-26 | 2026-03-02 | 13.74 | 2026-05-27 | 33.15 | +140.92% | 476092 | 6000 | 482092 | 0.029 | 0.75 |
| 定穎投控 | 電子零組件業 | 3715 | 2025-06-12 | 2025-06-13 | 41.65 | 2025-09-05 | 92.30 | +121.21% | 11002 | -3000 | 8002 | 0.052 | 0.54 |

### Bottom 10

| Name | Sector | Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD | Vol/20D |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 健策 | 電子零組件業 | 3653 | 2024-12-31 | 2025-01-02 | 1470.80 | 2025-04-09 | 748.62 | -49.50% | -122111 | -4876 | -108987 | 5.299 | 0.66 |
| 麗臺 | 電腦及週邊設備業 | 2465 | 2024-06-14 | 2024-06-17 | 125.63 | 2024-09-10 | 89.40 | -29.24% | -9000 | 0 | -9000 | 0.063 | 0.68 |
| 一詮 | 光電業 | 2486 | 2025-03-10 | 2025-03-11 | 106.33 | 2025-06-06 | 75.82 | -29.09% | 255920 | 76219 | 332139 | 0.212 | 0.75 |
| 吉茂 | 汽車工業 | 1587 | 2024-07-04 | 2024-07-05 | 59.24 | 2024-10-01 | 43.26 | -27.38% | -16000 | 0 | -16000 | 0.032 | 0.79 |
| 集盛 | 紡織纖維 | 1455 | 2024-06-14 | 2024-06-17 | 14.21 | 2024-09-10 | 10.65 | -25.45% | 1156000 | 118997 | 1263997 | 0.000 | 0.73 |
| 正基 | 通信網路業 | 6546 | 2025-09-08 | 2025-09-09 | 80.16 | 2025-12-05 | 60.70 | -24.68% | 165000 | 165000 | 330000 | 0.212 | 0.68 |
| 昶昕 | 綠能環保 | 8438 | 2024-09-27 | 2024-09-30 | 40.27 | 2024-12-26 | 30.59 | -24.43% | 110000 | 14243 | 124243 | 0.057 | 0.79 |
| 祥碩 | 半導體業 | 5269 | 2025-07-31 | 2025-08-04 | 1871.80 | 2025-10-30 | 1425.00 | -24.27% | 49671 | 742 | 25278 | 1.354 | 0.49 |
| 群電 | 電子零組件業 | 6412 | 2024-12-30 | 2024-12-31 | 114.17 | 2025-04-08 | 86.92 | -24.27% | 200712 | -702 | 195010 | 0.140 | 0.49 |
| 金麗-KY | 貿易百貨業 | 8429 | 2025-03-18 | 2025-03-19 | 10.54 | 2025-06-16 | 8.05 | -23.99% | 112000 | 19998 | 131998 | 0.008 | 0.59 |

## Robust Combo Extremes

- Combo: `friend_pullback_limit+macd_hist_positive+macd_improving+volume_quiet`

### Top 10

| Name | Sector | Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD | Vol/20D |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 華新科 | 電子零組件業 | 2492 | 2026-03-19 | 2026-03-20 | 131.69 | 2026-06-16 | 494.00 | +274.73% | -774530 | -7785 | -781315 | 0.535 | 0.75 |
| 中探針 | 電子零組件業 | 6217 | 2025-12-22 | 2025-12-23 | 51.06 | 2026-03-30 | 181.50 | +255.08% | 1029004 | 1029004 | 2058008 | 0.057 | 0.79 |
| 鈺邦 | 電子零組件業 | 6449 | 2026-03-18 | 2026-03-19 | 163.17 | 2026-06-15 | 463.00 | +183.35% | -77000 | -5000 | -82000 | 0.392 | 0.60 |
| 廣穎 | 半導體業 | 4973 | 2025-12-12 | 2025-12-15 | 34.10 | 2026-03-20 | 88.40 | +158.81% | 385000 | 385000 | 770000 | 0.009 | 0.79 |
| 愛普* | 半導體業 | 6531 | 2026-03-02 | 2026-03-03 | 424.83 | 2026-05-28 | 1095.00 | +157.35% | 152152 | -116768 | 2384 | 0.958 | 0.65 |
| 金山電 | 電子零組件業 | 8042 | 2026-02-26 | 2026-03-02 | 59.00 | 2026-05-27 | 150.50 | +154.68% | 480773 | 480773 | 961546 | 0.156 | 0.79 |
| 鑫科 | 其他電子業 | 3663 | 2024-05-23 | 2024-05-24 | 43.42 | 2024-08-20 | 109.77 | +152.41% | -53000 | -53000 | -106000 | 0.140 | 0.77 |
| 晶豪科 | 半導體業 | 3006 | 2025-12-12 | 2025-12-15 | 80.95 | 2026-03-20 | 195.50 | +141.11% | 1704000 | 199499 | 1903499 | 0.229 | 0.65 |
| 誠美材 | 光電業 | 4960 | 2026-02-26 | 2026-03-02 | 13.74 | 2026-05-27 | 33.15 | +140.92% | 476092 | 6000 | 482092 | 0.029 | 0.75 |
| 定穎投控 | 電子零組件業 | 3715 | 2025-06-12 | 2025-06-13 | 41.65 | 2025-09-05 | 92.30 | +121.21% | 11002 | -3000 | 8002 | 0.052 | 0.54 |

### Bottom 10

| Name | Sector | Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD | Vol/20D |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 健策 | 電子零組件業 | 3653 | 2024-12-31 | 2025-01-02 | 1470.80 | 2025-04-09 | 748.62 | -49.50% | -122111 | -4876 | -108987 | 5.299 | 0.66 |
| 麗臺 | 電腦及週邊設備業 | 2465 | 2024-06-14 | 2024-06-17 | 125.63 | 2024-09-10 | 89.40 | -29.24% | -9000 | 0 | -9000 | 0.063 | 0.68 |
| 一詮 | 光電業 | 2486 | 2025-03-10 | 2025-03-11 | 106.33 | 2025-06-06 | 75.82 | -29.09% | 255920 | 76219 | 332139 | 0.212 | 0.75 |
| 吉茂 | 汽車工業 | 1587 | 2024-07-04 | 2024-07-05 | 59.24 | 2024-10-01 | 43.26 | -27.38% | -16000 | 0 | -16000 | 0.032 | 0.79 |
| 集盛 | 紡織纖維 | 1455 | 2024-06-14 | 2024-06-17 | 14.21 | 2024-09-10 | 10.65 | -25.45% | 1156000 | 118997 | 1263997 | 0.000 | 0.73 |
| 正基 | 通信網路業 | 6546 | 2025-09-08 | 2025-09-09 | 80.16 | 2025-12-05 | 60.70 | -24.68% | 165000 | 165000 | 330000 | 0.212 | 0.68 |
| 昶昕 | 綠能環保 | 8438 | 2024-09-27 | 2024-09-30 | 40.27 | 2024-12-26 | 30.59 | -24.43% | 110000 | 14243 | 124243 | 0.057 | 0.79 |
| 祥碩 | 半導體業 | 5269 | 2025-07-31 | 2025-08-04 | 1871.80 | 2025-10-30 | 1425.00 | -24.27% | 49671 | 742 | 25278 | 1.354 | 0.49 |
| 群電 | 電子零組件業 | 6412 | 2024-12-30 | 2024-12-31 | 114.17 | 2025-04-08 | 86.92 | -24.27% | 200712 | -702 | 195010 | 0.140 | 0.49 |
| 金麗-KY | 貿易百貨業 | 8429 | 2025-03-18 | 2025-03-19 | 10.54 | 2025-06-16 | 8.05 | -23.99% | 112000 | 19998 | 131998 | 0.008 | 0.59 |

## Guardrails

- Offline research only; no production gate, scheduler, model, DB schema, or paper portfolio mutation.
- The search intentionally caps overlays at 3 and requires minimum sample size to reduce overfit.
- `Max Avg 60D` can still be right-tail dominated. Treat `Robust 60D` as the safer operational candidate.
- Risk columns use per-trade P10 and worst 60D return; overlapping 60D trades are not compounded as an equity curve.
- Historical ChipK app broker/main-force fields are unavailable; overlays use local institutional flow, technical, volume, RSI/KD, and margin proxies.
