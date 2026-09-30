# Sector Cap Validation

## Scope

- Generated at: 2026-05-02T16:53:11.752772+00:00
- Window: 2026-04-23 to 2026-04-29
- Dates: 2026-04-23, 2026-04-24, 2026-04-27, 2026-04-28, 2026-04-29
- Top N: 30
- Sector cap: 20% = max 6 names per sector

## Conclusion

- `apply_sector_cap` output pass: `True`
- Production penalty-overlay cap output pass: `True`
- `unified_signals` rank-20 rows pass: `True`
- Production cap ticker order matches `unified_signals.rank_20d`: `True`
- Post-tradability target weights can exceed 20% after many names are blocked: 2026-04-23, 2026-04-24, 2026-04-27, 2026-04-28, 2026-04-29

Interpretation: the single-day Top30 sector cap is working. The week-one 32.30% communication-services exposure is not a Top30 cap failure; it comes from cross-date accumulation and post-tradability target-weight normalization.

## Daily Summary

| date | raw max | baseline cap max | production cap max | unified rank20 max | active target max | active rows | match unified |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| 2026-04-23 | 其他電子業 7/30 | 其他電子業 6/30 | 通信網路業 6/30 | 通信網路業 6/30 | 其他電子業 23.81% | 21 | True |
| 2026-04-24 | 半導體業 10/30 | 其他電子業 6/30 | 其他電子業 6/30 | 其他電子業 6/30 | 通信網路業 50.00% | 4 | True |
| 2026-04-27 | 半導體業 10/30 | 其他電子業 6/30 | 其他電子業 6/30 | 其他電子業 6/30 | 通信網路業 60.00% | 5 | True |
| 2026-04-28 | 半導體業 9/30 | 其他電子業 6/30 | 其他電子業 6/30 | 其他電子業 6/30 | 半導體業 40.00% | 5 | True |
| 2026-04-29 | 半導體業 8/30 | 其他電子業 6/30 | 其他電子業 6/30 | 其他電子業 6/30 | 通信網路業 42.86% | 7 | True |

## Production Cap Sector Counts

| date | sector | count | count weight | pass |
| --- | --- | ---: | ---: | ---: |
| 2026-04-23 | 通信網路業 | 6 | 20.00% | True |
| 2026-04-23 | 其他電子業 | 6 | 20.00% | True |
| 2026-04-23 | 半導體業 | 4 | 13.33% | True |
| 2026-04-23 | 電子零組件業 | 4 | 13.33% | True |
| 2026-04-23 | 光電業 | 4 | 13.33% | True |
| 2026-04-23 | 電機機械 | 2 | 6.67% | True |
| 2026-04-23 | 塑膠工業 | 2 | 6.67% | True |
| 2026-04-23 | 建材營造業 | 1 | 3.33% | True |
| 2026-04-23 | 化學工業 | 1 | 3.33% | True |
| 2026-04-24 | 其他電子業 | 6 | 20.00% | True |
| 2026-04-24 | 通信網路業 | 6 | 20.00% | True |
| 2026-04-24 | 半導體業 | 6 | 20.00% | True |
| 2026-04-24 | 電子零組件業 | 5 | 16.67% | True |
| 2026-04-24 | 電機機械 | 3 | 10.00% | True |
| 2026-04-24 | 化學工業 | 1 | 3.33% | True |
| 2026-04-24 | 汽車工業 | 1 | 3.33% | True |
| 2026-04-24 | 光電業 | 1 | 3.33% | True |
| 2026-04-24 | 塑膠工業 | 1 | 3.33% | True |
| 2026-04-27 | 其他電子業 | 6 | 20.00% | True |
| 2026-04-27 | 半導體業 | 6 | 20.00% | True |
| 2026-04-27 | 通信網路業 | 6 | 20.00% | True |
| 2026-04-27 | 電子零組件業 | 6 | 20.00% | True |
| 2026-04-27 | 化學工業 | 2 | 6.67% | True |
| 2026-04-27 | 汽車工業 | 1 | 3.33% | True |
| 2026-04-27 | 光電業 | 1 | 3.33% | True |
| 2026-04-27 | 食品工業 | 1 | 3.33% | True |
| 2026-04-27 | 電機機械 | 1 | 3.33% | True |
| 2026-04-28 | 其他電子業 | 6 | 20.00% | True |
| 2026-04-28 | 半導體業 | 6 | 20.00% | True |
| 2026-04-28 | 電子零組件業 | 6 | 20.00% | True |
| 2026-04-28 | 通信網路業 | 5 | 16.67% | True |
| 2026-04-28 | 電機機械 | 2 | 6.67% | True |
| 2026-04-28 | 光電業 | 2 | 6.67% | True |
| 2026-04-28 | 化學工業 | 1 | 3.33% | True |
| 2026-04-28 | 汽車工業 | 1 | 3.33% | True |
| 2026-04-28 | 塑膠工業 | 1 | 3.33% | True |
| 2026-04-29 | 其他電子業 | 6 | 20.00% | True |
| 2026-04-29 | 半導體業 | 6 | 20.00% | True |
| 2026-04-29 | 電子零組件業 | 6 | 20.00% | True |
| 2026-04-29 | 通信網路業 | 3 | 10.00% | True |
| 2026-04-29 | 電機機械 | 3 | 10.00% | True |
| 2026-04-29 | 化學工業 | 2 | 6.67% | True |
| 2026-04-29 | 光電業 | 2 | 6.67% | True |
| 2026-04-29 | 食品工業 | 1 | 3.33% | True |
| 2026-04-29 | 生技醫療業 | 1 | 3.33% | True |

## Notes

- `raw_head30_before_cap` is the un-capped sorted prediction artifact head and is expected to exceed the cap on some days.
- `production_penalty_cap_output` mirrors the current unified-signal path: alpha gate, penalty overlay v1, hard-block removal, then sector cap.
- `unified_active_target` is after tradability and market-regime gates; its target weights are normalized over remaining target units, so it is not the same denominator as the Top30 cap.
- This validation is research-only and does not change production model, sector cap, or entry filters.
