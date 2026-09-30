# REQ-026 Large-Cap Flow Relaxation Backtest

## Summary

| Metric | Control | Treatment | Delta |
|---|---:|---:|---:|
| Monthly return | 1.87% | 2.98% | 1.10% |
| Monthly alpha vs TWII | -0.86% | 0.25% | 1.10% |
| Monthly alpha vs strategy-fit | 0.52% | 1.63% | 1.10% |
| MDD | -2.13% | -7.96% | -5.84% |
| Sharpe | 2.070 | 1.468 | -0.602 |
| Calmar | 10.561 | 4.485 | -6.076 |
| Avg positions | 2.385 | 4.804 | 2.419 |

## Decision Checks

| Gate | Treatment Result |
|---|---:|
| Monthly alpha vs TWII > +1pp | FAIL |
| MDD not worse than -15% | PASS |
| Calmar > 1.0 | PASS |
| Mainline gate | FAIL |

## Signal And Exit Analysis

- Control exit reasons: `{"FOREIGN_SELL_1D": 500, "HWM_8PCT_TRAIL": 15, "20D_TIMEOUT": 1}`
- Treatment exit reasons: `{"FOREIGN_SELL_2D": 332, "HWM_8PCT_TRAIL": 90, "20D_TIMEOUT": 13}`
- Treatment top tickers: `{"3017": 19, "2330": 18, "3231": 18, "2382": 15, "2317": 15, "1519": 14, "2383": 14, "2454": 14, "2308": 14, "6669": 13}`
- Treatment sector distribution: `{"半導體業": 121, "電子零組件業": 99, "電腦及週邊設備業": 81, "其他電子業": 38, "電機機械": 28, "航運業": 25, "通信網路業": 21, "光電業": 8, "塑膠工業": 7, "金融保險業": 5, "電器電纜": 3, "電子通路業": 3, "生技醫療業": 2, "鋼鐵工業": 2, "化學工業": 1, "玻璃陶瓷": 1}`

## April 2026 Replay

- Treatment 2330/2454/2308 closed trades: 1
- Treatment 2330/2454/2308 open positions at replay end: 1

| ticker | entry | exit | return | reason | hold_days |
|---|---|---|---:|---|---:|
| 2308 | 2026-04-10 | 2026-04-14 | 0.86% | FOREIGN_SELL_2D | 2 |

| ticker | entry | as_of | unrealized | hold_days | status |
|---|---|---|---:|---:|---|
| 2454 | 2026-04-15 | 2026-05-08 | 102.79% | 16 | open |

## Artifacts

- Monthly comparison CSV: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510_monthly.csv`
- Treatment trades CSV: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510_treatment_trades.csv`
- Treatment open positions CSV: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510_treatment_open_positions.csv`
- Treatment signals CSV: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510_treatment_signals.csv`
- April replay CSV: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510_april2026.csv`
- JSON: `F:\stock\ml\reports\req026_large_cap_flow_relaxation_20260510.json`
