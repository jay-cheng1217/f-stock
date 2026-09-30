# V2 Alpha Classifier Fixed-Snapshot A/B + Replay

- Generated: `2026-04-24 20:13:34`
- Dates: `2026-04-09, 2026-04-10`
- Exit policy: `ma5_break_plus_hwm_8pct`
- Snapshot compare CSV: `F:\stock\ml\reports\v2_alpha_classifier_snapshot_ab_20260424\v2_alpha_classifier_snapshot_ab_detail.csv`
- Snapshot sources: `dataset_cache`

## Classifier Bundles

| bundle | target_mode | threshold | trained_at | model_file |
| --- | --- | ---: | --- | --- |
| old | vanilla | 0.52 | 20260424_200927 | lgbm_alpha_cls_v2_vanilla_20260424_200927.txt |
| new | execution_adjusted | 0.52 | 20260424_201140 | lgbm_alpha_cls_v2_execution_adjusted_20260424_201140.txt |

## Same-Snapshot A/B Aggregate

| rows | old pass | new pass | gate changed | old->new fail | new->old pass | avg old edge | avg new edge | avg edge delta |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2185 | 619 | 555 | 110 | 87 | 23 | -0.1403 | -0.1483 | -0.0080 |

## Replay Summary

| branch | positions | filled | win rate | mtm return | twii | mtm alpha | avg slippage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| old (vanilla) | 7 | 7 | 40.00% | 10.85% | 6.48% | 4.37% | 3.57% |
| new (execution_adjusted) | 7 | 7 | 40.00% | 10.85% | 6.48% | 4.37% | 3.57% |

## Daily Snapshot Summary

| date | source | rows | old pass | new pass | top30 old pass | top30 new pass | avg edge loss | fingerprint |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-04-09 | dataset_cache | 1091 | 315 | 281 | 18 | 18 | 0.0065 | 10f2cd22084c |
| 2026-04-10 | dataset_cache | 1094 | 304 | 274 | 15 | 15 | 0.0096 | ef310ebb1063 |

## Readout

- Old branch target mode: `vanilla`
- New branch target mode: `execution_adjusted`
- `prob_edge = alpha_win_prob_20d - alpha_classifier_threshold`.
- Same-snapshot compare uses one shared point-in-time feature frame per date for both branches; cache-backed rows are preferred and DuckDB rebuild is the fallback.
- Replay uses the same 20D base prediction files, `Penalty Overlay V1`, `disable_t1=True`, and the same exit policy for both branches.
