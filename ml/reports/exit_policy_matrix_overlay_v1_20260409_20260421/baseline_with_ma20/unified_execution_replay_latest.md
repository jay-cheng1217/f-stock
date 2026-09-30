# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T00:01:50.950715+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Exit policy: `baseline_with_ma20`
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 58 |
| Filled positions | 58 |
| Closed/stopped positions | 24 |
| Open positions | 34 |
| Missed entries | 0 |
| Avg entry slippage | 2.36% |
| Avg filled hold days | 3.67 |
| Capital-weighted hold days | 5.82 |
| Capital turnover vs 20D | 3.44x |
| Avg profit giveback | 2.10% |
| Avg profit giveback ratio | 97.25% |
| Capital-weighted profit giveback | 1.43% |
| Realized avg return | 4.00% |
| Realized median return | 1.18% |
| Realized win rate | 58.33% |
| Realized capital-weighted return | 9.83% |
| MTM capital-weighted return | 5.80% |
| MTM cash return contribution | 13.75% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | 2.88% |
| MTM alpha vs TWII | -1.15% |
| Exit-day compounded return | 60.03% |
| Exit-day max drawdown | -1.48% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 22 |
| open | 34 |
| stopped_out | 2 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 58 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 58 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 58 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 33 |
| OPEN | 25 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 34 |
| STOP_LOSS_SLIPPAGE | 2 |
| TAKE_PROFIT_MA20 | 22 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-17 | 2485 | 20D_only | STOP_LOSS_SLIPPAGE | -0.51% | -10.45% |
| 2026-04-20 | 2466 | 20D_only | STOP_LOSS_SLIPPAGE | 1.40% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-17 | 2485 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-20 | 2466 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-04-20 | 6442 | 20D_only | open |  | -7.91% |
| 2026-04-17 | 2406 | 20D_only | open |  | -6.73% |
| 2026-04-21 | 6226 | 20D_only | open |  | -5.52% |
| 2026-04-13 | 3532 | 20D_only | closed | TAKE_PROFIT_MA20 | -5.42% |
| 2026-04-17 | 1305 | 20D_only | open |  | -5.09% |
| 2026-04-13 | 2103 | 20D_only | open |  | -4.83% |
| 2026-04-21 | 3450 | 20D_only | closed | TAKE_PROFIT_MA20 | -4.59% |
| 2026-04-13 | 2851 | 20D_only | open |  | -4.43% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-13 | 1 | 0.43% | 0.43% |
| 2026-04-14 | 3 | 11.33% | 6.18% |
| 2026-04-15 | 1 | 11.75% | 11.75% |
| 2026-04-16 | 2 | -1.48% | -1.48% |
| 2026-04-20 | 4 | 10.66% | 11.26% |
| 2026-04-21 | 7 | 14.96% | 4.10% |
| 2026-04-22 | 6 | 2.19% | -0.93% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
