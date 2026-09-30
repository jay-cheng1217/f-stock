# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T00:02:07.793428+00:00
- Signal window: 2026-04-09 to 2026-04-21
- Replay mark window: 2026-04-10 to 2026-04-22
- Exit policy: `pure_time_20d_only`
- Signal files: 9
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 5 |
| Filled positions | 5 |
| Closed/stopped positions | 0 |
| Open positions | 5 |
| Missed entries | 0 |
| Avg entry slippage | 4.47% |
| Avg filled hold days | 9.00 |
| Capital-weighted hold days | 9.00 |
| Capital turnover vs 20D | 2.22x |
| Avg profit giveback | 2.64% |
| Avg profit giveback ratio | 7.83% |
| Capital-weighted profit giveback | 2.64% |
| Realized avg return | n/a |
| Realized median return | n/a |
| Realized win rate | n/a |
| Realized capital-weighted return | n/a |
| MTM capital-weighted return | 24.89% |
| MTM cash return contribution | 24.89% |
| TWII same-window return | 6.95% |
| Realized alpha vs TWII | n/a |
| MTM alpha vs TWII | 17.94% |
| Exit-day compounded return | n/a |
| Exit-day max drawdown | n/a |

## Status Counts

| value | count |
| --- | ---: |
| open | 5 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 5 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 5 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 5 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 5 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 5 |

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
| 2026-04-09 | 3533 | 20D_only | open |  | 10.42% |
| 2026-04-09 | 4927 | 20D_only | open |  | 12.53% |
| 2026-04-09 | 6510 | 20D_only | open |  | 19.15% |
| 2026-04-09 | 3529 | 20D_only | open |  | 32.52% |
| 2026-04-09 | 4989 | 20D_only | open |  | 49.84% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| n/a | n/a | n/a | n/a |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
