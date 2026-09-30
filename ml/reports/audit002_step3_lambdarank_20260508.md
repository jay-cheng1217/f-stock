# AUDIT-002 Step 3 - LambdaRank Ranking Objective

- Generated at: 2026-05-08 21:38:45
- Objective: LightGBM `lambdarank`, metric `ndcg`, eval_at `30`.
- Label: monthly cross-sectional `trade_excess_return_20d` quantile rank, 5 bins.
- Group: one rebalance month snapshot = all tickers in that month.
- LambdaRank monthly artifact: `F:\stock\ml\reports\audit002_step3_lambdarank_20260508_monthly.csv`
- LambdaRank Top30 artifact: `F:\stock\ml\reports\audit002_step3_lambdarank_20260508_fold_top30.csv`
- Calibration chart: `F:\stock\ml\reports\audit002_step3_lambdarank_20260508_calibration.png`

## Regression / Step2 / LambdaRank Comparison

| Model | Universe IC | Universe t | Top30 IC | Top30 t | NDCG@30 | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Before_baseline | 0.0431 | 2.43 | -0.0351 | -0.70 | - | 4/10 (40%) | 4 | 0.4061 | +0.14% | FAIL |
| After_feature_cleanup | 0.0125 | 0.67 | -0.0116 | -0.29 | - | 5/10 (50%) | 2 | 0.3576 | +0.37% | FAIL |
| LambdaRank | 0.0049 | 0.26 | 0.0689 | 1.66 | 0.463 | 8/10 (80%) | 2 | 0.5273 | +1.02% | FAIL |

## LambdaRank Calibration Deciles

| Decile | N | Score | Actual excess | Actual raw | Positive rate |
|---:|---:|---:|---:|---:|---:|
| 1 | 84 | 0.0294 | +0.22% | +3.28% | 48% |
| 2 | 98 | 0.0429 | -1.82% | +1.39% | 41% |
| 3 | 72 | 0.0544 | -5.40% | +0.56% | 42% |
| 4 | 82 | 0.0877 | +1.54% | +3.19% | 59% |
| 5 | 84 | 0.1345 | +4.59% | +6.92% | 64% |
| 6 | 84 | 0.1657 | +1.88% | +2.70% | 55% |
| 7 | 87 | 0.2177 | +4.44% | +7.24% | 66% |
| 8 | 81 | 0.2807 | +0.43% | +6.56% | 72% |
| 9 | 84 | 0.3979 | +1.03% | +8.47% | 58% |
| 10 | 84 | 0.6564 | +2.69% | +11.24% | 65% |

## Verdict

LambdaRank turns Top30 IC positive but Universe IC is below 0.03. Recommendation: evaluate two-stage architecture: Stage 1 regression screen, Stage 2 LambdaRank reorder.
