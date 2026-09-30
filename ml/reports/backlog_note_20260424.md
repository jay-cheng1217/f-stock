# Backlog Note - 2026-04-24

This note keeps only the active items that still need follow-through.

## Completed

- MOPS announcement data wired into `backend/services/news_service.py`
- Stock Inspector shows recent announcements
- `_sentiment_score()` includes a low-weight announcement event factor
- Weekly retrain now stops web/tunnel and waits for DuckDB to become writable
- Prediction tracking is aligned to the trade spec `Open[t+1] -> Close[t+20]`
- Benchmark contract is standardized:
  - Trade / War Room / Replay -> `TWII close-to-close`
  - Legacy classifier backtest -> `Equal-weight universe excess`
- Execution-aware alpha classifier training path is implemented in
  `scripts/train_v2_alpha_classifier.py`
  - `target_mode=execution_adjusted`
  - next-open gap penalty
  - positive-label depoisoning for severe foreign selling / weak quality
  - same-window smoke run completed against a vanilla baseline
- Execution-aware classifier validation artifact
  - Same-snapshot A/B completed for `2026-04-09` and `2026-04-10`
  - Artifact: `ml/reports/v2_alpha_classifier_snapshot_ab_20260424/`
  - `gate_changed = 110`, `old_pass_new_fail = 87`, `old_fail_new_pass = 23`
  - Replay under `Penalty Overlay V1 + ma5_break_plus_hwm_8pct` was unchanged:
    both branches produced the same selected names and the same `MTM alpha = +4.37%`

## Active Backlog

1. T+1 isolated research
   - Keep it separate from production and incident cleanup work
   - Re-check live utility with the new trade-aligned target definition

2. Guardrail counterfactual
   - Re-test disposition / chip-divergence hard blocks as soft gates
   - Check whether a softer overlay improves recall without reopening junk-risk

## Fresh Research Readout

- Execution-aware smoke (`600k` rows, CPU, no-save)
  - Avg AP: `0.3492`
  - Avg AUC: `0.7340`
  - Avg Top30 precision: `37.25%`
  - Avg Top30 adjusted margin: `-0.88%`
  - Avg Top30 raw alpha: `+0.22%`

- Vanilla smoke (`600k` rows, CPU, no-save)
  - Avg AP: `0.3701`
  - Avg AUC: `0.7419`
  - Avg Top30 precision: `36.52%`
  - Avg Top30 adjusted margin: `-0.88%`
  - Avg Top30 raw alpha: `-0.38%`

Takeaway:
- Execution-aware did not win on AP/AUC.
- It did improve Top30 realized raw alpha in walk-forward backtest.
- Fixed-snapshot A/B confirmed that it removes edge on many weaker names,
  but the 2026-04-09 / 2026-04-10 replay names were unchanged under the
  current Entry/Exit contract, so there is still no promotion case.
