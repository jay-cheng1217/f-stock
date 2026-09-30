# Minimum Effective Positions Audit

## Scope

- Generated at: 2026-05-02T17:50:00.741758+00:00
- Signal window: 2026-04-09 to 2026-04-30
- Mark date: 2026-04-30
- Minimum active threshold: 10
- Top N denominator: 30
- Return basis: next-trading-day open to mark-date close, signal-day baskets.

## Active Breadth

- Available canonical signal days in last 60 calendar days: 16 (2026-04-09 to 2026-04-30)
- Active count min/p25/median/p75/max: 0 / 4.00 / 5.50 / 15.00 / 21
- Days with active < 10: 10 (62.50%)

## Key Findings

- fallback_b alpha delta vs baseline: -3.17% (baseline -0.99% vs fallback_b -4.16%)
- fallback_b max sector: 27.78%; passes <=25% check: False
- fallback_b daily volatility: 2.00% vs baseline 6.75%
- fallback_b passes no-material-alpha-sacrifice check: False

## Variant Summary

| variant | mean return | compound return | mean alpha vs TWII | vol | Sortino | alpha vol | mean selected | invested | cash | max name | max sector |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_current | +2.93% | +53.66% | -0.99% | 6.75% | 1.75 | 5.65% | 9.00 | 93.75% | 6.25% | 100.00% | 100.00% |
| fallback_a_cash_if_under_min | -0.47% | -7.49% | -4.62% | 1.66% | -0.33 | 4.28% | 6.44 | 37.50% | 62.50% | 7.14% | 27.78% |
| fallback_b_keep_cash_buffer | -0.04% | -0.99% | -4.16% | 2.00% | -0.02 | 3.92% | 9.00 | 46.04% | 53.96% | 7.14% | 27.78% |
| fallback_c_relax_gate_to_min | +4.22% | +87.81% | +0.39% | 6.62% | 3.10 | 6.05% | 12.00 | 100.00% | 0.00% | 20.00% | 60.00% |

## Daily Counterfactual

| date | variant | active | selected | added | invested | return | alpha | max name | max sector |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-04-09 | baseline_current | 5 | 5 | 0 | 100.00% | +20.54% | +9.17% | 20.00% | 電子零組件業 60.00% |
| 2026-04-09 | fallback_a_cash_if_under_min | 5 | 0 | 0 | 0.00% | +0.00% | -11.38% | 0.00% | None 0.00% |
| 2026-04-09 | fallback_b_keep_cash_buffer | 5 | 5 | 0 | 16.67% | +3.42% | -7.95% | 3.33% | 電子零組件業 10.00% |
| 2026-04-09 | fallback_c_relax_gate_to_min | 5 | 10 | 5 | 100.00% | +12.87% | +1.49% | 10.00% | 電子零組件業 40.00% |
| 2026-04-10 | baseline_current | 4 | 4 | 0 | 100.00% | +5.74% | -4.16% | 25.00% | 金融保險業 75.00% |
| 2026-04-10 | fallback_a_cash_if_under_min | 4 | 0 | 0 | 0.00% | +0.00% | -9.89% | 0.00% | None 0.00% |
| 2026-04-10 | fallback_b_keep_cash_buffer | 4 | 4 | 0 | 13.33% | +0.76% | -9.13% | 3.33% | 金融保險業 10.00% |
| 2026-04-10 | fallback_c_relax_gate_to_min | 4 | 8 | 4 | 100.00% | +11.22% | +1.33% | 12.50% | 金融保險業 37.50% |
| 2026-04-13 | baseline_current | 18 | 18 | 0 | 100.00% | +2.81% | -6.58% | 5.56% | 金融保險業 27.78% |
| 2026-04-13 | fallback_a_cash_if_under_min | 18 | 18 | 0 | 100.00% | +2.81% | -6.58% | 5.56% | 金融保險業 27.78% |
| 2026-04-13 | fallback_b_keep_cash_buffer | 18 | 18 | 0 | 100.00% | +2.81% | -6.58% | 5.56% | 金融保險業 27.78% |
| 2026-04-13 | fallback_c_relax_gate_to_min | 18 | 18 | 0 | 100.00% | +2.81% | -6.58% | 5.56% | 金融保險業 27.78% |
| 2026-04-14 | baseline_current | 1 | 1 | 0 | 100.00% | +11.89% | +4.86% | 100.00% | 光電業 100.00% |
| 2026-04-14 | fallback_a_cash_if_under_min | 1 | 0 | 0 | 0.00% | +0.00% | -7.03% | 0.00% | None 0.00% |
| 2026-04-14 | fallback_b_keep_cash_buffer | 1 | 1 | 0 | 3.33% | +0.40% | -6.64% | 3.33% | 光電業 3.33% |
| 2026-04-14 | fallback_c_relax_gate_to_min | 1 | 5 | 4 | 100.00% | +17.67% | +10.64% | 20.00% | 光電業 20.00% |
| 2026-04-15 | baseline_current | 4 | 4 | 0 | 100.00% | +14.84% | +8.94% | 25.00% | 光電業 25.00% |
| 2026-04-15 | fallback_a_cash_if_under_min | 4 | 0 | 0 | 0.00% | +0.00% | -5.90% | 0.00% | None 0.00% |
| 2026-04-15 | fallback_b_keep_cash_buffer | 4 | 4 | 0 | 13.33% | +1.98% | -3.92% | 3.33% | 光電業 3.33% |
| 2026-04-15 | fallback_c_relax_gate_to_min | 4 | 9 | 5 | 100.00% | +9.75% | +3.86% | 11.11% | 光電業 22.22% |
| 2026-04-16 | baseline_current | 0 | 0 | 0 | 0.00% | +0.00% | -4.79% | 0.00% | None 0.00% |
| 2026-04-16 | fallback_a_cash_if_under_min | 0 | 0 | 0 | 0.00% | +0.00% | -4.79% | 0.00% | None 0.00% |
| 2026-04-16 | fallback_b_keep_cash_buffer | 0 | 0 | 0 | 0.00% | +0.00% | -4.79% | 0.00% | None 0.00% |
| 2026-04-16 | fallback_c_relax_gate_to_min | 0 | 7 | 7 | 100.00% | +13.12% | +8.32% | 14.29% | 電子零組件業 42.86% |
| 2026-04-17 | baseline_current | 14 | 14 | 0 | 100.00% | -0.47% | -6.10% | 7.14% | 光電業 21.43% |
| 2026-04-17 | fallback_a_cash_if_under_min | 14 | 14 | 0 | 100.00% | -0.47% | -6.10% | 7.14% | 光電業 21.43% |
| 2026-04-17 | fallback_b_keep_cash_buffer | 14 | 14 | 0 | 100.00% | -0.47% | -6.10% | 7.14% | 光電業 21.43% |
| 2026-04-17 | fallback_c_relax_gate_to_min | 14 | 14 | 0 | 100.00% | -0.47% | -6.10% | 7.14% | 光電業 21.43% |
| 2026-04-20 | baseline_current | 18 | 18 | 0 | 100.00% | -3.95% | -9.01% | 5.56% | 光電業 16.67% |
| 2026-04-20 | fallback_a_cash_if_under_min | 18 | 18 | 0 | 100.00% | -3.95% | -9.01% | 5.56% | 光電業 16.67% |
| 2026-04-20 | fallback_b_keep_cash_buffer | 18 | 18 | 0 | 100.00% | -3.95% | -9.01% | 5.56% | 光電業 16.67% |
| 2026-04-20 | fallback_c_relax_gate_to_min | 18 | 18 | 0 | 100.00% | -3.95% | -9.01% | 5.56% | 光電業 16.67% |
| 2026-04-21 | baseline_current | 18 | 18 | 0 | 100.00% | -4.00% | -7.45% | 5.56% | 光電業 22.22% |
| 2026-04-21 | fallback_a_cash_if_under_min | 18 | 18 | 0 | 100.00% | -4.00% | -7.45% | 5.56% | 光電業 22.22% |
| 2026-04-21 | fallback_b_keep_cash_buffer | 18 | 18 | 0 | 100.00% | -4.00% | -7.45% | 5.56% | 光電業 22.22% |
| 2026-04-21 | fallback_c_relax_gate_to_min | 18 | 18 | 0 | 100.00% | -4.00% | -7.45% | 5.56% | 光電業 22.22% |
| 2026-04-22 | baseline_current | 14 | 14 | 0 | 100.00% | -2.74% | -5.25% | 7.14% | 光電業 21.43% |
| 2026-04-22 | fallback_a_cash_if_under_min | 14 | 14 | 0 | 100.00% | -2.74% | -5.25% | 7.14% | 光電業 21.43% |
| 2026-04-22 | fallback_b_keep_cash_buffer | 14 | 14 | 0 | 100.00% | -2.74% | -5.25% | 7.14% | 光電業 21.43% |
| 2026-04-22 | fallback_c_relax_gate_to_min | 14 | 14 | 0 | 100.00% | -2.74% | -5.25% | 7.14% | 光電業 21.43% |
| 2026-04-23 | baseline_current | 21 | 21 | 0 | 100.00% | +0.80% | -2.01% | 4.76% | 其他電子業 23.81% |
| 2026-04-23 | fallback_a_cash_if_under_min | 21 | 21 | 0 | 100.00% | +0.80% | -2.01% | 4.76% | 其他電子業 23.81% |
| 2026-04-23 | fallback_b_keep_cash_buffer | 21 | 21 | 0 | 100.00% | +0.80% | -2.01% | 4.76% | 其他電子業 23.81% |
| 2026-04-23 | fallback_c_relax_gate_to_min | 21 | 21 | 0 | 100.00% | +0.80% | -2.01% | 4.76% | 其他電子業 23.81% |
| 2026-04-24 | baseline_current | 4 | 4 | 0 | 100.00% | -0.11% | +2.23% | 25.00% | 通信網路業 50.00% |
| 2026-04-24 | fallback_a_cash_if_under_min | 4 | 0 | 0 | 0.00% | +0.00% | +2.34% | 0.00% | None 0.00% |
| 2026-04-24 | fallback_b_keep_cash_buffer | 4 | 4 | 0 | 13.33% | -0.01% | +2.32% | 3.33% | 通信網路業 6.67% |
| 2026-04-24 | fallback_c_relax_gate_to_min | 4 | 10 | 6 | 100.00% | -1.63% | +0.70% | 10.00% | 半導體業 60.00% |
| 2026-04-27 | baseline_current | 5 | 5 | 0 | 100.00% | +0.26% | +1.79% | 20.00% | 通信網路業 60.00% |
| 2026-04-27 | fallback_a_cash_if_under_min | 5 | 0 | 0 | 0.00% | +0.00% | +1.53% | 0.00% | None 0.00% |
| 2026-04-27 | fallback_b_keep_cash_buffer | 5 | 5 | 0 | 16.67% | +0.04% | +1.58% | 3.33% | 通信網路業 10.00% |
| 2026-04-27 | fallback_c_relax_gate_to_min | 5 | 10 | 5 | 100.00% | +6.74% | +8.27% | 10.00% | 通信網路業 40.00% |
| 2026-04-28 | baseline_current | 5 | 5 | 0 | 100.00% | +0.24% | +0.88% | 20.00% | 半導體業 40.00% |
| 2026-04-28 | fallback_a_cash_if_under_min | 5 | 0 | 0 | 0.00% | +0.00% | +0.64% | 0.00% | None 0.00% |
| 2026-04-28 | fallback_b_keep_cash_buffer | 5 | 5 | 0 | 16.67% | +0.04% | +0.68% | 3.33% | 半導體業 6.67% |
| 2026-04-28 | fallback_c_relax_gate_to_min | 5 | 10 | 5 | 100.00% | +4.40% | +5.04% | 10.00% | 半導體業 40.00% |
| 2026-04-29 | baseline_current | 7 | 7 | 0 | 100.00% | +1.03% | +2.66% | 14.29% | 通信網路業 42.86% |
| 2026-04-29 | fallback_a_cash_if_under_min | 7 | 0 | 0 | 0.00% | +0.00% | +1.63% | 0.00% | None 0.00% |
| 2026-04-29 | fallback_b_keep_cash_buffer | 7 | 7 | 0 | 23.33% | +0.24% | +1.87% | 3.33% | 通信網路業 10.00% |
| 2026-04-29 | fallback_c_relax_gate_to_min | 7 | 10 | 3 | 100.00% | +1.00% | +2.63% | 10.00% | 半導體業 40.00% |
| 2026-04-30 | baseline_current | 6 | 6 | 0 | 100.00% | +0.00% | - | 16.67% | 半導體業 50.00% |
| 2026-04-30 | fallback_a_cash_if_under_min | 6 | 0 | 0 | 0.00% | +0.00% | - | 0.00% | None 0.00% |
| 2026-04-30 | fallback_b_keep_cash_buffer | 6 | 6 | 0 | 20.00% | +0.00% | - | 3.33% | 半導體業 10.00% |
| 2026-04-30 | fallback_c_relax_gate_to_min | 6 | 10 | 4 | 100.00% | +0.00% | - | 10.00% | 半導體業 40.00% |

## Recommendation

Do not promote fallback_b_keep_cash_buffer as a standalone production change from this window. It is a strong concentration brake, but it fails the no-material-alpha-sacrifice check and does not fully keep max sector <= 25% because sufficient-breadth days still use baseline weights. Keep it as a portfolio-risk candidate to combine with gate calibration, not as the main alpha fix.

fallback_b keeps the gate decision intact and prevents 4-7 names from being re-normalized to 100% exposure, but the longer available window shows that many low-breadth days were strong up days. The cash buffer therefore reduces volatility and concentration by giving up too much upside in this sample. The relax-gate variant remains a research upper bound because it explicitly re-admits rows that production gates blocked.

## Notes

- `baseline_current` uses the current `target_weight_ratio`, which re-normalizes active target units to 100%.
- `fallback_a_cash_if_under_min` goes fully to cash when active count is below the threshold.
- `fallback_b_keep_cash_buffer` keeps active 20D names at `target_units / (TopN * 2)` and leaves the rest in cash.
- `fallback_c_relax_gate_to_min` fills from blocked `rank_20d` rows until the threshold is reached, then equal-weights the basket.
- This is research-only and does not change production gates, model scoring, sector cap, or entry filters.
