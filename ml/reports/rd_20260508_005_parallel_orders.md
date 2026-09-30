# RD-2026-0508-005 Parallel Orders

Generated: 2026-05-08

## W1 - StopOnly Exit Production

Code changes:

- Added `stop_only_20d` to `scripts/exit_policies.py`.
- Changed unified paper-book defaults to `stop_only_20d`.
- Changed nightly Champion default from `ma5_break_plus_hwm_8pct` to `stop_only_20d`.
- Kept legacy MA5/MA10 policies available, but gated MA-break exits behind
  `EXIT_POLICY_MA_BREAK_ENABLED`; default is disabled.
- Updated War Room / shadow / chat / frontend labels so the UI no longer says
  Champion is MA5 by default.

Expected new Champion behavior: `-10% stop OR 20D timeout`, with narrative
decay still available for narrative-tagged positions.

Rollback path: set `UNIFIED_EXIT_POLICY=ma5_break_plus_hwm_8pct` and
`EXIT_POLICY_MA_BREAK_ENABLED=1`.

## W2 - REQ-009 Regime Overlap Detail

Artifacts:

- `ml/reports/req009_regime_overlap_detail_20260508.md`
- `ml/reports/req009_regime_overlap_detail_20260508_detail.csv`
- `ml/reports/req009_regime_overlap_detail_20260508_sector.csv`

| Month | Both | Regime only | Production only |
|---|---:|---:|---:|
| 2026-03 | 3 | 27 | 27 |
| 2026-04 | 8 | 22 | 22 |

Read:

- Regime model and production picks are materially different.
- Regime fold is more aggressive on semiconductor exposure:
  - 2026-03: regime semiconductor 9/30 vs production 6/30.
  - 2026-04: regime semiconductor 13/30 vs production 6/30.
- Production-only names were generally not sector-cap removals from regime;
  they mostly ranked below regime Top30. That means the difference is model
  score ordering, not just cap mechanics.
- Size type is a liquidity proxy bucket from `AMOUNT_MA_20`, because the repo
  does not currently carry true market-cap shares outstanding.

## W3 - REQ-004 Signal-Level Replay

Artifacts:

- `ml/reports/req004_sector_overheat_signal_replay_20260508.md`
- `ml/reports/req004_sector_overheat_signal_replay_20260508_summary.csv`
- `ml/reports/req004_sector_overheat_signal_replay_20260508_selections.csv`
- `ml/reports/req004_sector_overheat_signal_replay_20260508_diffs.csv`

Same monthly model-score snapshot, 2024-01 to 2026-04:

| Threshold | Triggers | Raw Top30 triggers | Monthly ret | Alpha | Median selected | Positive | MDD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 20% | 659 | 24 | +4.35% | +0.20% | +0.44% | +51.19% | -6.43% |
| 25% | 605 | 22 | +4.37% | +0.23% | +0.45% | +51.31% | -6.43% |
| 30% | 567 | 22 | +4.37% | +0.23% | +0.45% | +51.31% | -6.43% |
| 40% | 487 | 20 | +4.36% | +0.22% | +0.45% | +51.31% | -6.43% |

Read:

- Event-level history said overheated high-risk sectors are weak, but
  signal-level basket impact is tiny.
- 20% catches more rows, but it slightly underperforms 25%/30% in the
  regenerated monthly Top30 replay.
- 25% and 30% are effectively tied in selection and performance over this
  replay. This argues against promoting the aggressive 20% version on CL3
  grounds.

## Verification

- Default policy smoke: `_resolve_unified_nightly_config(...).exit_policy == stop_only_20d`
- MA-break flag smoke: `ma_break_exits_enabled() == False`
- `python -m pytest tests/test_narrative_exit_split.py`
- `python -m pytest` -> 118 passed
