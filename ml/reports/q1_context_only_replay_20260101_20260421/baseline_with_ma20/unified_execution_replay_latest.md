# Unified Execution Replay Latest

This is an execution-adjusted replay built from existing unified signal artifacts.
It uses a temporary ledger and does not mutate production portfolio state.

## Scope

- Generated at: 2026-04-23T03:28:03.838152+00:00
- Signal window: 2026-03-18 to 2026-04-21
- Replay mark window: 2026-03-19 to 2026-04-22
- Exit policy: `baseline_with_ma20`
- Signal files: 22
- Temp database: deleted after replay

## Key Metrics

| metric | value |
| --- | ---: |
| Positions | 114 |
| Filled positions | 114 |
| Closed/stopped positions | 76 |
| Open positions | 38 |
| Missed entries | 0 |
| Avg entry slippage | 1.27% |
| Avg filled hold days | 6.36 |
| Capital-weighted hold days | 8.25 |
| Capital turnover vs 20D | 2.42x |
| Avg profit giveback | 3.00% |
| Avg profit giveback ratio | 107.07% |
| Capital-weighted profit giveback | 3.58% |
| Realized avg return | 3.22% |
| Realized median return | 0.39% |
| Realized win rate | 52.63% |
| Realized capital-weighted return | 1.13% |
| MTM capital-weighted return | 2.53% |
| MTM cash return contribution | 14.42% |
| TWII same-window return | 12.43% |
| Realized alpha vs TWII | -11.30% |
| MTM alpha vs TWII | -9.91% |
| Exit-day compounded return | 69.81% |
| Exit-day max drawdown | -37.57% |

## Status Counts

| value | count |
| --- | ---: |
| closed | 53 |
| open | 38 |
| stopped_out | 23 |

## Entry Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 114 |

## Current Signal Counts

| value | count |
| --- | ---: |
| 20D_only | 114 |

## Fill Status Counts

| value | count |
| --- | ---: |
| FILLED | 114 |

## Order Type Counts

| value | count |
| --- | ---: |
| INTRADAY_CHASE | 47 |
| OPEN | 67 |

## Exit Reason Counts

| value | count |
| --- | ---: |
| GAP_STOP_LOSS | 1 |
| NULL | 38 |
| STOP_LOSS_SLIPPAGE | 22 |
| TAKE_PROFIT_MA20 | 45 |
| TIME_20D | 8 |

## Stopped Positions

| prediction_date | ticker | entry_signal_type | exit_reason | entry_slippage_pct | realized_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-03-25 | 3135 | 20D_only | GAP_STOP_LOSS | -1.19% | -13.25% |
| 2026-03-18 | 3324 | 20D_only | STOP_LOSS_SLIPPAGE | -1.46% | -10.45% |
| 2026-03-18 | 8299 | 20D_only | STOP_LOSS_SLIPPAGE | -2.03% | -10.45% |
| 2026-03-18 | 4772 | 20D_only | STOP_LOSS_SLIPPAGE | -0.31% | -10.45% |
| 2026-03-20 | 6510 | 20D_only | STOP_LOSS_SLIPPAGE | -2.92% | -10.45% |
| 2026-03-20 | 8299 | 20D_only | STOP_LOSS_SLIPPAGE | -4.70% | -10.45% |
| 2026-03-19 | 6510 | 20D_only | STOP_LOSS_SLIPPAGE | 0.28% | -10.45% |
| 2026-03-19 | 3663 | 20D_only | STOP_LOSS_SLIPPAGE | -0.27% | -10.45% |
| 2026-03-25 | 3663 | 20D_only | STOP_LOSS_SLIPPAGE | -1.35% | -10.45% |
| 2026-03-25 | 4934 | 20D_only | STOP_LOSS_SLIPPAGE | 1.93% | -10.45% |

## Missed Entries

| prediction_date | ticker | entry_signal_type | order_type | exit_reason |
| --- | --- | --- | --- | --- |
| n/a | n/a | n/a | n/a | n/a |

## Worst Effective Returns

| prediction_date | ticker | entry_signal_type | status | exit_reason | effective_return_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-03-25 | 3135 | 20D_only | stopped_out | GAP_STOP_LOSS | -13.25% |
| 2026-03-18 | 3324 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-19 | 3663 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-18 | 4772 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-19 | 6510 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-20 | 8299 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-20 | 6510 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-18 | 8299 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-25 | 3663 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |
| 2026-03-25 | 4934 | 20D_only | stopped_out | STOP_LOSS_SLIPPAGE | -10.45% |

## Exit-Day Curve

| exit_date | position_count | weighted_return_pct | avg_return_pct |
| --- | --- | --- | --- |
| 2026-03-19 | 4 | 0.14% | 0.14% |
| 2026-03-20 | 3 | -0.04% | 5.35% |
| 2026-03-23 | 3 | -4.87% | -5.46% |
| 2026-03-24 | 5 | -10.45% | -10.45% |
| 2026-03-26 | 3 | 10.00% | 12.68% |
| 2026-03-27 | 3 | -7.18% | -1.34% |
| 2026-03-30 | 2 | -10.45% | -10.45% |
| 2026-03-31 | 3 | -10.45% | -10.45% |
| 2026-04-02 | 3 | -10.45% | -10.45% |
| 2026-04-07 | 3 | 8.96% | 11.28% |
| 2026-04-08 | 1 | 18.92% | 18.92% |
| 2026-04-09 | 2 | 6.34% | 6.34% |
| 2026-04-13 | 3 | 14.97% | 17.09% |
| 2026-04-14 | 5 | 14.08% | 10.74% |
| 2026-04-15 | 2 | 7.29% | 6.92% |
| 2026-04-16 | 2 | -1.48% | -1.48% |
| 2026-04-17 | 4 | -0.92% | -0.92% |
| 2026-04-20 | 5 | 10.52% | 11.02% |
| 2026-04-21 | 14 | 10.30% | 7.17% |
| 2026-04-22 | 6 | 17.72% | 2.17% |

## Caveats

- This replay is based on the unified signal artifacts currently on disk.
- Older artifacts can contain signal mixes produced before later production gates.
- Open 20D positions are marked to the latest available local K data.
- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.
- TWII comparison is close-to-close over the replay mark window.
