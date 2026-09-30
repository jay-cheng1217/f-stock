# Friend ChipK-Style Logic Backtest

- Generated: `20260623_3013_review`
- Daily data dir: `F:\stock\日K資料`
- Signal window: `2024-01-01` to `latest`
- Execution: signal after close, enter next open, fixed holding-day close exits
- Friction: `0.40%` per round trip
- Max selected per signal day: `10`

## Rule Translation

- `friend_base`: foreign turns from previous 5-day net selling to buy; total institutions net buy; MACD histogram improves; price is near MA60 support and not extended above MA20; volume is alive but not a spike.
- `friend_strict`: requires 5 straight prior foreign sell days, dealer not selling, 2-day MACD improvement, above MA5, and tighter volume/price chase limits.
- `friend_loose`: allows either foreign or total institutional turn-buy, with MACD improvement and looser support/chase filters.

## Summary

| Rule | Raw Signals | Selected | Hold | Avg Net | Median Net | Win Rate | Basket Cum | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| friend_base | 15331 | 5373 | 5D | -0.40% | -0.64% | +41.27% | -92.24% | -94.17% |
| friend_base | 15331 | 5373 | 10D | -0.15% | -0.60% | +43.78% | -65.95% | -94.91% |
| friend_base | 15331 | 5373 | 20D | +0.52% | -0.58% | +46.14% | +808.81% | -99.21% |
| friend_strict | 401 | 401 | 5D | -0.53% | -0.94% | +37.37% | -74.72% | -79.75% |
| friend_strict | 401 | 401 | 10D | -0.08% | -0.53% | +45.82% | -55.31% | -81.90% |
| friend_strict | 401 | 401 | 20D | +0.64% | -0.67% | +45.92% | +196.35% | -89.54% |
| friend_loose | 43288 | 5845 | 5D | -0.27% | -0.63% | +41.81% | -81.19% | -89.50% |
| friend_loose | 43288 | 5845 | 10D | +0.03% | -0.54% | +44.80% | +11.43% | -94.30% |
| friend_loose | 43288 | 5845 | 20D | +0.63% | -0.56% | +46.29% | +2582.32% | -99.34% |

## Yearly 10D Check

| Rule | Year | Trades | Avg Net | Win Rate |
|---|---:|---:|---:|---:|
| friend_base | 2024 | 2263 | -0.34% | +41.98% |
| friend_base | 2025 | 2101 | -0.50% | +43.22% |
| friend_base | 2026 | 926 | +1.09% | +49.46% |
| friend_strict | 2024 | 220 | -0.45% | +44.55% |
| friend_strict | 2025 | 124 | -0.62% | +45.97% |
| friend_strict | 2026 | 51 | +2.81% | +50.98% |
| friend_loose | 2024 | 2391 | -0.23% | +42.95% |
| friend_loose | 2025 | 2373 | -0.26% | +44.67% |
| friend_loose | 2026 | 991 | +1.39% | +49.55% |

## 3013 Current Diagnostic

| Date | Close | Foreign | Dealer | Inst Total | MACD Hist | Vol/20D | Base | Strict | Loose |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| 2026-06-18 | 113.00 | -118370 | -8144 | -126514 | -1.369 | 0.32 | False | False | False |
| 2026-06-22 | 113.50 | 151560 | 4050 | 155610 | -1.114 | 0.28 | True | True | True |
| 2026-06-23 | 109.50 | 93605 | -76641 | 16964 | -1.161 | 0.34 | False | False | False |

## Interpretation Guardrails

- This is an offline research backtest; it does not authorize a production gate change.
- The local historical data does not include historical ChipK broker-branch or main-force app rows, so those parts are approximated by foreign/dealer/trust flow and price/volume behavior.
- Results use next-open execution to avoid same-day lookahead.
