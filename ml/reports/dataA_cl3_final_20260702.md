# REQ-032 Two-Stage Model Retrain CL3

- Generated at: `2026-07-02T14:26:29`
- Data cutoff: daily K `2026-07-02`, target dataset `['2020-01-02', '2026-06-03']`
- Old Stage1 meta: `F:\stock\ml\models\lgbm_v2_20260508_200128_meta.json`
- New Stage1 meta: `ml\models\lgbm_v2_20260702_131745_meta.json`
- New Stage2 meta: `ml\models\lgbm_two_stage_ranker_20260702_141205_meta.json`
- Old fold: `F:\stock\ml\reports\audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv`
- New fold: `ml\reports\req032_step4_two_stage_dataA_20260702_top75_fold_top30.csv`

## Stage1 Holdout Score Distribution

| Model | Trained At | N | Mean | Median | P25 | P75 | AC |
|---|---:|---:|---:|---:|---:|---:|---|
| old_stage1 | 20260508_200128 | 3627 | -0.0544 | -0.0568 | -0.0840 | -0.0291 | FAIL |
| new_stage1 | 20260702_131745 | 3627 | -0.0174 | -0.0218 | -0.0520 | 0.0056 | FAIL |

- Holdout months: `2026-04, 2026-05, 2026-06`
- New Stage1 median threshold: `0.055`

## Stage2 Top10 Holdout

- Months: `2026-04, 2026-05, 2026-06`
- Rows: `10`
- Top10 win rate: `+50.00%` (threshold `+40.00%`)
- Top10 average return: `+1.24%`
- Top10 average alpha: `-13.68%`

## CL3

| Check | Value | Threshold | Pass |
|---|---:|---:|:---:|
| alpha_sacrifice | +1.64% | +0.50% | FAIL |
| mdd_not_worse | -3.71% | +0.00% | FAIL |
| sharpe_regression | +14.70% | +5.00% | FAIL |
| calmar_regression | +58.72% | +5.00% | FAIL |

Overall: **FAIL**

## Performance

| Metric | Old Production Fold | New Retrain Fold | Delta |
|---|---:|---:|---:|
| Monthly return | +7.64% | +5.84% | -1.80% |
| Monthly alpha | +3.54% | +1.90% | -1.64% |
| MDD | -5.57% | -9.28% | -3.71% (positive = improved) |
| Sharpe | 2.969 | 2.533 | -0.436 |
| Calmar | 25.471 | 10.514 | -14.957 |
| Positive months | +85.71% | +75.00% | -10.71% |

## Repeat Diagnostics

| Model | Rows | Unique tickers | Repeat rows | Repeat ratio | Top repeat tickers |
|---|---:|---:|---:|---:|---|
| old | 840 | 446 | 394 | +46.90% | `{"6919": 10, "5314": 9, "6442": 8, "7751": 8, "1591": 7, "3066": 7, "7721": 7, "2404": 7, "7728": 7, "3555": 6}` |
| new | 840 | 460 | 380 | +45.24% | `{"5314": 9, "6498": 9, "8935": 8, "6739": 8, "7721": 7, "3555": 7, "6919": 7, "1519": 6, "7751": 6, "8932": 6}` |

## Verdict

REQ-032 does **not** clear promotion gates: Stage1 holdout median score < 0.055; CL3 failed. Keep production pinned to the current Champion models.

## Artifacts

- Stage1 score distribution CSV: `F:\stock\ml\reports\dataA_cl3_final_20260702_stage1_score_distribution.csv`
- Monthly comparison CSV: `F:\stock\ml\reports\dataA_cl3_final_20260702_monthly.csv`
- JSON: `F:\stock\ml\reports\dataA_cl3_final_20260702.json`
