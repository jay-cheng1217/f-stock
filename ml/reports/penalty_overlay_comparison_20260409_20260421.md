# Penalty Overlay Replay Comparison

## Scope

- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Overlay source artifacts: `predictions_overlay_ctx_YYYY-MM-DD.csv`
- Overlay signal artifacts: `unified_signals_overlay_YYYY-MM-DD.csv`
- Overlay replay: `ml/reports/penalty_overlay_replay_20260409_20260421/`
- Tuned overlay replay: `ml/reports/penalty_overlay_tuned_replay_20260409_20260421/`

## Result

| Metric | Baseline | PR90 Classifier | Penalty Overlay | Tuned Overlay |
| --- | ---: | ---: | ---: | ---: |
| Positions | 48 | 10 | 58 | 58 |
| Filled | 48 | 10 | 58 | 58 |
| Realized win rate | 37.50% | 40.00% | 58.33% | 56.00% |
| Avg entry slippage | 2.67% | 3.14% | 2.36% | 2.45% |
| Realized return | 4.43% | -0.22% | 9.83% | 8.41% |
| MTM return | 2.72% | -0.62% | 5.80% | 5.01% |
| TWII same-window return | 6.95% | 6.95% | 6.95% | 6.95% |
| Realized alpha | -2.51% | -7.17% | 2.88% | 1.47% |
| MTM alpha | -4.23% | -7.57% | -1.15% | -1.93% |
| Stop-loss count | 2 | 2 | 2 | 2 |

## Key Reads

- Penalty overlay materially improved the baseline without using the PR90 classifier gate.
- The overlay removed the PR90 stop-loss names `5351` and `2344` from active positions.
- The overlay preserved baseline winners `3529`, `4989`, and `3049`.
- The overlay missed baseline winners `6861` and `5289`; both need follow-up tuning before promotion.
- New overlay stop-loss names were `2485` and `2466`, so the next pass should analyze whether their risk context needs another penalty term.
- The tuned overlay restored `6861` via the foreign-selling dead zone, but overall MTM alpha worsened from -1.15% to -1.93%.
- The tuned overlay added `3665` and `6861`, while removing `3533` and `6133`; the `3533` to `3665` swap carried enough capital to dominate the improvement from `6861`.

## Decision

Penalty overlay is a better near-term path than the PR90 classifier gate. It remains research-only because MTM alpha is still negative versus TWII, but it is a strong improvement over both baseline and PR90 in this same-window replay.

The tuned dead-zone and quality-cap pass is not promoted. It validates the `6861` diagnosis, but it worsens aggregate replay performance. The next tuning pass should isolate the foreign dead zone from the quality cap and inspect high-capital swaps before changing the default overlay rule.

## Version Contract

- `--penalty-overlay-version v1` is the default champion contract.
- `v1` reproduces the original penalty-overlay signal population from this report window.
- `--penalty-overlay-version tuned` is research-only and preserves the foreign dead-zone plus quality-cap experiment.
