# AUDIT-002 Step 6 Same-Snapshot A/B Validation

- Generated at: 2026-05-09T01:07:46
- Control frame: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_stage1_oof_candidates.csv`
- Treatment frame: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- Months: 2025-11, 2025-12, 2026-01, 2026-02, 2026-03, 2026-04
- Top N: 30

## AC Summary

| AC | Threshold | Value | Pass |
|---|---:|---:|:---:|
| Treatment monthly excess delta | >= +0.30pp | +9.50% | PASS |
| Treatment MDD deterioration | <= +1.00pp | -0.44% | PASS |
| Average Jaccard | >= 0.40 | 0.302 | FAIL |

Overall: **FAIL**

## Control vs Treatment

| Metric | Control | Treatment | Delta |
|---|---:|---:|---:|
| Monthly return | +8.29% | +17.79% | +9.50% |
| Monthly alpha vs TWII | -1.47% | +8.03% | +9.50% |
| MDD | -0.44% | +0.00% | -0.44% deterioration |
| Win rate | +83.33% | +100.00% | +16.67% |

## Monthly Details

| Month | Control Ret | Treatment Ret | Control Alpha | Treatment Alpha | Alpha Delta | Jaccard | Intersect | Union Spearman | Common Spearman |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025-11 | +9.14% | +11.21% | +4.85% | +6.92% | +2.07% | 0.364 | 16/30 | -0.245 | 0.376 |
| 2025-12 | +4.90% | +7.50% | -7.43% | -4.83% | +2.60% | 0.364 | 16/30 | -0.431 | 0.032 |
| 2026-01 | +10.17% | +21.32% | +3.78% | +14.92% | +11.14% | 0.333 | 15/30 | -0.376 | 0.107 |
| 2026-02 | -0.44% | +14.16% | +6.06% | +20.66% | +14.59% | 0.200 | 10/30 | -0.416 | -0.091 |
| 2026-03 | +20.55% | +39.06% | -2.16% | +16.36% | +18.52% | 0.250 | 12/30 | -0.375 | -0.448 |
| 2026-04 | +5.41% | +13.50% | -13.93% | -5.84% | +8.09% | 0.304 | 14/30 | -0.499 | -0.191 |

## Turnover And Cost

- Average Jaccard: 0.302
- Average monthly replacement ratio: +53.89%
- Cost assumption: 30.0 bps one-way
- Estimated one-way monthly cost drag: +0.16%
- Estimated round-trip monthly cost drag: +0.32%
- Jaccard is below AC, so the treatment should be treated as a high-turnover architecture change.

## Artifacts

- Monthly CSV: `F:\stock\ml\reports\audit002_step6_ab_validation_20260509_monthly.csv`
- Control picks CSV: `F:\stock\ml\reports\audit002_step6_ab_validation_20260509_control_top30.csv`
- Treatment picks CSV: `F:\stock\ml\reports\audit002_step6_ab_validation_20260509_treatment_top30.csv`
- Monthly return chart: `F:\stock\ml\reports\audit002_step6_ab_validation_20260509_monthly_returns.png`
