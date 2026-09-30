# REQ-022 Guardrail Logging Gap Replay

## Scope

- No guardrail logic changed; this fix only normalizes/persists reason strings for blocked rows.
- Historical replay uses `predictions_2026-04-30.csv`, which previously had blocked rows with empty reasons.

## Validation

| Check | Before | After |
|---|---:|---:|
| Legacy predictions guardrail blocked rows | 782 | 782 |
| Missing guardrail reason in legacy predictions | 782 | 0 |
| Replay unified guardrail missing reason | n/a | 0 |
| Replay tradability missing reason | n/a | 0 |
| DIAG-004 rows still showing guardrail_blocked_no_reason | n/a | 0 |

## Normalized Reason Counts (legacy 2026-04-30)

| Reason | Count |
|---|---:|
| NO_BUY_SIGNAL_OR_LOW_EDGE | 706 |
| DISPOSITION_PERIOD | 32 |
| LIQUIDITY_GUARD | 28 |
| CHIP_DIVERGENCE_GUARD | 10 |
| FUNDAMENTAL_GUARD | 6 |

## Artifacts

- JSON: `ml\reports\req022_guardrail_logging_replay_20260510.json`
- Replay CSV: `ml\reports\req022_guardrail_replay_2026-04-30.csv`
- DIAG-004 rerun CSV: `ml\reports\diag004_twii_rally_leadership_20260509.csv`
