# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-22T15:46:28.154043+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 10 |
| Filled positions | 10 |
| Closed/stopped positions | 5 |
| Open positions | 5 |
| Missed entries | 0 |
| Avg entry slippage | 3.14% |
| Realized avg return | -1.31% |
| Realized median return | -0.56% |
| Realized win rate | 40.00% |
| Realized capital-weighted return | -0.22% |
| MTM capital-weighted return | -0.62% |
| MTM cash return contribution | -1.39% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | -7.17% |
| MTM alpha vs TWII | -7.57% |
| Exit-day compounded return | -8.33% |
| Exit-day max drawdown | -19.81% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 3 |
| open | 5 |
| stopped_out | 2 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 10 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 10 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 10 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 7 |
| OPEN | 3 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 5 |
| STOP_LOSS_SLIPPAGE | 2 |
| TAKE_PROFIT_MA20 | 3 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-13 | 2344 | 20D_only | STOP_LOSS_SLIPPAGE | 3.18% | -10.45% |
| 2026-04-09 | 5351 | 20D_only | STOP_LOSS_SLIPPAGE | 3.58% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-13 | 2344 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-09 | 5351 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-09 | 2485 | 20D_only | open |  | -4.31% |
| 2026-04-10 | 2883 | 20D_only | open |  | -1.65% |
| 2026-04-15 | 2885 | 20D_only | open |  | -0.60% |
| 2026-04-09 | 3665 | 20D_only | closed | TAKE_PROFIT_MA20 | -0.56% |
| 2026-04-13 | 2891 | 20D_only | open |  | 0.38% |
| 2026-04-10 | 8021 | 20D_only | closed | TAKE_PROFIT_MA20 | 0.43% |
| 2026-04-13 | 3138 | 20D_only | open |  | 4.92% |
| 2026-04-09 | 4927 | 20D_only | closed | TAKE_PROFIT_MA20 | 14.47% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-10 | 1 | -0.56% | -0.56% |
| 2026-04-13 | 1 | 0.43% | 0.43% |
| 2026-04-15 | 1 | -10.45% | -10.45% |
| 2026-04-17 | 1 | -10.45% | -10.45% |
| 2026-04-21 | 1 | 14.47% | 14.47% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
