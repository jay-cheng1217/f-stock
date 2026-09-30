# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T00:11:59.198500+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Exit policy: `ma20_plus_hwm_8pct`
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 62 |
| Filled positions | 62 |
| Closed/stopped positions | 28 |
| Open positions | 34 |
| Missed entries | 0 |
| Avg entry slippage | 2.14% |
| Avg filled hold days | 3.68 |
| Capital-weighted hold days | 5.79 |
| Capital turnover vs 20D | 3.46x |
| Avg profit giveback | 2.43% |
| Avg profit giveback ratio | 94.92% |
| Capital-weighted profit giveback | 1.78% |
| Realized avg return | 4.16% |
| Realized median return | 1.18% |
| Realized win rate | 60.71% |
| Realized capital-weighted return | 9.97% |
| MTM capital-weighted return | 6.02% |
| MTM cash return contribution | 14.92% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | 3.02% |
| MTM alpha vs TWII | -0.92% |
| Exit-day compounded return | 74.20% |
| Exit-day max drawdown | -1.48% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 26 |
| open | 34 |
| stopped_out | 2 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 62 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 62 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 62 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 35 |
| OPEN | 27 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 34 |
| STOP_LOSS_SLIPPAGE | 2 |
| TAKE_PROFIT_MA20 | 22 |
| TRAILING_STOP_HWM_8PCT | 4 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-20 | 2466 | 20D_only | STOP_LOSS_SLIPPAGE | 1.40% | -10.45% |
| 2026-04-17 | 3576 | 20D_only | STOP_LOSS_SLIPPAGE | 1.37% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-20 | 2466 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-17 | 3576 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-20 | 3576 | 20D_only | open |  | -8.26% |
| 2026-04-20 | 6442 | 20D_only | open |  | -7.91% |
| 2026-04-17 | 2485 | 20D_only | closed | TRAILING_STOP_HWM_8PCT | -7.50% |
| 2026-04-17 | 2406 | 20D_only | open |  | -6.73% |
| 2026-04-21 | 6226 | 20D_only | open |  | -5.52% |
| 2026-04-13 | 3532 | 20D_only | closed | TAKE_PROFIT_MA20 | -5.42% |
| 2026-04-17 | 1305 | 20D_only | open |  | -5.09% |
| 2026-04-13 | 2103 | 20D_only | open |  | -4.83% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-13 | 1 | 0.43% | 0.43% |
| 2026-04-14 | 3 | 11.33% | 6.18% |
| 2026-04-15 | 1 | 11.75% | 11.75% |
| 2026-04-16 | 2 | -1.48% | -1.48% |
| 2026-04-20 | 4 | 10.59% | 11.26% |
| 2026-04-21 | 8 | 14.57% | 2.65% |
| 2026-04-22 | 9 | 11.69% | 2.51% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
