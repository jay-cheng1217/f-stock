# AUDIT-002 Step 4 - Two-Stage Regression + LambdaRank

- Generated at: 2026-05-09 00:52:10
- Stage 1: regression model screens full universe into Top-N candidates.
- Stage 2: LambdaRank reorders only Stage 1 candidates.
- Stage 2 training data uses only Stage 1 OOF candidates before the embargo cutoff.
- Candidate sizes tested: [60, 80]
- Stage 1 OOF artifact: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_stage1_oof_candidates.csv`
- Calibration chart: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_calibration.png`

## Four-Way Comparison

| Model | Stage1/Universe IC | Universe t | Final Top30 IC | Top30 t | NDCG@30 | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Before_baseline | 0.0431 | 2.43 | -0.0351 | -0.70 | - | 4/10 (40%) | 4 | 0.4061 | +0.14% | FAIL |
| After_feature_cleanup | 0.0125 | 0.67 | -0.0116 | -0.29 | - | 5/10 (50%) | 2 | 0.3576 | +0.37% | FAIL |
| LambdaRank | 0.0049 | 0.26 | 0.0689 | 1.66 | 0.463 | 8/10 (80%) | 2 | 0.5273 | +1.02% | FAIL |
| TwoStage_Top60 | 0.0509 | 3.40 | 0.0235 | 0.57 | 0.571 | 7/10 (70%) | 1 | -0.0303 | +2.05% | FAIL |
| TwoStage_Top80 | 0.0509 | 3.40 | 0.0517 | 1.27 | 0.522 | 8/10 (80%) | 1 | 0.4909 | +2.23% | FAIL |

## Artifacts

- Top60 fold artifact: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_top60_fold_top30.csv`
- Top60 calibration deciles: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_top60_calibration_deciles.csv`
- Top80 fold artifact: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_top80_fold_top30.csv`
- Top80 calibration deciles: `F:\stock\ml\reports\audit002_step4_two_stage_20260509_top80_calibration_deciles.csv`

## Verdict

Two-stage does not fully clear AC. Best research candidate is `TwoStage_Top80` with Top30 IC 0.0517, NDCG@30 0.522, and Top30 excess +2.23%. Recommendation: redesign Stage 2 labels/objective or test a wider Stage 1 candidate pool before production work.
