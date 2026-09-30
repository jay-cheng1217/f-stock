# REQ-009 2026-04 Regime-only vs Production-only Realized Return

- Source: `ml\reports\req009_regime_overlap_detail_20260508_detail.csv`
- Window: 2026-04 fold, prediction date 2026-04-08
- Return basis: dataset `trade_return_20d` / `trade_excess_return_20d` from the same fold snapshot.

| Group | Count | Avg 20D | Median 20D | Positive | Avg alpha | Median alpha | Avg beta | Large-liquidity proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| regime_only | 22 | +22.53% | +19.15% | +81.82% | +1.90% | -1.49% | 1.21 | 15 |
| production_only | 22 | +23.72% | +16.94% | +86.36% | +3.09% | -3.69% | 1.30 | 18 |

## Delta

- Avg 20D return delta: -1.19%
- Avg alpha delta: -1.19%
- Positive-rate delta: -4.55%

## Sector Summary

| Group | Sector | Count | Avg 20D | Avg alpha |
|---|---|---:|---:|---:|
| production_only | 其他電子業 | 5 | +22.49% | +1.86% |
| production_only | 電子零組件業 | 5 | +49.61% | +28.97% |
| production_only | 半導體業 | 4 | +17.99% | -2.65% |
| production_only | 光電業 | 2 | +2.10% | -18.53% |
| production_only | 通信網路業 | 2 | +22.04% | +1.41% |
| production_only | 玻璃陶瓷 | 1 | +15.12% | -5.51% |
| production_only | 綠能環保 | 1 | +8.72% | -11.91% |
| production_only | 電腦及週邊設備業 | 1 | +6.86% | -13.77% |
| production_only | 食品工業 | 1 | +10.38% | -10.25% |
| regime_only | 半導體業 | 11 | +23.24% | +2.61% |
| regime_only | 通信網路業 | 3 | +9.77% | -10.86% |
| regime_only | 其他電子業 | 2 | +12.53% | -8.10% |
| regime_only | 電子零組件業 | 2 | +40.29% | +19.65% |
| regime_only | 電腦及週邊設備業 | 2 | +35.66% | +15.03% |
| regime_only | 光電業 | 1 | -19.06% | -39.69% |
| regime_only | 電機機械 | 1 | +52.81% | +32.18% |

## Top / Bottom Names

### regime_only top 5

| Ticker | Sector | 20D | Alpha | Beta | Size |
|---|---|---:|---:|---:|---|
| 6141 | 電子零組件業 | +56.32% | +35.68% | 0.58 | small_liquidity_proxy |
| 8027 | 電機機械 | +52.81% | +32.18% | 0.42 | large_liquidity_proxy |
| 5386 | 電腦及週邊設備業 | +52.63% | +32.00% | 1.07 | mid_liquidity_proxy |
| 7734 | 半導體業 | +50.10% | +29.47% | 1.35 | large_liquidity_proxy |
| 2351 | 半導體業 | +42.31% | +21.67% | 0.96 | mid_liquidity_proxy |

### regime_only bottom 5

| Ticker | Sector | 20D | Alpha | Beta | Size |
|---|---|---:|---:|---:|---|
| 7751 | 半導體業 | +9.39% | -11.24% | 1.90 | mid_liquidity_proxy |
| 3163 | 通信網路業 | -6.06% | -26.69% | 1.70 | large_liquidity_proxy |
| 6640 | 半導體業 | -7.33% | -27.97% | 1.33 | large_liquidity_proxy |
| 6739 | 其他電子業 | -13.02% | -33.65% | 1.34 | large_liquidity_proxy |
| 3576 | 光電業 | -19.06% | -39.69% | 0.86 | large_liquidity_proxy |

### production_only top 5

| Ticker | Sector | 20D | Alpha | Beta | Size |
|---|---|---:|---:|---:|---|
| 6274 | 電子零組件業 | +97.67% | +77.04% | 1.43 | large_liquidity_proxy |
| 2345 | 通信網路業 | +47.98% | +27.34% | 1.58 | large_liquidity_proxy |
| 8046 | 電子零組件業 | +47.28% | +26.65% | 1.83 | large_liquidity_proxy |
| 8021 | 其他電子業 | +46.22% | +25.59% | 1.73 | large_liquidity_proxy |
| 4989 | 電子零組件業 | +44.26% | +23.62% | 1.35 | large_liquidity_proxy |

### production_only bottom 5

| Ticker | Sector | 20D | Alpha | Beta | Size |
|---|---|---:|---:|---:|---|
| 3017 | 電腦及週邊設備業 | +6.86% | -13.77% | 1.18 | large_liquidity_proxy |
| 5351 | 半導體業 | +4.46% | -16.17% | 1.77 | large_liquidity_proxy |
| 6442 | 通信網路業 | -3.90% | -24.53% | 1.32 | large_liquidity_proxy |
| 3563 | 光電業 | -4.00% | -24.63% | 0.80 | large_liquidity_proxy |
| 3131 | 其他電子業 | -7.28% | -27.91% | 0.96 | large_liquidity_proxy |
