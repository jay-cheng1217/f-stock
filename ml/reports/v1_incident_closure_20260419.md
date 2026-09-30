# Incident Closure Note: V1 Z-Score Production Drift

- **Date Resolved**: 2026-04-19
- **Incident Period**: 2026-04-08 ~ 2026-04-17
- **Status**: CLOSED

## 1. Description & Impact

Production V1 model experienced a severe collapse in `UP` signals, resulting in a systemic veto of V2 Top30 recommendations. The daily breathing ratio (selected count) plummeted to as low as 0.16 (5/30), effectively paralyzing the trading pipeline's ability to maintain designed Alpha exposure.

## 2. Root Cause Analysis

The incident was classified as a **Feature Processor / Model Contract Mismatch**.

The deployment of model `lgbm_20260409_125845.txt` introduced `cross_sectional_zscore = True` to the feature pipeline. Same-snapshot A/B testing on `2026-04-10` and `2026-04-13` confirmed that the cross-sectional z-score non-linearly penalized absolute momentum.

**Key Evidence:**

- `Old UP -> New DOWN` flip rate: 97.8% (221 / 226).
- Top decile edge loss: the strongest V1 signals suffered an average edge loss of `+0.154`, with a 96.38% flip-to-down rate.
- This forced mean-reversion systematically killed the strongest Alpha candidates during market drawdowns.

## 3. Resolution

- **Immediate Mitigation**: Rolled back production V1 to `lgbm_20260404_131437.txt` (`cross_sectional_zscore = False`).
- **Artifact Marking**: Model `lgbm_20260409_125845.txt` is retained in the repository for autopsy purposes but explicitly tagged as a `REJECTED / INCIDENT MODEL`.

## 4. Prevention (Promotion Gate)

The `v1_snapshot_ab_test.py` script has been promoted to a mandatory pre-release gate. Future V1 model promotions MUST pass the following same-snapshot checks against the incumbent model:

1. `Old UP -> New DOWN` flip rate must not exceed historical tolerances.
2. Top `old_edge` decile average edge loss must not be excessive.
3. Total `new UP` count must not collapse.
4. Top 30 V2 candidates must not face a systemic V1 veto.
5. Signal distribution must remain structurally stable, with no all-DOWN collapse.

## 5. Future Research Tracks

- Cross-sectional z-score is banned for the V1 directional/risk-veto role to preserve absolute strength signals.
- Independent research track opened for evaluating `time-series z-score` (rolling historical self-comparison) to avoid cross-sectional squashing.
- T+1 model evaluation and refactoring are decoupled from this incident and will be tracked in a separate workstream.

## 6. Closure Artifacts

- Same-snapshot A/B markdown: `ml/reports/v1_snapshot_ab_test_latest.md`
- Same-snapshot A/B JSON: `ml/reports/v1_snapshot_ab_test_latest.json`
- Same-snapshot A/B joined detail CSV: `ml/reports/v1_snapshot_ab_test_latest.csv`
- Incident autopsy: `ml/reports/v1_autopsy_latest.md`
