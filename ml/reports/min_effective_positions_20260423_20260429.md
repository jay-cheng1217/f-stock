# Minimum Effective Positions Audit

## Scope

- Generated at: 2026-05-02T17:07:24.710361+00:00
- Signal window: 2026-04-23 to 2026-04-29
- Mark date: 2026-04-30
- Minimum active threshold: 10
- Top N denominator: 30
- Return basis: next-trading-day open to mark-date close, signal-day baskets.

## Active Breadth

- Available canonical signal days in last 60 calendar days: 16 (2026-04-09 to 2026-04-30)
- Active count min/p25/median/p75/max: 0 / 4.00 / 5.50 / 15.00 / 21
- Days with active < 10: 10 (62.50%)

## Variant Summary

| variant | mean return | compound return | mean alpha vs TWII | mean selected | invested | max name | max sector |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_current | +0.44% | +2.24% | +1.11% | 8.40 | 100.00% | 25.00% | 60.00% |
| fallback_a_cash_if_under_min | +0.16% | +0.80% | +0.82% | 4.20 | 20.00% | 4.76% | 23.81% |
| fallback_b_keep_cash_buffer | +0.22% | +1.11% | +0.89% | 8.40 | 34.00% | 4.76% | 23.81% |
| fallback_c_relax_gate_to_min | +2.26% | +11.59% | +2.92% | 12.20 | 100.00% | 10.00% | 60.00% |

## Daily Counterfactual

| date | variant | active | selected | added | invested | return | alpha | max name | max sector |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
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

## Recommendation

Research recommendation: prefer fallback_b_keep_cash_buffer as the first production candidate. It sharply reduces single-name and sector concentration while preserving some exposure; fallback_c_relax_gate_to_min can improve exposure but deliberately re-admits names that gates blocked.

The first-week numbers favor cash buffering for risk control, not because it maximizes upside. It keeps the gate decision intact and prevents 4-7 names from being re-normalized to 100% exposure. The relax-gate variant is useful as a research upper bound, but it explicitly re-admits rows that production gates blocked, so it should not be promoted without a separate gate-quality review.

## Notes

- `baseline_current` uses the current `target_weight_ratio`, which re-normalizes active target units to 100%.
- `fallback_a_cash_if_under_min` goes fully to cash when active count is below the threshold.
- `fallback_b_keep_cash_buffer` keeps active 20D names at `target_units / (TopN * 2)` and leaves the rest in cash.
- `fallback_c_relax_gate_to_min` fills from blocked `rank_20d` rows until the threshold is reached, then equal-weights the basket.
- This is research-only and does not change production gates, model scoring, sector cap, or entry filters.
