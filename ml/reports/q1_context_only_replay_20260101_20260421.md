# Q1 Context-Only Replay: Entry Overlay V1 + Exit Champion

Generated: 2026-04-23

## Scope

- Requested window: 2026-01-01 to 2026-04-21.
- Backfill mode: `context-only`.
- Entry policy: 20D Regressor + Penalty Overlay V1.
- T+1 dependency: disabled for research (`--disable-t1`).
- Unified signal prefix: `unified_signals_overlay_q1`.
- Replay policies:
  - `baseline_with_ma20`
  - `ma5_break_plus_hwm_8pct`

This run is the first successful resumable Q1 batch attempt, but it is not a full Lunar New Year stress test. The local repository still lacks January and February base `predictions_YYYY-MM-DD.csv` artifacts, so those dates can only be recorded as `missing_source` in the manifest.

## Backfill Coverage

| item | count |
| --- | ---: |
| Requested business dates | 79 |
| Missing source prediction dates | 54 |
| Enriched prediction artifacts written | 25 |
| Unified signal artifacts written | 22 |
| Legacy prediction schemas skipped for unified build | 3 |

The 3 skipped unified builds are 2026-03-11, 2026-03-13, and 2026-03-17. Those source files are legacy probability-only or pre-V2 schemas and do not contain `pred_return_20d`, so they cannot honestly run the V2 Entry Overlay V1 contract.

The actual replayable signal window is therefore 2026-03-18 to 2026-04-21.

## Replay Summary

| metric | baseline_with_ma20 | ma5_break_plus_hwm_8pct |
| --- | ---: | ---: |
| Signal files | 22 | 22 |
| Positions | 114 | 124 |
| Filled positions | 114 | 124 |
| Closed/stopped positions | 76 | 93 |
| Open positions | 38 | 31 |
| Missed entries | 0 | 0 |
| Avg entry slippage | 1.27% | 1.04% |
| Avg filled hold days | 6.36 | 3.34 |
| Capital-weighted hold days | 8.25 | 4.10 |
| Capital turnover vs 20D | 2.42x | 4.88x |
| Realized win rate | 52.63% | 39.78% |
| MTM capital-weighted return | 2.53% | 4.38% |
| TWII same-window return | 12.43% | 12.43% |
| MTM alpha vs TWII | -9.91% | -8.06% |
| Exit-day max drawdown | -37.57% | -10.60% |

## Exit Attribution

| policy | exit reasons |
| --- | --- |
| `baseline_with_ma20` | 45 `TAKE_PROFIT_MA20`, 22 `STOP_LOSS_SLIPPAGE`, 1 `GAP_STOP_LOSS`, 8 `TIME_20D`, 38 open |
| `ma5_break_plus_hwm_8pct` | 88 `MA5_BREAK`, 4 `STOP_LOSS_SLIPPAGE`, 1 `TRAILING_STOP_HWM_8PCT`, 31 open |

## Monthly Breakdown

| policy | month | positions | closed | open | weighted MTM |
| --- | --- | ---: | ---: | ---: | ---: |
| `baseline_with_ma20` | 2026-03 | 52 | 46 | 6 | 1.57% |
| `baseline_with_ma20` | 2026-04 | 62 | 30 | 32 | 8.13% |
| `ma5_break_plus_hwm_8pct` | 2026-03 | 52 | 52 | 0 | 0.52% |
| `ma5_break_plus_hwm_8pct` | 2026-04 | 72 | 41 | 31 | 11.06% |

## Interpretation

The MA5 exit engine still improves over the MA20 baseline:

- MTM return improves from 2.53% to 4.38%.
- MTM alpha improves by 185 bps, from -9.91% to -8.06%.
- Capital turnover improves from 2.42x to 4.88x.
- Exit-day max drawdown improves materially, from -37.57% to -10.60%.

But the broader conclusion is defensive, not triumphant. Once the replay starts on 2026-03-18 instead of 2026-04-09, both policies lag the TWII rebound badly. The MA5 engine helps control damage and recycle capital, but it does not fully solve the earlier March entry population.

The current blocker for the original Q1 question remains the base prediction layer. To test Lunar New Year honestly, the missing January and February `predictions_YYYY-MM-DD.csv` artifacts must be regenerated from the 20D Regressor first; context-only overlay backfill cannot invent missing model predictions.
