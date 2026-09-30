# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T00:12:10.423638+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Exit policy: `pure_ma5_break`
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 72 |
| Filled positions | 72 |
| Closed/stopped positions | 39 |
| Open positions | 33 |
| Missed entries | 0 |
| Avg entry slippage | 1.88% |
| Avg filled hold days | 3.01 |
| Capital-weighted hold days | 5.11 |
| Capital turnover vs 20D | 3.92x |
| Avg profit giveback | 2.68% |
| Avg profit giveback ratio | 68.72% |
| Capital-weighted profit giveback | 4.89% |
| Realized avg return | -0.42% |
| Realized median return | -0.34% |
| Realized win rate | 38.46% |
| Realized capital-weighted return | 2.68% |
| MTM capital-weighted return | 11.86% |
| MTM cash return contribution | 31.05% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | -4.27% |
| MTM alpha vs TWII | 4.91% |
| Exit-day compounded return | 15.71% |
| Exit-day max drawdown | -2.90% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 39 |
| open | 33 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 72 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 72 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 72 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 38 |
| OPEN | 34 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| MA5_BREAK | 39 |
| NULL | 33 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a | n/a |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-04-17 | 3049 | 20D_only | open |  | -9.37% |
| 2026-04-20 | 6442 | 20D_only | open |  | -7.91% |
| 2026-04-17 | 2485 | 20D_only | closed | MA5_BREAK | -7.50% |
| 2026-04-13 | 3576 | 20D_only | closed | MA5_BREAK | -7.14% |
| 2026-04-17 | 2406 | 20D_only | open |  | -6.73% |
| 2026-04-17 | 3576 | 20D_only | closed | MA5_BREAK | -6.20% |
| 2026-04-20 | 2466 | 20D_only | closed | MA5_BREAK | -5.67% |
| 2026-04-21 | 1309 | 20D_only | open |  | -5.58% |
| 2026-04-21 | 6226 | 20D_only | open |  | -5.52% |
| 2026-04-21 | 3576 | 20D_only | open |  | -4.73% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-14 | 1 | -0.47% | -0.47% |
| 2026-04-15 | 8 | -2.90% | -2.72% |
| 2026-04-16 | 5 | 13.92% | 2.18% |
| 2026-04-17 | 2 | 2.42% | 2.43% |
| 2026-04-20 | 2 | 1.23% | 0.05% |
| 2026-04-21 | 8 | 1.32% | -1.19% |
| 2026-04-22 | 13 | 0.05% | -0.04% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
