# REQ-004 Sector OVERHEAT Signal-Level Replay

- Window: 2024-01-01 to 2026-04-01
- Same monthly model-score snapshot for all threshold variants.
- `40%` is included as the current/default context baseline.

| Threshold | Triggers | Raw Top30 triggers | Monthly ret | Alpha | Median selected | Positive | MDD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 20% | 659 | 24 | +4.35% | +0.20% | +0.44% | +51.19% | -6.43% |
| 25% | 605 | 22 | +4.37% | +0.23% | +0.45% | +51.31% | -6.43% |
| 30% | 567 | 22 | +4.37% | +0.23% | +0.45% | +51.31% | -6.43% |
| 40% | 487 | 20 | +4.36% | +0.22% | +0.45% | +51.31% | -6.43% |

## Notes

- This is signal-level A/B on regenerated walk-forward folds, not a live artifact replay.
- Thresholds apply only to high-risk sectors: plastics/petrochemical, shipping, steel/metal.
- Selection diffs versus the 40% context baseline are in the `_diffs.csv` artifact.
