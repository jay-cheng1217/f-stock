# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-22T15:47:21.664979+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 48 |
| Filled positions | 48 |
| Closed/stopped positions | 16 |
| Open positions | 32 |
| Missed entries | 0 |
| Avg entry slippage | 2.67% |
| Realized avg return | 2.19% |
| Realized median return | -0.31% |
| Realized win rate | 37.50% |
| Realized capital-weighted return | 4.43% |
| MTM capital-weighted return | 2.72% |
| MTM cash return contribution | 6.32% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | -2.51% |
| MTM alpha vs TWII | -4.23% |
| Exit-day compounded return | 36.03% |
| Exit-day max drawdown | -11.78% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 14 |
| open | 32 |
| stopped_out | 2 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 48 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 48 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 48 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 31 |
| OPEN | 17 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 32 |
| STOP_LOSS_SLIPPAGE | 2 |
| TAKE_PROFIT_MA20 | 14 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-17 | 2485 | 20D_only | STOP_LOSS_SLIPPAGE | -0.51% | -10.45% |
| 2026-04-09 | 5351 | 20D_only | STOP_LOSS_SLIPPAGE | 3.58% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-17 | 2485 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-09 | 5351 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-20 | 6442 | 20D_only | open |  | -7.91% |
| 2026-04-17 | 2406 | 20D_only | open |  | -6.73% |
| 2026-04-13 | 3372 | 20D_only | open |  | -5.88% |
| 2026-04-21 | 6226 | 20D_only | open |  | -5.52% |
| 2026-04-13 | 3532 | 20D_only | closed | TAKE_PROFIT_MA20 | -5.42% |
| 2026-04-17 | 1305 | 20D_only | open |  | -5.09% |
| 2026-04-13 | 2103 | 20D_only | open |  | -4.83% |
| 2026-04-17 | 1309 | 20D_only | open |  | -4.62% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-10 | 1 | -0.56% | -0.56% |
| 2026-04-13 | 1 | 0.43% | 0.43% |
| 2026-04-14 | 3 | 11.33% | 6.18% |
| 2026-04-15 | 1 | -10.45% | -10.45% |
| 2026-04-16 | 2 | -1.48% | -1.48% |
| 2026-04-20 | 1 | 26.33% | 26.33% |
| 2026-04-21 | 5 | 13.84% | 2.16% |
| 2026-04-22 | 2 | -3.57% | -3.57% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
