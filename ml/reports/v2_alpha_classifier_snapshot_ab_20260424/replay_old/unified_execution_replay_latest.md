# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-24T12:13:34.225721+00:00
- Signal window: 2026-04-09 to 2026-04-10
- Replay mark window: 2026-04-10 to 2026-04-23
- Exit policy: `ma5_break_plus_hwm_8pct`
- Signal files: 2
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Benchmark | TWII close-to-close |
| Positions | 7 |
| Filled positions | 7 |
| Closed/stopped positions | 5 |
| Open positions | 2 |
| Missed entries | 0 |
| Avg entry slippage | 3.57% |
| Avg filled hold days | 6.57 |
| Capital-weighted hold days | 6.40 |
| Capital turnover vs 20D | 3.12x |
| Avg profit giveback | 2.79% |
| Avg profit giveback ratio | 109.35% |
| Capital-weighted profit giveback | 2.27% |
| Realized avg return | 5.73% |
| Realized median return | -1.56% |
| Realized win rate | 40.00% |
| Realized capital-weighted return | 4.16% |
| MTM capital-weighted return | 10.85% |
| MTM cash return contribution | 18.09% |
| TWII same-window return | 6.48% |
| Realized alpha vs TWII | -2.32% |
| MTM alpha vs TWII | 4.37% |
| Exit-day compounded return | 30.04% |
| Exit-day max drawdown | -2.97% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 5 |
| open | 2 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 7 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 7 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 7 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 5 |
| OPEN | 2 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| MA5_BREAK | 5 |
| NULL | 2 |

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
| 2026-04-09 | 3533 | 20D_only | closed | MA5_BREAK | -4.37% |
| 2026-04-09 | 6568 | 20D_only | closed | MA5_BREAK | -2.91% |
| 2026-04-09 | 6510 | 20D_only | closed | MA5_BREAK | -1.56% |
| 2026-04-10 | 2890 | 20D_only | closed | MA5_BREAK | 1.54% |
| 2026-04-09 | 3665 | 20D_only | open |  | 24.52% |
| 2026-04-09 | 3529 | 20D_only | closed | MA5_BREAK | 35.95% |
| 2026-04-09 | 8021 | 20D_only | open |  | 50.76% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-04-13 | 1 | -2.91% | -2.91% |
| 2026-04-15 | 2 | -2.97% | -2.97% |
| 2026-04-20 | 1 | 1.54% | 1.54% |
| 2026-04-23 | 1 | 35.95% | 35.95% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
