# REQ-010 Two-Stage N=75 CL3 Backtest

- Generated at: `2026-05-09T01:26:45`
- Window: `2024-01` to `2026-04`
- Control: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_stage1_oof_candidates.csv`
- Treatment: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- Config: candidate_size=75, label_bins=5, top_n=30

## CL3

| Check | Value | Threshold | Pass |
|---|---:|---:|:---:|
| alpha_sacrifice | +0.00% | +0.50% | PASS |
| mdd_improvement | +3.66% | +0.10% | PASS |
| sharpe_regression | +0.00% | +5.00% | PASS |
| calmar_regression | +0.00% | +5.00% | PASS |

Overall: **PASS**

## Performance

| Metric | Control | Treatment | Delta |
|---|---:|---:|---:|
| Monthly return | +3.49% | +7.64% | +4.14% |
| Monthly alpha | -0.61% | +3.54% | +4.14% |
| MDD | -9.23% | -5.57% | +3.66% improvement |
| Sharpe | 2.114 | 2.969 | 0.855 |
| Calmar | 5.528 | 25.471 | 19.943 |
| Win rate | +64.29% | +85.71% | +21.43% |

## Turnover And Cost

- Average Jaccard: 0.300
- Average replacement ratio: +54.52%
- Cost assumption: 30.0 bps one-way
- Estimated one-way monthly cost drag: +0.16%
- Estimated round-trip monthly cost drag: +0.33%
- Treatment cost-adjusted monthly alpha: +3.21%
- Cost-adjusted alpha delta vs Control: +3.82%

## Monthly Sample

| Month | Control Ret | Treatment Ret | Control Alpha | Treatment Alpha | Jaccard | Replacement |
|---|---:|---:|---:|---:|---:|---:|
| 2024-01 | +5.40% | +9.20% | -4.86% | -1.07% | 0.304 | +53.33% |
| 2024-02 | -0.51% | +6.20% | -6.73% | -0.02% | 0.429 | +40.00% |
| 2024-03 | +5.08% | +3.57% | +4.58% | +3.07% | 0.395 | +43.33% |
| 2024-04 | +3.11% | +6.63% | -3.10% | +0.42% | 0.304 | +53.33% |
| 2024-05 | +9.20% | +12.00% | +0.30% | +3.10% | 0.304 | +53.33% |
| 2024-06 | +3.62% | +4.69% | +7.13% | +8.20% | 0.176 | +70.00% |
| 2024-07 | -1.07% | +5.54% | -1.85% | +4.76% | 0.200 | +66.67% |
| 2024-08 | -1.45% | -4.52% | -1.26% | -4.33% | 0.304 | +53.33% |
| 2024-09 | +4.78% | +8.06% | +2.28% | +5.56% | 0.364 | +46.67% |
| 2024-10 | -2.55% | -1.41% | -0.27% | +0.87% | 0.395 | +43.33% |
| 2024-11 | +0.83% | +4.08% | -3.73% | -0.47% | 0.200 | +66.67% |
| 2024-12 | +3.56% | +3.87% | +1.64% | +1.95% | 0.364 | +46.67% |
| 2025-01 | +4.21% | +8.74% | +7.48% | +12.01% | 0.364 | +46.67% |
| 2025-02 | -4.19% | -4.08% | +2.10% | +2.21% | 0.395 | +43.33% |
| 2025-03 | -3.37% | -1.55% | -1.15% | +0.68% | 0.250 | +60.00% |
| 2025-04 | -0.66% | +0.96% | -6.15% | -4.54% | 0.304 | +53.33% |
| 2025-05 | -0.93% | +5.54% | -6.70% | -0.24% | 0.250 | +60.00% |
| 2025-06 | -0.38% | +6.15% | -5.58% | +0.96% | 0.154 | +73.33% |
| 2025-07 | +17.16% | +21.99% | +14.21% | +19.04% | 0.364 | +46.67% |
| 2025-08 | +2.27% | +0.69% | -3.29% | -4.87% | 0.200 | +66.67% |
| 2025-09 | +1.98% | +9.21% | -7.37% | -0.13% | 0.132 | +76.67% |
| 2025-10 | +2.03% | +1.55% | +4.18% | +3.70% | 0.429 | +40.00% |
| 2025-11 | +9.14% | +11.21% | +4.85% | +6.92% | 0.364 | +46.67% |
| 2025-12 | +4.90% | +7.50% | -7.43% | -4.83% | 0.364 | +46.67% |
| 2026-01 | +10.17% | +21.32% | +3.78% | +14.92% | 0.333 | +50.00% |
| 2026-02 | -0.44% | +14.16% | +6.06% | +20.66% | 0.200 | +66.67% |
| 2026-03 | +20.55% | +39.06% | -2.16% | +16.36% | 0.250 | +60.00% |
| 2026-04 | +5.41% | +13.50% | -13.93% | -5.84% | 0.304 | +53.33% |

## Artifacts

- Summary CSV: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_summary.csv`
- Monthly CSV: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_monthly.csv`
- Control picks CSV: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_control_top30.csv`
- Treatment picks CSV: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_treatment_top30.csv`
- Monthly return chart: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_monthly_returns.png`
