# REQ-034-v3 My Holdings Dry Run (2026-05-17)

## Scope
- Requested as-of date: 2026-05-16
- Email sent: no
- DB mutation: no
- Private cost/share fields: omitted
- Status: `dry_run_only_pm_review_required`

## Bucket Counts
- Red: 6
- Yellow: 0
- Green: 6
- Total holdings: 12

## Coverage
- Red sample present: `True`
- Yellow sample present: `False`
- Green sample present: `True`
- ETF processing result present: `False`
- Prediction-universe fallback present: `False`
- Prediction-universe fallback coverage gap: `True`

## Holdings
| ticker | asset | bucket | signal | review status | daily K | reasons |
|---|---|---|---|---|---|---|
| 2107 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | BELOW_BREAKEVEN, LOW_LIQUIDITY |
| 2317 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 2330 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 2382 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | BELOW_BREAKEVEN |
| 2886 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 3229 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | BELOW_BREAKEVEN |
| 3528 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 5292 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | LOW_LIQUIDITY |
| 8027 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | PREDICTION_EXPLAIN_HARD_GUARD |
| 8039 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 8299 | stock | green | HOLD | STOCK_V2_ALPHA_MODEL_ENABLED | ok | - |
| 8341 | stock | red | EXIT | STOCK_V2_ALPHA_MODEL_ENABLED | ok | STOP_LOSS_10PCT, BELOW_BREAKEVEN, LOW_LIQUIDITY |

## Degraded Data Cases
- None.

## Synthetic Boundary Appendix
- These rows are not real holdings and are not included in bucket counts.
| case | bucket | reason | conditions |
|---|---|---|---|
| yellow_trim_watch_boundary | yellow | Real 12 holdings produced no yellow sample; this validates report rendering only. | OVERHEAT_MA20, NEWS_NEGATIVE_ALERT |
| etf_rule_subset_boundary | yellow | Real 12 holdings include no ETF; this validates ETF coverage expectation only. | ETF_V2_ALPHA_MODEL_UNSUPPORTED, technical_rule_subset |
| prediction_universe_fallback_boundary | yellow | Real 12 holdings all had prediction payloads; this validates fallback rendering only. | MODEL_UNAVAILABLE_REVIEW_REQUIRED |
