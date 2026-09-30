# MA60 Support Bounce Tactical Backtest

- asof: `ma60_support_bounce_backtest_20260624`
- status: `not_actionable`
- scope: offline diagnostic only; no production gate, scheduler, or model path changed.
- strategy name: `MA60 support bounce` / tactical short trade.
- swing strategy remains the existing 20D/60D `shortwave` entry overlay.

## Rule

| Step | Rule |
| --- | --- |
| Setup after close | liquid stock, low touches MA60 support band, close remains near MA60, not chasing above MA20, volume dries up, RSI 35-56, K oversold or K>D, MACD hist >= -3.0 |
| Next-session entry | limit near signal-day MA60; count only if next session opens below the limit or trades through the limit |
| Exit diagnostics | close-based 1D/2D/3D/5D/10D returns plus MFE/MAE and 2% target/stop hit rates |
| Boundary | this is not a 20D production buy signal; it is a short-term support-bounce candidate layer |

## Universe

- daily_dir: `F:\stock\日K資料`
- start_date: `2024-01-01`
- max signals per day: `10`
- friction: `0.40%`
- universe rows: `1193294`
- raw setups: `44497`
- filled setups: `33429`
- selected trades: `5928`

## Summary

| Hold | Trades | Avg net | Median | Win | P10 | P90 | Avg MFE | Avg MAE | TP2 hit | Stop2 hit | Basket max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1D | 5928 | -0.55% | -0.53% | +30.04% | -2.40% | +1.14% | +1.39% | -1.10% | +21.47% | +16.46% | -96.40% |
| 2D | 5917 | -0.56% | -0.60% | +35.93% | -3.47% | +2.32% | +2.05% | -1.78% | +35.07% | +31.98% | -96.49% |
| 3D | 5907 | -0.53% | -0.65% | +38.70% | -4.26% | +3.23% | +2.61% | -2.30% | +43.32% | +42.03% | -96.45% |
| 5D | 5883 | -0.45% | -0.66% | +41.02% | -5.49% | +4.65% | +3.58% | -3.12% | +53.49% | +52.51% | -95.78% |
| 10D | 5837 | -0.19% | -0.69% | +44.05% | -7.62% | +7.48% | +5.58% | -4.64% | +66.15% | +65.60% | -98.12% |

## Yearly 3D Summary

| Year | Trades | Avg net | Median | Win |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 2401 | -0.57% | -0.62% | +38.11% |
| 2025 | 2366 | -0.59% | -0.60% | +39.05% |
| 2026 | 1140 | -0.32% | -0.77% | +39.21% |

## 3013 Case Study

- The 2026-06-12 after-close setup produces a 2026-06-15 MA60-limit entry around 108.57, matching the observed 108.5-style short trade.
- This validates the tactical framing: short trade yes; swing confirmation no.

| Date | Close | Low | MA60 | Vol/20D | RSI14 | MACD hist | K/D | Foreign | Dealer | Setup | Filled next day | Entry limit | Entry price | Score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |
| 2026-06-16 | 110.50 | 110.50 | 108.99 | 0.26 | 46.43 | -1.933 | 24.6/17.7 | -210939 | -31995 | True | False | 108.99 | - | 0.740 |
| 2026-06-17 | 113.00 | 109.50 | 109.21 | 0.21 | 49.10 | -1.629 | 33.1/25.6 | -183610 | -2909 | True | False | 109.21 | - | 0.670 |
| 2026-06-18 | 113.00 | 113.00 | 109.45 | 0.32 | 49.10 | -1.369 | 42.6/33.4 | -118370 | -8144 | False | False | 109.45 | - | 0.637 |
| 2026-06-22 | 113.50 | 113.00 | 109.73 | 0.28 | 49.69 | -1.114 | 57.9/44.5 | 151560 | 4050 | False | False | 109.73 | - | 0.617 |
| 2026-06-23 | 109.50 | 109.50 | 109.91 | 0.34 | 45.23 | -1.161 | 63.3/54.6 | 93605 | -76641 | True | True | 109.91 | 109.50 | 0.874 |
| 2026-06-24 | 109.50 | 107.50 | 110.13 | 0.30 | 45.23 | -1.137 | 63.2/61.4 | 156021 | -23913 | True | True | 110.13 | 110.13 | 0.775 |
| 2026-06-25 | 108.00 | 107.50 | 110.16 | 0.35 | 43.53 | -1.162 | 42.6/56.4 | 282000 | -7397 | False | False | 110.16 | - | 0.603 |
| 2026-06-26 | 103.00 | 102.50 | 110.13 | 0.43 | 38.35 | -1.438 | 24.7/43.5 | -277495 | -96937 | False | False | 110.13 | - | 0.397 |
| 2026-06-29 | 104.50 | 102.00 | 110.27 | 0.28 | 40.63 | -1.438 | 11.9/26.4 | 66360 | 58037 | False | False | 110.27 | - | 0.386 |
| 2026-06-30 | 108.00 | 105.00 | 110.41 | 0.23 | 45.68 | -1.135 | 23.1/19.9 | 80100 | 34618 | True | True | 110.41 | 109.00 | 0.584 |
| 2026-07-01 | 107.00 | 105.00 | 110.55 | 0.40 | 44.52 | -0.942 | 34.6/23.2 | 368000 | -58092 | True | True | 110.55 | 106.00 | 0.644 |
| 2026-07-02 | 108.00 | 105.00 | - | - | - | - | -/- | nan | nan | False | False | - | - | 0.050 |

## Latest Setups For Next Session

| Ticker | Name | Sector | Signal date | Entry limit | Zone | Score | Close | Vol/20D | RSI | MACD |
| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| - | - | - | - | - | - | - | - | - | - | - |

## Top 10 3D Trades

| Ticker | Name | Sector | Signal | Entry | Entry Px | Exit | Exit Px | Net | Score | Vol/20D | MACD | RSI |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 6217 | 中探針 | 電子零組件業 | 2026-01-15 | 2026-01-16 | 51.59 | 2026-01-20 | 63.80 | +23.27% | 0.837 | 0.50 | 0.069 | 50.5 |
| 2388 | 威盛 | 半導體業 | 2026-06-16 | 2026-06-17 | 68.30 | 2026-06-22 | 84.40 | +23.17% | 0.867 | 0.27 | -0.920 | 45.4 |
| 8088 | 品安 | 半導體業 | 2025-04-17 | 2025-04-18 | 24.50 | 2025-04-22 | 30.26 | +23.13% | 0.877 | 0.56 | -0.565 | 44.8 |
| 1815 | 富喬 | 電子零組件業 | 2024-11-19 | 2024-11-20 | 22.35 | 2024-11-22 | 27.30 | +21.75% | 0.807 | 0.41 | -0.032 | 49.4 |
| 3663 | 鑫科 | 其他電子業 | 2026-05-14 | 2026-05-15 | 68.34 | 2026-05-19 | 83.40 | +21.64% | 0.762 | 0.36 | -0.464 | 46.5 |
| 6215 | 和椿 | 其他電子業 | 2025-07-16 | 2025-07-17 | 96.20 | 2025-07-21 | 117.00 | +21.22% | 0.769 | 0.47 | -0.272 | 46.3 |
| 6546 | 正基 | 通信網路業 | 2024-07-04 | 2024-07-05 | 135.65 | 2024-07-09 | 164.29 | +20.72% | 0.821 | 0.30 | -2.546 | 43.2 |
| 8155 | 博智 | 電子零組件業 | 2025-08-07 | 2025-08-08 | 121.00 | 2025-08-12 | 146.50 | +20.67% | 0.830 | 0.38 | -0.645 | 46.2 |
| 8088 | 品安 | 半導體業 | 2025-04-16 | 2025-04-17 | 24.41 | 2025-04-21 | 29.52 | +20.54% | 0.856 | 0.38 | -0.672 | 45.3 |
| 4973 | 廣穎 | 半導體業 | 2026-03-05 | 2026-03-06 | 42.25 | 2026-03-10 | 50.60 | +19.36% | 0.770 | 0.33 | -0.683 | 44.2 |

## Bottom 10 3D Trades

| Ticker | Name | Sector | Signal | Entry | Entry Px | Exit | Exit Px | Net | Score | Vol/20D | MACD | RSI |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2606 | 裕民 | 航運業 | 2025-04-01 | 2025-04-02 | 60.96 | 2025-04-08 | 49.71 | -18.85% | 0.772 | 0.33 | -1.234 | 37.2 |
| 2302 | 麗正 | 半導體業 | 2025-04-01 | 2025-04-02 | 19.47 | 2025-04-08 | 15.97 | -18.41% | 0.766 | 0.36 | -0.271 | 43.3 |
| 6206 | 飛捷 | 電腦及週邊設備業 | 2025-04-01 | 2025-04-02 | 116.81 | 2025-04-08 | 96.03 | -18.19% | 0.802 | 0.38 | -2.893 | 36.4 |
| 2617 | 台航 | 航運業 | 2025-04-02 | 2025-04-07 | 26.38 | 2025-04-09 | 21.93 | -17.28% | 0.846 | 0.39 | -0.270 | 40.7 |
| 4749 | 新應材 | 半導體業 | 2024-07-31 | 2024-08-01 | 585.11 | 2024-08-05 | 487.10 | -17.15% | 0.788 | 0.54 | -2.815 | 46.0 |
| 6219 | 富旺 | 建材營造業 | 2024-09-18 | 2024-09-19 | 44.71 | 2024-09-23 | 37.38 | -16.80% | 0.836 | 0.36 | -0.198 | 47.9 |
| 3078 | 僑威 | 電子零組件業 | 2025-04-01 | 2025-04-02 | 82.51 | 2025-04-08 | 69.60 | -16.05% | 0.834 | 0.48 | -0.425 | 45.5 |
| 5302 | 太欣 | 半導體業 | 2024-08-01 | 2024-08-02 | 12.60 | 2024-08-06 | 10.70 | -15.48% | 0.891 | 0.43 | -0.145 | 46.4 |
| 2015 | 豐興 | 鋼鐵工業 | 2025-04-01 | 2025-04-02 | 66.82 | 2025-04-08 | 57.10 | -14.95% | 0.789 | 0.43 | -0.410 | 48.7 |
| 2313 | 華通 | 電子零組件業 | 2024-07-31 | 2024-08-01 | 74.06 | 2024-08-05 | 63.77 | -14.30% | 0.791 | 0.59 | -1.122 | 43.6 |

## SA Reading

- Use this layer as a separate tactical card next to the swing card, not as a replacement for the swing model.
- A valid tactical signal should say: entry limit, stop, take-profit band, and max holding day.
- A stock can be tactical-positive and swing-negative at the same time; 3013 on 2026-06-15 is exactly that shape.
- Next step after PM/user review: add `tactical_bounce_*` fields to the prediction/reporting surface and show both tactical and swing strategies in the web UI.
