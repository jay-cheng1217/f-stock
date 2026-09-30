# Exit Policy Overlay Comparison: 2026-04-09 to 2026-04-21

## Scope

- Entry population: Penalty Overlay V1 unified signals (`unified_signals_overlay_YYYY-MM-DD.csv`).
- Signal window: 2026-04-09 to 2026-04-21.
- Replay mark window: 2026-04-10 to 2026-04-22.
- Control variable: exit policy only.
- Baseline note: the current ledger baseline already includes the existing 10% intraday stop, MA20 overheat take-profit, and TIME_20D behavior.

## Results

| Metric | baseline_20d | trailing_stop_hwm_8pct | Delta |
| --- | ---: | ---: | ---: |
| Positions | 58 | 62 | +4 |
| Filled positions | 58 | 62 | +4 |
| Closed/stopped positions | 24 | 28 | +4 |
| Open positions | 34 | 34 | +0 |
| Realized win rate | 58.33% | 60.71% | +2.38 pp |
| Avg entry slippage | 2.36% | 2.14% | -0.22 pp |
| MTM capital-weighted return | 5.80% | 6.02% | +0.22 pp |
| TWII same-window return | 6.95% | 6.95% | +0.00 pp |
| MTM alpha vs TWII | -1.15% | -0.92% | +0.22 pp |
| Capital-weighted hold days | 5.82 | 5.79 | -0.03 |
| Capital turnover vs 20D | 3.44x | 3.46x | +0.02x |
| Avg profit giveback | 2.10% | 2.43% | +0.33 pp |
| Avg profit giveback ratio | 97.25% | 94.92% | -2.33 pp |
| Capital-weighted profit giveback | 1.43% | 1.78% | +0.34 pp |

## Exit Reason Counts

| Exit reason | baseline_20d | trailing_stop_hwm_8pct |
| --- | ---: | ---: |
| Open / MTM | 34 | 34 |
| STOP_LOSS_SLIPPAGE | 2 | 2 |
| TAKE_PROFIT_MA20 | 22 | 22 |
| TRAILING_STOP_HWM_8PCT | 0 | 4 |

## Read

The first HWM 8% pass is directionally positive: it improves MTM alpha by 22 bps, lifts realized win rate, and demonstrates capital reuse by adding four later-window positions from the same fixed Entry V1 signal stream.

It does not yet prove the giveback thesis cleanly. The average giveback ratio improves, but absolute and capital-weighted giveback worsen slightly because the triggered sample is small and includes gap-sensitive exits. The next sensitivity pass should compare 10% and 12% HWM thresholds against this same report format before promoting an exit overlay default.

## Artifacts

- `ml/reports/exit_policy_baseline_overlay_v1_20260409_20260421/`
- `ml/reports/exit_policy_hwm8_overlay_v1_20260409_20260421/`
