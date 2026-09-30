# RD-2026-0508-004 Parallel Backtests

Generated: 2026-05-08

## Task A - Exit Policy Full Replay

Artifact: `ml/reports/exit_policy_sensitivity_20260508.md`

Important scope note: saved `predictions_YYYY-MM-DD.csv` artifacts only cover
2026-03-11 to 2026-05-07, so the same-entry replay uses that actual window.
Full 2024-2026 same-snapshot coverage requires regenerated fold artifacts.

| Exit version | Monthly ret | Alpha vs TWII | MDD | Avg hold | MA exits | Stop exits | 20D | MTM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current_MA5 | +0.55% | -1.23% | -22.20% | 3.71 | 740 | 126 | 3 | 87 |
| Variant A MA10 | +0.70% | -1.64% | -25.80% | 4.49 | 638 | 186 | 19 | 113 |
| Variant B StopOnly | +5.71% | -1.22% | -17.34% | 10.48 | 0 | 376 | 294 | 286 |
| Variant C MA5 2-day | +1.46% | -1.75% | -21.72% | 5.64 | 547 | 228 | 22 | 159 |

Read: MA5 clearly shortens the model horizon (3.71-day avg hold vs 10.48 days
without MA exits). StopOnly improves raw return and MDD in this short live
artifact window, but alpha vs TWII is roughly unchanged because the benchmark
was exceptionally strong. This supports more research on exit timing, not an
immediate production change.

## Task B - REQ-009 Regime Retrain

Artifacts:

- Model meta: `ml/models/lgbm_v2_20260508_200128_meta.json`
- Fold Top30: `ml/reports/v2_fold_top30_20260508_200128.csv`
- Production overlap: `ml/reports/req009_regime_fold_overlap_20260508.md`

Key results:

| Metric | Result |
| --- | ---: |
| Dataset rows | 1,651,630 |
| Dataset date range | 2020-01-02 to 2026-04-08 |
| Features | 245 |
| Fold Top30 rows | 840 |
| Avg Top30 excess | +0.14% |
| Avg spread | +3.49% |
| Avg IC | +0.0431 |
| IC > 0 months | 19 / 28 |
| Final production regime importance | 3.24% |
| Final production importance cap pass | yes |

Regime feature columns are present in `dataset_cache_20260508.parquet` and in
the fold artifact. Production-overlap comparison is only possible for 2026-03
and 2026-04 because older production daily prediction artifacts are not saved:

| Month | Date | Fold vs production overlap | Fold excess |
| --- | ---: | ---: | ---: |
| 2026-03 | 2026-03-31 | 3 / 30 | -0.83% |
| 2026-04 | 2026-04-08 | 8 / 30 | +0.93% |

Read: the formal artifact gap is fixed, and the final model keeps regime
importance under 15%. However, several monthly folds exceeded the cap during
walk-forward, and overlap with production daily picks is low in the two months
where comparison is possible. Keep REQ-009 in shadow/research until a cleaner
production decision artifact is defined.

## Task C - REQ-004 Historical Threshold Validation

Artifact: `ml/reports/sector_overheat_threshold_history_20260508.md`

This is an event study over high-risk sectors, not a production signal replay.
Each event uses Open[t+1] to Close[t+20].

| Threshold | Triggers | Tickers | Avg 20D | Median 20D | Positive | Avg alpha | MDD |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 2,618 | 102 | -0.72% | -4.70% | 38.46% | -4.16% | -61.69% |
| 25% | 1,764 | 77 | -0.32% | -4.85% | 39.91% | -4.08% | -66.91% |
| 30% | 1,268 | 62 | -0.25% | -4.70% | 40.22% | -4.46% | -58.54% |

Read: high-risk-sector overheat events historically underperform TWII across
20% / 25% / 30%. This supports the direction of stricter sector thresholds, but
production promotion still needs same-snapshot signal-level A/B because this
event study does not prove how live selection changes.

## Verification

- `python -m py_compile scripts/exit_policy_sensitivity_full.py scripts/sector_overheat_threshold_history_audit.py scripts/regime_fold_overlap_audit.py scripts/train_v2.py`
- `python -m pytest` -> 116 passed
