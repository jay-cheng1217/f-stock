# REQ-025 Large-Cap Institutional Flow Portfolio Backtest

## Summary

| Metric | Value |
|---|---:|
| Monthly return | 1.87% |
| Monthly alpha vs TWII | -0.86% |
| Monthly alpha vs strategy-fit proxy | 0.52% |
| MDD | -2.13% |
| Sharpe | 2.070 |
| Calmar | 10.561 |
| Positive months | 18/28 |
| Avg positions | 2.38 |
| Mainline gate | FAIL |

## Decision Checks

| Gate | Result |
|---|---:|
| Monthly alpha vs TWII > +1pp | FAIL |
| MDD not worse than -15% | PASS |
| Calmar > 1.0 | PASS |

## Signal Analysis

- Signals: 513
- Avg monthly signals: 18.32
- Top tickers: `{"2330": 24, "3017": 24, "2317": 22, "2308": 20, "2382": 19, "3231": 19, "2454": 17, "2383": 15, "1519": 15, "3443": 14}`
- Sector distribution: `{"半導體業": 136, "電子零組件業": 112, "電腦及週邊設備業": 93, "其他電子業": 53, "電機機械": 34, "航運業": 25, "通信網路業": 22, "塑膠工業": 10, "光電業": 7, "金融保險業": 5, "電器電纜": 4, "電子通路業": 3, "生技醫療業": 3, "鋼鐵工業": 3, "玻璃陶瓷": 2, "化學工業": 1}`
- Exit reasons: `{"FOREIGN_FLIP_SELL": 494, "HWM_8PCT_TRAIL": 14, "20D_TIMEOUT": 1}`
- Strategy-fit benchmark: historical OOF daily-K stock-universe equal-weight proxy; live weekly reports use prediction CSV union.

## April 2026 Replay

- Closed trades with entry 2026-04-01 to 2026-05-08: 31
- 2330/2454 trades: 2

| ticker | entry | exit | return | reason |
|---|---|---|---:|---|
| 2454 | 2026-04-16 | 2026-04-20 | 0.26% | FOREIGN_FLIP_SELL |
| 2454 | 2026-04-27 | 2026-04-29 | 5.75% | FOREIGN_FLIP_SELL |

## Artifacts

- Monthly CSV: `F:\stock\ml\reports\req025_large_cap_flow_20260510_monthly.csv`
- Daily CSV: `F:\stock\ml\reports\req025_large_cap_flow_20260510_daily.csv`
- Trades CSV: `F:\stock\ml\reports\req025_large_cap_flow_20260510_trades.csv`
- Signals CSV: `F:\stock\ml\reports\req025_large_cap_flow_20260510_signals.csv`
- April replay CSV: `F:\stock\ml\reports\req025_large_cap_flow_20260510_april2026.csv`
- JSON: `F:\stock\ml\reports\req025_large_cap_flow_20260510.json`
