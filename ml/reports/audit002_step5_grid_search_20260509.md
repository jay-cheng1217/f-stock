# AUDIT-002 Step 5 - Candidate Pool x Label Granularity Grid Search

- Generated at: 2026-05-09 01:01:08
- Stage1 OOF source: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_stage1_oof_candidates.csv`
- Heatmap: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_heatmap.png`
- Recommended Config: `N=75, label_bins=5`

## Grid Search

| N | Label bins | Stage1 IC | Top30 IC | NDCG@30 | Max consecutive negative deciles | Top30 excess | AC |
|---:|---:|---:|---:|---:|---:|---:|---|
| 60 | 5 | 0.0509 | 0.0099 | 0.565 | 3 | +1.55% | FAIL |
| 70 | 5 | 0.0509 | 0.0281 | 0.548 | 1 | +2.82% | FAIL |
| 75 | 5 | 0.0509 | 0.0754 | 0.567 | 1 | +3.54% | PASS |
| 80 | 5 | 0.0509 | -0.0132 | 0.526 | 1 | +2.58% | FAIL |
| 100 | 5 | 0.0509 | -0.0826 | 0.514 | 1 | +2.94% | FAIL |
| 60 | 10 | 0.0509 | 0.0203 | 0.516 | 1 | +1.28% | FAIL |
| 70 | 10 | 0.0509 | 0.0412 | 0.523 | 2 | +2.52% | FAIL |
| 75 | 10 | 0.0509 | 0.0336 | 0.488 | 1 | +2.07% | FAIL |
| 80 | 10 | 0.0509 | 0.0016 | 0.451 | 2 | +2.03% | FAIL |
| 100 | 10 | 0.0509 | 0.0414 | 0.417 | 1 | +2.52% | FAIL |

## Verdict

Grid search found a passing config: N=75, label_bins=5. Recommendation: run same-snapshot production A/B before any promotion.
