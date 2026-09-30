# Extended Replay: Entry Overlay V1 + Exit Champion

Generated: 2026-04-23

## Scope

- Requested research objective: extend the V2 champion replay toward 2026 Q1.
- Actual usable local signal window: 2026-03-24 to 2026-04-21.
- Replay mark window: 2026-04-10 to 2026-04-22.
- Signal files consumed: 19.
- Entry policy: `penalty_overlay` version `v1`.
- Baseline exit: `baseline_with_ma20`.
- Champion exit: `ma5_break_plus_hwm_8pct`.

This is not yet a full 2026-01-01 to 2026-04-21 quarter replay. Local unified 20D + T+1 prediction artifacts were not available for January and February, and the official context backfill did not complete within the local timeout. Lunar New Year behavior remains untested until those artifacts are regenerated.

## Replay Fix

`scripts/update_unified_portfolio.py` previously enforced `PORTFOLIO_START_DATE = "2026-04-09"` inside the ledger. That made research replays silently ignore earlier signal files even when `unified_execution_replay.py --start-date` was set earlier.

This run adds a research-safe start-date override:

- Production default remains `2026-04-09`.
- `sync_unified_portfolio(..., portfolio_start_date=...)` now controls the accepted ledger start.
- `unified_execution_replay.py --start-date` passes the same date into the temporary replay ledger.
- `scripts/update_unified_portfolio.py --portfolio-start-date` is available for explicit research runs.

## Data Preparation

Lightweight context artifacts were generated for 2026-03-24 through 2026-04-08 to support Entry Overlay V1 scoring. They include the fields needed by the v1 penalty overlay: beta, gap risk, foreign flow, liquidity context, and basic financial quality fields.

Financial context is point-in-time approximated as:

- Before 2026-03-31: 2025Q3.
- From 2026-03-31 onward: 2025Q4.

The generated prediction and unified signal artifacts under `ml/models` are ignored model outputs and are not committed.

## Pre-April Signal Activity

The extended window did include pre-2026-04-09 signal runs after the start-date override. They did not create positions.

| period | signal dates | candidates | new positions | cash deployed |
| --- | ---: | ---: | ---: | ---: |
| 2026-03-24 to 2026-03-31 | 6 | 180 | 0 | 0.00 |
| 2026-04-01 to 2026-04-08 | 4 | 120 | 0 | 0.00 |
| 2026-04-09 to 2026-04-21 | 9 | 206 | 58 baseline / 72 MA5 | 2.3714 / 2.6188 |

Interpretation: the available extension mostly validates that the production gates stayed closed before the active April regime. It does not add new filled trades before 2026-04-09.

## Result Summary

| metric | baseline_with_ma20 | ma5_break_plus_hwm_8pct |
| --- | ---: | ---: |
| Signal runs | 19 | 19 |
| Positions | 58 | 72 |
| Filled positions | 58 | 72 |
| Closed/stopped positions | 24 | 39 |
| Open positions | 34 | 33 |
| Missed entries | 0 | 0 |
| Avg entry slippage | 2.36% | 1.88% |
| Avg filled hold days | 3.67 | 3.01 |
| Capital-weighted hold days | 5.82 | 5.11 |
| Capital turnover vs 20D | 3.44x | 3.92x |
| Realized win rate | 58.33% | 38.46% |
| MTM capital-weighted return | 5.80% | 11.86% |
| TWII same-window return | 6.95% | 6.95% |
| MTM alpha vs TWII | -1.15% | 4.91% |
| Exit-day max drawdown | -1.48% | -2.90% |

Exit reason distribution:

| exit policy | exit reasons |
| --- | --- |
| `baseline_with_ma20` | 22 `TAKE_PROFIT_MA20`, 2 `STOP_LOSS_SLIPPAGE`, 34 open |
| `ma5_break_plus_hwm_8pct` | 39 `MA5_BREAK`, 33 open |

Hold-day metrics are trading-day counts. The ledger increments `days_observed` over future K-line rows, not calendar dates.

## Takeaway

Within the available local window, the V2 champion remains intact:

- The start-date override confirms the replay can now consume pre-2026-04-09 research signals.
- The pre-April available signals generated no positions, so April performance is unchanged.
- `ma5_break_plus_hwm_8pct` still dominates the MA20 baseline on MTM return and alpha, moving from -1.15% alpha to +4.91% alpha.
- HWM remains a safety net in this configuration; the realized early exits are driven by `MA5_BREAK`.

The next real validation step is not another exit tweak. It is regenerating January and February unified prediction/context artifacts so Lunar New Year and early-Q1 behavior can be tested on the same replay path.
