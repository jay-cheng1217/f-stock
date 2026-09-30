# Fundamental Veto Backtest (Serenity overlay evaluation)

Generated: 2026-07-02 22:35. Research-only.
Base signal: 貼MA20 ±5% + vol ratio <1.5 + foreign net buy
+ volume ≥500,000 shares, cooldown 20d, friction 0.4%,
entry next-day open, start 2021-01-01. Point-in-time fundamentals
(quarterly filing deadlines; monthly revenue +10d lag).

### All base signals  (n=40298)

| horizon | mean | median | win rate |
| --- | --- | --- | --- |
| 20d | +0.50% | -1.01% | 45.0% |
| 60d | +3.50% | -1.11% | 46.8% |

### PASS (no fundamental veto)  (n=30688)

| horizon | mean | median | win rate |
| --- | --- | --- | --- |
| 20d | +0.55% | -0.80% | 45.9% |
| 60d | +3.70% | -0.58% | 48.2% |

### VETOED (OM<0 or rev 3M-avg YoY < -20%)  (n=9610)

| horizon | mean | median | win rate |
| --- | --- | --- | --- |
| 20d | +0.31% | -1.72% | 42.2% |
| 60d | +2.86% | -2.88% | 42.2% |

### veto detail: operating margin < 0  (n=8384)

| horizon | mean | median | win rate |
| --- | --- | --- | --- |
| 20d | +0.24% | -1.96% | 41.7% |
| 60d | +2.65% | -3.49% | 40.6% |

### veto detail: revenue 3M-avg YoY < -20%  (n=1955)

| horizon | mean | median | win rate |
| --- | --- | --- | --- |
| 20d | +0.76% | -0.84% | 44.5% |
| 60d | +4.22% | +0.20% | 50.6% |

### Yearly delta (PASS mean - VETO mean, 20d)

| year | pass n | veto n | pass 20d | veto 20d | delta |
| --- | --- | --- | --- | --- | --- |
| 2021 | 6736 | 1493 | +0.94% | +0.98% | -0.03% |
| 2022 | 5472 | 1252 | -1.44% | -1.26% | -0.17% |
| 2023 | 5629 | 2043 | +1.78% | +0.71% | +1.07% |
| 2024 | 6099 | 2347 | +0.23% | -0.22% | +0.46% |
| 2025 | 5398 | 1891 | +0.31% | +0.54% | -0.22% |
| 2026 | 1354 | 584 | +3.95% | +2.01% | +1.94% |
