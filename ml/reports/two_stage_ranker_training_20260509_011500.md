# REQ-011 Phase A Two-Stage Ranker Training

- Generated at: 2026-05-09T01:15:00
- Stage 1 model: `F:\stock\ml\models\lgbm_v2_20260508_200128.txt`
- Stage 2 model: `F:\stock\ml\models\lgbm_two_stage_ranker_20260509_011500.txt`
- Stage 2 meta: `F:\stock\ml\models\lgbm_two_stage_ranker_20260509_011500_meta.json`
- Candidate artifact: `F:\stock\ml\reports\two_stage_ranker_train_candidates_20260509_011500.csv`
- Feature importance: `F:\stock\ml\reports\two_stage_ranker_feature_importance_20260509_011500.csv`

## Configuration

| Field | Value |
|---|---:|
| candidate_size | 75 |
| label_bins | 5 |
| top_n | 30 |
| objective | lambdarank |
| metric | ndcg |
| device | cpu |

## Training Data

| Field | Value |
|---|---:|
| source rows | 1,654,918 |
| stock universe | 1,114 |
| stage2 feature columns | 245 |

## Smoke Test

`ml.two_stage_model.TwoStageModel.predict()` was run on the latest monthly
training frame using the newly trained ranker and precomputed Stage 1 scores.

| Check | Result |
|---|---:|
| smoke month | 2026-04 |
| returned rows | 30 |
| top ticker | 6531 |
| top score | 2.4752237674 |

## Notes

- The model binary is intentionally ignored by git under the repository's
  trained-model artifact policy; it remains available on the local machine.
- The committed meta JSON points to the local model binary path.
- `TWO_STAGE_RANKER_ENABLED` remains off by default, so nightly Champion behavior
  does not change until Phase B/C explicitly enables it.
