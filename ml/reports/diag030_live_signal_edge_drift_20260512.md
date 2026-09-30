# DIAG-030 Live Signal Edge Drift 診斷

Generated: 2026-05-12T12:15:51.301349+00:00
Live window: 2026-04-23 ~ 2026-05-11
Live entries: 117; closed/stopped entries: 96
OOF reference: audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv; rows=840

## 整體診斷結論

DIAG-030 conclusion: live edge drift is present at the execution/live-signal layer. The strongest evidence is weak live rank-bucket PnL versus OOF and repeated MA5 exits; exit suppression has already failed CL3, so next action should focus on live signal quality gates or continued observation rather than another MA5 relaxation.

## 1. Live vs OOF Feature Distribution

結論：partial drift: live features available; compare sector/group and numeric shifts below

### Numeric Features

| metric | sample | n | mean | median | p25 | p75 | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| stage1_score | Live Apr-May | 117 | 0.043 | 0.035 | 0.021 | 0.061 | 117/117 |
| stage1_score | OOF Apr-May | 150 | 0.060 | 0.052 | 0.039 | 0.074 | 150/150 |
| stage1_score | OOF All | 840 | 0.053 | 0.046 | 0.034 | 0.065 | 840/840 |
| stage2_score | Live Apr-May | 0 | n/a | n/a | n/a | n/a | 0/117 |
| stage2_score | OOF Apr-May | 150 | 0.037 | 0.018 | 0.004 | 0.059 | 150/150 |
| stage2_score | OOF All | 840 | 0.034 | 0.024 | -0.013 | 0.082 | 840/840 |
| two_stage_rank | Live Apr-May | 116 | 12.509 | 10.000 | 4.750 | 20.000 | 116/117 |
| two_stage_rank | OOF Apr-May | 150 | 15.500 | 15.500 | 8.000 | 23.000 | 150/150 |
| two_stage_rank | OOF All | 840 | 15.500 | 15.500 | 8.000 | 23.000 | 840/840 |
| price_vs_ma5 | Live Apr-May | 117 | -0.013 | -0.013 | -0.044 | 0.022 | 117/117 |
| price_vs_ma5 | OOF Apr-May | 150 | 0.015 | 0.006 | -0.010 | 0.039 | 150/150 |
| price_vs_ma5 | OOF All | 839 | 0.001 | -0.001 | -0.018 | 0.020 | 839/840 |
| price_vs_ma20 | Live Apr-May | 117 | 0.018 | 0.019 | -0.032 | 0.078 | 117/117 |
| price_vs_ma20 | OOF Apr-May | 149 | 0.042 | 0.024 | -0.025 | 0.086 | 149/150 |
| price_vs_ma20 | OOF All | 833 | 0.024 | 0.017 | -0.035 | 0.072 | 833/840 |
| beta_60 | Live Apr-May | 117 | 0.891 | 0.827 | 0.590 | 1.218 | 117/117 |
| beta_60 | OOF Apr-May | 149 | 0.855 | 0.889 | 0.612 | 1.113 | 149/150 |
| beta_60 | OOF All | 830 | 0.765 | 0.751 | 0.413 | 1.095 | 830/840 |
| avg_20d_amount | Live Apr-May | 117 | 2158286855.403 | 1131360208.650 | 356984845.407 | 3424439855.500 | 117/117 |
| avg_20d_amount | OOF Apr-May | 149 | 542257972.235 | 74203865.660 | 9296818.960 | 418847137.179 | 149/150 |
| avg_20d_amount | OOF All | 833 | 1320264780.507 | 141225200.824 | 25019223.361 | 872254725.375 | 833/840 |

### Sector Share

| category | live_share | live_share_n | oof_apr_may_share | oof_apr_may_share_n | delta |
| --- | --- | --- | --- | --- | --- |
| 半導體業 | 25.64% | 30 | 14.00% | 21 | 11.64% |
| 通信網路業 | 19.66% | 23 | 8.67% | 13 | 10.99% |
| 電子零組件業 | 11.97% | 14 | 13.33% | 20 | -1.37% |
| 光電業 | 11.97% | 14 | 13.33% | 20 | -1.37% |
| 其他電子業 | 17.09% | 20 | 4.67% | 7 | 12.43% |
| 建材營造業 | 0.85% | 1 | 10.67% | 16 | -9.81% |
| 電機機械 | 6.84% | 8 | 4.00% | 6 | 2.84% |
| 其他業 | 1.71% | 2 | 7.33% | 11 | -5.62% |

### Supply-chain Group Share

| category | live_share | live_share_n | oof_apr_may_share | oof_apr_may_share_n | delta |
| --- | --- | --- | --- | --- | --- |
| OTHER | 80.34% | 94 | 66.00% | 99 | 14.34% |
| SEMI_EQUIP_MATERIALS | 11.11% | 13 | 4.67% | 7 | 6.44% |
| CONSTRUCTION_DEVELOPER | 0.85% | 1 | 6.67% | 10 | -5.81% |
| SEMI_DESIGN_FOUNDRY | 1.71% | 2 | 5.33% | 8 | -3.62% |
| OPTO_DISPLAY_LED | 2.56% | 3 | 4.67% | 7 | -2.10% |
| OPTICAL_COMM | 0.85% | 1 | 2.67% | 4 | -1.81% |
| PCB_ABF_SUBSTRATE | 0.85% | 1 | 2.00% | 3 | -1.15% |
| ELECTRONICS_CHANNEL | 0.00% | 0 | 2.00% | 3 | -2.00% |

## 2. Rank Bucket PnL

結論：drift: live Top10 avg -1.86% vs OOF Top10 4.00%; rank edge is not carrying into live exits

| rank_bucket | live_n | live_win_rate | live_avg_return | oof_n | oof_win_rate | oof_avg_return |
| --- | --- | --- | --- | --- | --- | --- |
| Top10 | 56 | 26.79% | -1.86% | 280 | 51.43% | 4.00% |
| Rank11_20 | 20 | 15.00% | -4.05% | 280 | 38.93% | 1.25% |
| Rank21_30 | 20 | 15.00% | -4.36% | 280 | 44.29% | 1.63% |
| UNKNOWN | 0 | n/a | n/a | 0 | n/a | n/a |

## 3. MA5_BREAK Exit-after Forward Return

結論：exit check: live MA5_BREAK 3D forward return is 3.26%; positive means whipsaw remains, negative means entry had no edge

| horizon | n | avg_forward_return | positive_rate | diag007_reference |
| --- | --- | --- | --- | --- |
| 1D | 82 | 1.91% | 59.76% | +1D n/a / +3D +2.45% / +5D +4.51% |
| 3D | 59 | 3.26% | 64.41% | +1D n/a / +3D +2.45% / +5D +4.51% |
| 5D | 38 | 5.89% | 71.05% | +1D n/a / +3D +2.45% / +5D +4.51% |

## 4. Repeat-entry Ticker Table

結論：friction: 20 tickers repeated >=2 entries; repeat churn is concentrated if top rows dominate

| ticker | entries | closed | compound_closed_return | avg_closed_return | ma5_exits | first_entry | last_entry |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3491 | 10 | 10 | -20.39% | -2.15% | 10 | 2026-04-23 | 2026-05-07 |
| 3131 | 8 | 8 | -10.96% | -1.37% | 8 | 2026-04-24 | 2026-05-06 |
| 6568 | 8 | 8 | -23.73% | -3.26% | 7 | 2026-04-27 | 2026-05-08 |
| 6588 | 5 | 5 | 15.70% | 3.26% | 5 | 2026-04-24 | 2026-04-30 |
| 6223 | 4 | 4 | -3.28% | -0.81% | 4 | 2026-04-29 | 2026-05-08 |
| 2485 | 4 | 3 | -11.45% | -3.86% | 2 | 2026-04-23 | 2026-05-08 |
| 3324 | 4 | 3 | -23.37% | -8.45% | 1 | 2026-04-24 | 2026-05-08 |
| 6788 | 4 | 3 | -11.93% | -4.03% | 2 | 2026-04-23 | 2026-05-08 |
| 4749 | 3 | 3 | -5.77% | -1.95% | 3 | 2026-05-05 | 2026-05-07 |
| 6226 | 3 | 3 | -16.10% | -5.62% | 2 | 2026-04-23 | 2026-05-08 |
| 3576 | 3 | 2 | -8.35% | -4.26% | 2 | 2026-04-23 | 2026-05-06 |
| 3049 | 2 | 2 | -6.94% | -3.50% | 2 | 2026-04-23 | 2026-04-24 |
| 3498 | 2 | 2 | -11.23% | -5.66% | 1 | 2026-04-23 | 2026-04-24 |
| 3580 | 2 | 2 | 6.93% | 3.41% | 2 | 2026-04-24 | 2026-04-30 |
| 6425 | 2 | 2 | -2.73% | -1.37% | 2 | 2026-05-06 | 2026-05-08 |
| 6510 | 2 | 2 | -19.81% | -10.45% | 0 | 2026-04-23 | 2026-04-24 |
| 6664 | 2 | 2 | -1.83% | -0.92% | 2 | 2026-04-23 | 2026-04-24 |
| 3533 | 2 | 1 | -0.57% | -0.57% | 1 | 2026-05-05 | 2026-05-06 |
| 4991 | 2 | 1 | -0.61% | -0.61% | 1 | 2026-04-30 | 2026-05-11 |
| 8027 | 2 | 1 | -10.45% | -10.45% | 0 | 2026-04-23 | 2026-04-24 |

## 5. April vs May Cohort PnL

結論：cohort: May avg -3.16%, win 17.95%; compare April row to locate degradation.

| entry_month | entries | closed | avg_realized | win_rate | ma5_exits | top10_entries | rank_unknown_entries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-04 | 59 | 57 | -2.61% | 24.56% | 47 | 35 | 0 |
| 2026-05 | 58 | 39 | -3.16% | 17.95% | 35 | 26 | 1 |

## RD 建議下一步

- 不建議再開 MA5 放寬類 production 票；REQ-016/018/019/021/029 已反覆顯示 MDD 或 alpha 不支持。
- 若 PM 要繼續追，建議開一張 live signal quality gate 診斷：只看 low-rank / low-liquidity / below-MA5 重複進場是否應降權，而不是改 exit。
- 5 月樣本仍短，若未達 PM 上線變更門檻，建議先累積到 20D 成熟後再做 closure。
