# OVERHEAT_RISK Calibration Audit

## Scope

- Generated at: 2026-05-02T17:27:00.659519+00:00
- Signal window: 2026-04-09 to 2026-04-30 (16 canonical days)
- Mark date: 2026-04-30
- Prediction distribution window: 2026-03-11 to 2026-04-30 (32 available days in last 60 calendar days)
- Research-only: no production model, gate, sector cap, entry filter, or paper ledger changes.

## Implementation Reality Check

- `ml/thresholds.py` / recommendation guardrail documents `price_vs_ma20 > 30%` as the CLAUDE rule #10 downgrade path.
- `scripts/build_unified_signals.py` production `OVERHEAT_RISK` uses `price_vs_ma60 > 40%` inside `_apply_overheat_risk_gate()`.
- This report therefore treats `ma60_40_production` as the true current baseline. The 30% row is a strict proxy, not the unified production baseline.

## Key Findings

- OVERHEAT_RISK rows audited: 158
- Current production mean selected count: 9.00; min selected count: 0
- Current production mean alpha vs TWII: -0.99%
- Best same-window mean alpha variant: ma60_regime_adaptive_50_30_25
- Broadest active-breadth variant: ma60_regime_adaptive_50_30_25
- Full 20D blocked-return maturity: 0 / 158 rows
- Interim blocked mark-to-date median return: -0.55%; positive rate: 47.26%

## Threshold Counterfactual Summary

| variant | mean selected | min selected | mean return | compound return | mean alpha vs TWII | rescued overheat | max name | max sector | max selected MA60 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ma60_30_strict_proxy | 6.88 | 0 | +2.05% | +34.78% | -1.92% | 0 | 100.00% | 100.00% | 29.76% |
| ma60_35 | 7.62 | 0 | +2.45% | +42.96% | -1.51% | 0 | 100.00% | 100.00% | 34.83% |
| ma60_40_production | 9.00 | 0 | +2.93% | +53.66% | -0.99% | 0 | 100.00% | 100.00% | 39.95% |
| ma60_regime_adaptive_50_30_25 | 9.69 | 1 | +3.63% | +70.00% | -0.24% | 27 | 100.00% | 100.00% | 49.80% |

## Daily Counterfactual

| date | variant | regime | threshold | selected | rescued | return | alpha | max sector | max name |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 2026-04-09 | ma60_30_strict_proxy | caution | 30.00% | 3 | 0 | +21.02% | +9.64% | 電子零組件業 66.67% | 33.33% |
| 2026-04-09 | ma60_35 | caution | 35.00% | 4 | 0 | +23.44% | +12.06% | 半導體業 50.00% | 25.00% |
| 2026-04-09 | ma60_40_production | caution | 40.00% | 5 | 0 | +20.54% | +9.17% | 電子零組件業 60.00% | 20.00% |
| 2026-04-09 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 3 | 0 | +21.02% | +9.64% | 電子零組件業 66.67% | 33.33% |
| 2026-04-10 | ma60_30_strict_proxy | caution | 30.00% | 3 | 0 | +2.20% | -7.69% | 金融保險業 100.00% | 33.33% |
| 2026-04-10 | ma60_35 | caution | 35.00% | 3 | 0 | +2.20% | -7.69% | 金融保險業 100.00% | 33.33% |
| 2026-04-10 | ma60_40_production | caution | 40.00% | 4 | 0 | +5.74% | -4.16% | 金融保險業 75.00% | 25.00% |
| 2026-04-10 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 3 | 0 | +2.20% | -7.69% | 金融保險業 100.00% | 33.33% |
| 2026-04-13 | ma60_30_strict_proxy | trending | 30.00% | 17 | 0 | +2.82% | -6.57% | 金融保險業 29.41% | 5.88% |
| 2026-04-13 | ma60_35 | trending | 35.00% | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-13 | ma60_40_production | trending | 40.00% | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-13 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 20 | 2 | +3.16% | -6.23% | 金融保險業 25.00% | 5.00% |
| 2026-04-14 | ma60_30_strict_proxy | trending | 30.00% | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | ma60_35 | trending | 35.00% | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | ma60_40_production | trending | 40.00% | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 4 | 3 | +19.72% | +12.69% | 光電業 25.00% | 25.00% |
| 2026-04-15 | ma60_30_strict_proxy | trending | 30.00% | 2 | 0 | -0.74% | -6.63% | 光電業 50.00% | 50.00% |
| 2026-04-15 | ma60_35 | trending | 35.00% | 3 | 0 | -2.03% | -7.92% | 光電業 33.33% | 33.33% |
| 2026-04-15 | ma60_40_production | trending | 40.00% | 4 | 0 | +14.84% | +8.94% | 光電業 25.00% | 25.00% |
| 2026-04-15 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 5 | 1 | +13.98% | +8.08% | 光電業 20.00% | 20.00% |
| 2026-04-16 | ma60_30_strict_proxy | trending | 30.00% | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | ma60_35 | trending | 35.00% | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | ma60_40_production | trending | 40.00% | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 1 | 1 | +3.98% | -0.82% | 電子零組件業 100.00% | 100.00% |
| 2026-04-17 | ma60_30_strict_proxy | trending | 30.00% | 10 | 0 | -3.57% | -9.20% | 光電業 30.00% | 10.00% |
| 2026-04-17 | ma60_35 | trending | 35.00% | 13 | 0 | +1.35% | -4.29% | 光電業 23.08% | 7.69% |
| 2026-04-17 | ma60_40_production | trending | 40.00% | 14 | 0 | -0.47% | -6.10% | 光電業 21.43% | 7.14% |
| 2026-04-17 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 18 | 4 | +0.23% | -5.40% | 光電業 16.67% | 5.56% |
| 2026-04-20 | ma60_30_strict_proxy | trending | 30.00% | 15 | 0 | -4.14% | -9.20% | 光電業 20.00% | 6.67% |
| 2026-04-20 | ma60_35 | trending | 35.00% | 15 | 0 | -4.14% | -9.20% | 光電業 20.00% | 6.67% |
| 2026-04-20 | ma60_40_production | trending | 40.00% | 18 | 0 | -3.95% | -9.01% | 光電業 16.67% | 5.56% |
| 2026-04-20 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 23 | 5 | -3.62% | -8.68% | 電子零組件業 17.39% | 4.35% |
| 2026-04-21 | ma60_30_strict_proxy | trending | 30.00% | 14 | 0 | -3.16% | -6.61% | 塑膠工業 28.57% | 7.14% |
| 2026-04-21 | ma60_35 | trending | 35.00% | 14 | 0 | -3.16% | -6.61% | 塑膠工業 28.57% | 7.14% |
| 2026-04-21 | ma60_40_production | trending | 40.00% | 18 | 0 | -4.00% | -7.45% | 光電業 22.22% | 5.56% |
| 2026-04-21 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 22 | 4 | -5.20% | -8.66% | 光電業 18.18% | 4.55% |
| 2026-04-22 | ma60_30_strict_proxy | trending | 30.00% | 10 | 0 | -2.29% | -4.81% | 光電業 30.00% | 10.00% |
| 2026-04-22 | ma60_35 | trending | 35.00% | 12 | 0 | -1.04% | -3.56% | 光電業 25.00% | 8.33% |
| 2026-04-22 | ma60_40_production | trending | 40.00% | 14 | 0 | -2.74% | -5.25% | 光電業 21.43% | 7.14% |
| 2026-04-22 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 18 | 4 | -3.07% | -5.59% | 光電業 16.67% | 5.56% |
| 2026-04-23 | ma60_30_strict_proxy | trending | 30.00% | 18 | 0 | +1.53% | -1.28% | 光電業 22.22% | 5.56% |
| 2026-04-23 | ma60_35 | trending | 35.00% | 18 | 0 | +1.53% | -1.28% | 光電業 22.22% | 5.56% |
| 2026-04-23 | ma60_40_production | trending | 40.00% | 21 | 0 | +0.80% | -2.01% | 其他電子業 23.81% | 4.76% |
| 2026-04-23 | ma60_regime_adaptive_50_30_25 | trending | 50.00% | 24 | 3 | -0.34% | -3.15% | 其他電子業 20.83% | 4.17% |
| 2026-04-24 | ma60_30_strict_proxy | caution | 30.00% | 2 | 0 | +4.55% | +6.89% | 通信網路業 100.00% | 50.00% |
| 2026-04-24 | ma60_35 | caution | 35.00% | 2 | 0 | +4.55% | +6.89% | 通信網路業 100.00% | 50.00% |
| 2026-04-24 | ma60_40_production | caution | 40.00% | 4 | 0 | -0.11% | +2.23% | 通信網路業 50.00% | 25.00% |
| 2026-04-24 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 2 | 0 | +4.55% | +6.89% | 通信網路業 100.00% | 50.00% |
| 2026-04-27 | ma60_30_strict_proxy | caution | 30.00% | 4 | 0 | +1.38% | +2.91% | 通信網路業 75.00% | 25.00% |
| 2026-04-27 | ma60_35 | caution | 35.00% | 5 | 0 | +0.26% | +1.79% | 通信網路業 60.00% | 20.00% |
| 2026-04-27 | ma60_40_production | caution | 40.00% | 5 | 0 | +0.26% | +1.79% | 通信網路業 60.00% | 20.00% |
| 2026-04-27 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 3 | 0 | +1.03% | +2.56% | 通信網路業 100.00% | 33.33% |
| 2026-04-28 | ma60_30_strict_proxy | caution | 30.00% | 2 | 0 | +0.00% | +0.64% | 其他電子業 50.00% | 50.00% |
| 2026-04-28 | ma60_35 | caution | 35.00% | 4 | 0 | +0.02% | +0.66% | 通信網路業 50.00% | 25.00% |
| 2026-04-28 | ma60_40_production | caution | 40.00% | 5 | 0 | +0.24% | +0.88% | 半導體業 40.00% | 20.00% |
| 2026-04-28 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 2 | 0 | +0.00% | +0.64% | 其他電子業 50.00% | 50.00% |
| 2026-04-29 | ma60_30_strict_proxy | caution | 30.00% | 5 | 0 | +1.38% | +3.01% | 其他電子業 40.00% | 20.00% |
| 2026-04-29 | ma60_35 | caution | 35.00% | 6 | 0 | +1.45% | +3.08% | 通信網路業 50.00% | 16.67% |
| 2026-04-29 | ma60_40_production | caution | 40.00% | 7 | 0 | +1.03% | +2.66% | 通信網路業 42.86% | 14.29% |
| 2026-04-29 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 4 | 0 | +0.50% | +2.12% | 其他電子業 50.00% | 25.00% |
| 2026-04-30 | ma60_30_strict_proxy | caution | 30.00% | 4 | 0 | +0.00% | - | 半導體業 50.00% | 25.00% |
| 2026-04-30 | ma60_35 | caution | 35.00% | 4 | 0 | +0.00% | - | 半導體業 50.00% | 25.00% |
| 2026-04-30 | ma60_40_production | caution | 40.00% | 6 | 0 | +0.00% | - | 半導體業 50.00% | 16.67% |
| 2026-04-30 | ma60_regime_adaptive_50_30_25 | caution | 25.00% | 3 | 0 | +0.00% | - | 半導體業 66.67% | 33.33% |

## OVERHEAT_RISK Blocked Return Diagnostics

These rows are actual `rank_20d` rows whose `tradability_reason` contains `OVERHEAT_RISK`.

| group | count | partial median | partial mean | partial positive | full 20D rows | full 20D median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL | 158 | -0.55% | +1.04% | 47.26% | 0 | - |

By sector:

| sector | count | partial median | partial positive | full 20D rows |
| --- | ---: | ---: | ---: | ---: |
| 電子零組件業 | 42 | +2.83% | 55.26% | 0 |
| 半導體業 | 35 | -2.62% | 42.42% | 0 |
| 其他電子業 | 33 | +1.68% | 54.84% | 0 |
| 通信網路業 | 26 | -7.26% | 24.00% | 0 |
| 化學工業 | 6 | +2.73% | 60.00% | 0 |
| 汽車工業 | 4 | +9.00% | 100.00% | 0 |
| 光電業 | 3 | -5.11% | 33.33% | 0 |
| 電機機械 | 3 | +11.56% | 100.00% | 0 |
| 電腦及週邊設備業 | 3 | -3.17% | 50.00% | 0 |
| 食品工業 | 3 | -15.28% | 0.00% | 0 |

## Price Divergence Distribution

| metric | sector | count | p50 | p75 | p90 | p95 | >30% | >35% | >40% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| price_vs_ma20 | 其他電子業 | 1711 | +3.51% | +10.90% | +22.01% | +30.10% | 5.08% | 3.45% | 1.87% |
| price_vs_ma20 | 半導體業 | 4528 | +4.00% | +11.49% | +22.08% | +30.10% | 5.06% | 3.07% | 1.52% |
| price_vs_ma20 | 通信網路業 | 1894 | +1.28% | +8.17% | +17.78% | +24.03% | 2.53% | 1.53% | 0.74% |
| price_vs_ma20 | 電子零組件業 | 4474 | +2.57% | +10.85% | +21.41% | +29.30% | 4.54% | 2.44% | 1.39% |
| price_vs_ma60 | 其他電子業 | 1711 | +5.63% | +23.89% | +48.37% | +67.57% | 20.86% | 17.24% | 14.32% |
| price_vs_ma60 | 半導體業 | 4528 | +6.45% | +21.78% | +47.98% | +64.08% | 18.55% | 15.59% | 13.30% |
| price_vs_ma60 | 通信網路業 | 1894 | +3.24% | +22.01% | +43.12% | +57.85% | 18.06% | 14.73% | 11.25% |
| price_vs_ma60 | 電子零組件業 | 4474 | +4.40% | +21.93% | +45.09% | +59.57% | 18.55% | 15.00% | 12.36% |

## Recommendation

Do not change production yet. The calibration evidence should be interpreted as threshold research only; the full 20D ex-post window has not matured for the 2026-04-09 to 2026-04-30 blocked rows. Among same-window variants, the MA60 40% production setting remains the clean baseline; MA60 35%/30% are stricter and worsen active breadth, while the adaptive rule must be re-tested after full 20D outcomes mature.

## Notes

- Threshold variants are simulated after rank20 selection and existing non-overheat gates; market-regime CAUTION Top10 pruning is kept unchanged.
- `rescued_overheat_count` means rows whose historical artifact had `OVERHEAT_RISK` but pass the simulated threshold and all other unchanged gates.
- Same-window basket returns use next-trading-day open to mark-date close and 100% equal-weight among selected rows. They are diagnostics, not a ledger replay.
- Full 20D ex-post returns are only populated when 20 trading bars exist after entry. As of mark date 2026-04-30, available full-20D rows = 0; use partial mark-to-date rows as interim diagnostics, not closure evidence.
- A production threshold change still requires a fixed-snapshot A/B closure artifact before promotion.
