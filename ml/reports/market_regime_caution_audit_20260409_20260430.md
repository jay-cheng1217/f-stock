# MARKET_REGIME_CAUTION Top-N Audit

## Scope

- Generated at: 2026-05-02T17:43:42.111782+00:00
- Signal window: 2026-04-09 to 2026-04-30 (16 canonical days)
- Mark date: 2026-04-30
- Research-only: no production gate, model, sector cap, entry filter, or paper ledger changes.

## Implementation Reality Check

- Production path: `scripts/build_unified_signals.py::_apply_market_regime_gate()`.
- Current CAUTION behavior: keep `rank_20d <= 10`; block the rest with `MARKET_REGIME_CAUTION`.
- This audit changes only that rank limit. Liquidity, OVERHEAT_RISK, beta, alpha-win, and disposition gates stay unchanged.

## Key Findings

- MARKET_REGIME_CAUTION blocked rows audited: 70
- Current production mean selected count: 9.00; caution-day mean selected: 5.14
- Current production mean alpha vs TWII: -0.99%
- Best same-window mean alpha variant: top30_no_prune
- Broadest active-breadth variant: top30_no_prune
- Full 20D blocked-return maturity: 0 / 70 rows
- Interim blocked mark-to-date median return: +4.74%; positive rate: 75.00%

## Rank-Limit Counterfactual Summary

| variant | mean selected | caution-day selected | min selected | mean return | compound return | mean alpha vs TWII | rescued rows | max name | max sector |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| top10_production | 9.00 | 5.14 | 0 | +2.93% | +53.66% | -0.99% | 0 | 100.00% | 100.00% |
| top15 | 10.12 | 7.71 | 0 | +2.83% | +51.88% | -1.10% | 18 | 100.00% | 100.00% |
| top20 | 11.06 | 9.86 | 0 | +3.05% | +57.32% | -0.86% | 33 | 100.00% | 100.00% |
| top30_no_prune | 13.31 | 15.00 | 0 | +3.31% | +63.96% | -0.58% | 69 | 100.00% | 100.00% |

## Daily Counterfactual

| date | variant | action | pre-market pool | selected | rescued | return | alpha | max sector | max name |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 2026-04-09 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 5 | 0 | +20.54% | +9.17% | 電子零組件業 60.00% | 20.00% |
| 2026-04-09 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 10 | 5 | +17.51% | +6.13% | 半導體業 50.00% | 10.00% |
| 2026-04-09 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 12 | 7 | +17.80% | +6.42% | 半導體業 50.00% | 8.33% |
| 2026-04-09 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 17 | 12 | +16.74% | +5.37% | 半導體業 35.29% | 5.88% |
| 2026-04-10 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 4 | 4 | 0 | +5.74% | -4.16% | 金融保險業 75.00% | 25.00% |
| 2026-04-10 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 4 | 4 | 0 | +5.74% | -4.16% | 金融保險業 75.00% | 25.00% |
| 2026-04-10 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 4 | 4 | 0 | +5.74% | -4.16% | 金融保險業 75.00% | 25.00% |
| 2026-04-10 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 4 | 4 | 0 | +5.74% | -4.16% | 金融保險業 75.00% | 25.00% |
| 2026-04-13 | top10_production | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-13 | top15 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-13 | top20 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-13 | top30_no_prune | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | +2.81% | -6.58% | 金融保險業 27.78% | 5.56% |
| 2026-04-14 | top10_production | ALLOW_NEW_POSITIONS | 1 | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | top15 | ALLOW_NEW_POSITIONS | 1 | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | top20 | ALLOW_NEW_POSITIONS | 1 | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-14 | top30_no_prune | ALLOW_NEW_POSITIONS | 1 | 1 | 0 | +11.89% | +4.86% | 光電業 100.00% | 100.00% |
| 2026-04-15 | top10_production | ALLOW_NEW_POSITIONS | 4 | 4 | 0 | +14.84% | +8.94% | 光電業 25.00% | 25.00% |
| 2026-04-15 | top15 | ALLOW_NEW_POSITIONS | 4 | 4 | 0 | +14.84% | +8.94% | 光電業 25.00% | 25.00% |
| 2026-04-15 | top20 | ALLOW_NEW_POSITIONS | 4 | 4 | 0 | +14.84% | +8.94% | 光電業 25.00% | 25.00% |
| 2026-04-15 | top30_no_prune | ALLOW_NEW_POSITIONS | 4 | 4 | 0 | +14.84% | +8.94% | 光電業 25.00% | 25.00% |
| 2026-04-16 | top10_production | ALLOW_NEW_POSITIONS | 0 | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | top15 | ALLOW_NEW_POSITIONS | 0 | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | top20 | ALLOW_NEW_POSITIONS | 0 | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-16 | top30_no_prune | ALLOW_NEW_POSITIONS | 0 | 0 | 0 | +0.00% | -4.79% | None 0.00% | 0.00% |
| 2026-04-17 | top10_production | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -0.47% | -6.10% | 光電業 21.43% | 7.14% |
| 2026-04-17 | top15 | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -0.47% | -6.10% | 光電業 21.43% | 7.14% |
| 2026-04-17 | top20 | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -0.47% | -6.10% | 光電業 21.43% | 7.14% |
| 2026-04-17 | top30_no_prune | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -0.47% | -6.10% | 光電業 21.43% | 7.14% |
| 2026-04-20 | top10_production | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -3.95% | -9.01% | 光電業 16.67% | 5.56% |
| 2026-04-20 | top15 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -3.95% | -9.01% | 光電業 16.67% | 5.56% |
| 2026-04-20 | top20 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -3.95% | -9.01% | 光電業 16.67% | 5.56% |
| 2026-04-20 | top30_no_prune | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -3.95% | -9.01% | 光電業 16.67% | 5.56% |
| 2026-04-21 | top10_production | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -4.00% | -7.45% | 光電業 22.22% | 5.56% |
| 2026-04-21 | top15 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -4.00% | -7.45% | 光電業 22.22% | 5.56% |
| 2026-04-21 | top20 | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -4.00% | -7.45% | 光電業 22.22% | 5.56% |
| 2026-04-21 | top30_no_prune | ALLOW_NEW_POSITIONS | 18 | 18 | 0 | -4.00% | -7.45% | 光電業 22.22% | 5.56% |
| 2026-04-22 | top10_production | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -2.74% | -5.25% | 光電業 21.43% | 7.14% |
| 2026-04-22 | top15 | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -2.74% | -5.25% | 光電業 21.43% | 7.14% |
| 2026-04-22 | top20 | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -2.74% | -5.25% | 光電業 21.43% | 7.14% |
| 2026-04-22 | top30_no_prune | ALLOW_NEW_POSITIONS | 14 | 14 | 0 | -2.74% | -5.25% | 光電業 21.43% | 7.14% |
| 2026-04-23 | top10_production | ALLOW_NEW_POSITIONS | 21 | 21 | 0 | +0.80% | -2.01% | 其他電子業 23.81% | 4.76% |
| 2026-04-23 | top15 | ALLOW_NEW_POSITIONS | 21 | 21 | 0 | +0.80% | -2.01% | 其他電子業 23.81% | 4.76% |
| 2026-04-23 | top20 | ALLOW_NEW_POSITIONS | 21 | 21 | 0 | +0.80% | -2.01% | 其他電子業 23.81% | 4.76% |
| 2026-04-23 | top30_no_prune | ALLOW_NEW_POSITIONS | 21 | 21 | 0 | +0.80% | -2.01% | 其他電子業 23.81% | 4.76% |
| 2026-04-24 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 4 | 0 | -0.11% | +2.23% | 通信網路業 50.00% | 25.00% |
| 2026-04-24 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 6 | 2 | -0.54% | +1.80% | 通信網路業 50.00% | 16.67% |
| 2026-04-24 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 9 | 5 | +1.40% | +3.74% | 其他電子業 44.44% | 11.11% |
| 2026-04-24 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 16 | 12 | +2.08% | +4.42% | 其他電子業 31.25% | 6.25% |
| 2026-04-27 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 5 | 0 | +0.26% | +1.79% | 通信網路業 60.00% | 20.00% |
| 2026-04-27 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 7 | 2 | +0.81% | +2.34% | 半導體業 42.86% | 14.29% |
| 2026-04-27 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 10 | 5 | +1.58% | +3.11% | 通信網路業 40.00% | 10.00% |
| 2026-04-27 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 16 | 11 | +4.50% | +6.03% | 通信網路業 31.25% | 6.25% |
| 2026-04-28 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 5 | 0 | +0.24% | +0.88% | 半導體業 40.00% | 20.00% |
| 2026-04-28 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 9 | 4 | +1.59% | +2.23% | 半導體業 33.33% | 11.11% |
| 2026-04-28 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 11 | 6 | +2.17% | +2.81% | 其他電子業 27.27% | 9.09% |
| 2026-04-28 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 17 | 17 | 12 | +3.98% | +4.61% | 其他電子業 23.53% | 5.88% |
| 2026-04-29 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 19 | 7 | 0 | +1.03% | +2.66% | 通信網路業 42.86% | 14.29% |
| 2026-04-29 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 19 | 9 | 2 | +0.96% | +2.59% | 通信網路業 33.33% | 11.11% |
| 2026-04-29 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 19 | 13 | 6 | +0.97% | +2.60% | 其他電子業 30.77% | 7.69% |
| 2026-04-29 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 19 | 19 | 12 | +0.75% | +2.38% | 其他電子業 26.32% | 5.26% |
| 2026-04-30 | top10_production | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 6 | 0 | +0.00% | - | 半導體業 50.00% | 16.67% |
| 2026-04-30 | top15 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 9 | 3 | +0.00% | - | 其他電子業 33.33% | 11.11% |
| 2026-04-30 | top20 | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 10 | 4 | +0.00% | - | 半導體業 40.00% | 10.00% |
| 2026-04-30 | top30_no_prune | LIMIT_20D_TOP10_AND_BLOCK_T1 | 16 | 16 | 10 | +0.00% | - | 其他電子業 25.00% | 6.25% |

## MARKET_REGIME_CAUTION Blocked Return Diagnostics

These rows are actual inactive `rank_20d` rows whose `tradability_reason` contains `MARKET_REGIME_CAUTION`.

| group | count | partial median | partial mean | partial positive | full 20D rows | full 20D median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL | 70 | +4.74% | +6.23% | 75.00% | 0 | - |

By sector:

| sector | count | partial median | partial positive | full 20D rows |
| --- | ---: | ---: | ---: | ---: |
| 其他電子業 | 19 | +4.72% | 81.25% | 0 |
| 電機機械 | 10 | +4.82% | 87.50% | 0 |
| 半導體業 | 9 | +2.40% | 75.00% | 0 |
| 通信網路業 | 9 | +6.94% | 75.00% | 0 |
| 電子零組件業 | 8 | -1.19% | 42.86% | 0 |
| 光電業 | 7 | +5.96% | 85.71% | 0 |
| 化學工業 | 3 | -1.83% | 50.00% | 0 |
| 塑膠工業 | 2 | +4.53% | 100.00% | 0 |
| 其他業 | 1 | - | - | 0 |
| 電腦及週邊設備業 | 1 | +22.99% | 100.00% | 0 |
| 食品工業 | 1 | -2.80% | 0.00% | 0 |

## Recommendation

Do not change production yet. The audit shows the Top10 CAUTION gate is a major active-breadth throttle, but the 20D ex-post window has not matured for the blocked rows. Treat Top15/Top20/Top30 as research counterfactuals until full 20D outcomes and a fixed-snapshot A/B are available.

## Notes

- `pre_market_pool_count` is the pool that passed non-market gates before CAUTION rank pruning.
- Same-window basket returns use next-trading-day open to mark-date close and 100% equal-weight among selected rows. They are diagnostics, not a ledger replay.
- Full 20D ex-post returns are only populated when 20 trading bars exist after entry. As of mark date 2026-04-30, available full-20D rows = 0; use partial mark-to-date rows as interim diagnostics, not closure evidence.
- A production market-regime change still requires a fixed-snapshot A/B closure artifact before promotion.
