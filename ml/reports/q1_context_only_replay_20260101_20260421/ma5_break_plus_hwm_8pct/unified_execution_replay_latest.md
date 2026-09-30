# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T03:28:02.901622+00:00
- Signal window: 2026-03-18 to 2026-04-21
- Replay mark window: 2026-03-19 to 2026-04-22
- Exit policy: `ma5_break_plus_hwm_8pct`
- Signal files: 22
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 124 |
| Filled positions | 124 |
| Closed/stopped positions | 93 |
| Open positions | 31 |
| Missed entries | 0 |
| Avg entry slippage | 1.04% |
| Avg filled hold days | 3.34 |
| Capital-weighted hold days | 4.10 |
| Capital turnover vs 20D | 4.88x |
| Avg profit giveback | 3.82% |
| Avg profit giveback ratio | 115.94% |
| Capital-weighted profit giveback | 4.57% |
| Realized avg return | 0.22% |
| Realized median return | -0.71% |
| Realized win rate | 39.78% |
| Realized capital-weighted return | 1.02% |
| MTM capital-weighted return | 4.38% |
| MTM cash return contribution | 34.52% |
| TWII same-window return | 12.43% |
| Realized alpha vs TWII | -11.41% |
| MTM alpha vs TWII | -8.06% |
| Exit-day compounded return | 17.92% |
| Exit-day max drawdown | -10.60% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 89 |
| open | 31 |
| stopped_out | 4 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 124 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 124 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 124 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 50 |
| OPEN | 74 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| MA5_BREAK | 88 |
| NULL | 31 |
| STOP_LOSS_SLIPPAGE | 4 |
| TRAILING_STOP_HWM_8PCT | 1 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-03-25 | 3017 | 20D_only | STOP_LOSS_SLIPPAGE | 7.38% | -10.45% |
| 2026-03-18 | 4768 | 20D_only | STOP_LOSS_SLIPPAGE | 5.46% | -10.45% |
| 2026-03-18 | 8155 | 20D_only | STOP_LOSS_SLIPPAGE | 10.00% | -10.45% |
| 2026-03-19 | 4768 | 20D_only | STOP_LOSS_SLIPPAGE | 3.02% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-03-25 | 3135 | 20D_only | closed | MA5_BREAK | -13.25% |
| 2026-03-25 | 3017 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-18 | 4768 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-18 | 8155 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-19 | 4768 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-17 | 3049 | 20D_only | open |  | -9.37% |
| 2026-03-25 | 2338 | 20D_only | closed | MA5_BREAK | -9.14% |
| 2026-03-19 | 3663 | 20D_only | closed | MA5_BREAK | -8.48% |
| 2026-03-19 | 8299 | 20D_only | closed | MA5_BREAK | -8.29% |
| 2026-04-20 | 6442 | 20D_only | open |  | -7.91% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-03-20 | 6 | -5.48% | -3.13% |
| 2026-03-23 | 7 | -5.92% | -5.58% |
| 2026-03-24 | 15 | 3.11% | 2.52% |
| 2026-03-25 | 6 | 2.15% | 2.23% |
| 2026-03-27 | 6 | -6.22% | -6.22% |
| 2026-03-31 | 6 | -3.80% | -4.00% |
| 2026-04-01 | 5 | 13.69% | 15.56% |
| 2026-04-14 | 2 | -4.11% | -3.66% |
| 2026-04-15 | 9 | -3.14% | -2.83% |
| 2026-04-16 | 5 | 13.92% | 2.18% |
| 2026-04-17 | 3 | 13.00% | 13.53% |
| 2026-04-20 | 2 | 1.21% | 0.05% |
| 2026-04-21 | 9 | 1.36% | -0.86% |
| 2026-04-22 | 12 | 0.07% | -0.02% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
