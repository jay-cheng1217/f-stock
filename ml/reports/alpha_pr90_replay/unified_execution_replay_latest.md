# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-22T14:32:40.773127+00:00
- Signal window: 2026-04-21 to 2026-04-21
- Replay mark window: 2026-04-21 to 2026-04-21
- Signal files: 1
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 17 |
| Filled positions | 0 |
| Closed/stopped positions | 0 |
| Open positions | 0 |
| Missed entries | 0 |
| Avg entry slippage | n/a |
| Realized avg return | n/a |
| Realized median return | n/a |
| Realized win rate | n/a |
| Realized capital-weighted return | n/a |
| MTM capital-weighted return | n/a |
| MTM cash return contribution | n/a |
| TWII same-window return | n/a |
| Realized alpha vs TWII | n/a |
| MTM alpha vs TWII | n/a |
| Exit-day compounded return | n/a |
| Exit-day max drawdown | n/a |

## Status Counts

| value | count |
| --- | ---: |
| pending | 17 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 17 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 17 |

## Fill Status Counts

| value | count |
| --- | ---: |
| PENDING | 17 |

## Order Type Counts

| value | count |
| --- | ---: |
| NULL | 17 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| NULL | 17 |

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
| n/a | n/a | n/a | n/a | n/a | n/a |

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
