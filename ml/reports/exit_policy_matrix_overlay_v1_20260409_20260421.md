# Exit Policy Matrix: Entry Overlay V1, 2026-04-09 to 2026-04-21

## Scope

- Entry population: Penalty Overlay V1 unified signals (`unified_signals_overlay_YYYY-MM-DD.csv`).
- Signal window: 2026-04-09 to 2026-04-21.
- Replay mark window: 2026-04-10 to 2026-04-22.
- Hard stop: retained in every policy.
- Stacking mode: existing MA20 overheat take-profit remains enabled.
- Pure mode: MA20 take-profit is disabled to isolate TIME_20D and HWM behavior.

## Matrix Results

| Mode | Policy | Positions | Closed | Open | Win rate | MTM return | MTM alpha | Hold days | Turnover | MA20 exits | HWM exits | Giveback | Giveback ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Stacking | `baseline_with_ma20` | 58 | 24 | 34 | 58.33% | 5.80% | -1.15% | 5.82 | 3.44x | 22 | 0 | 2.10% | 97.25% |
| Stacking | `ma20_plus_hwm_8pct` | 62 | 28 | 34 | 60.71% | 6.02% | -0.92% | 5.79 | 3.46x | 22 | 4 | 2.43% | 94.92% |
| Stacking | `ma20_plus_hwm_10pct` | 58 | 24 | 34 | 58.33% | 5.80% | -1.15% | 5.82 | 3.44x | 22 | 0 | 2.10% | 97.25% |
| Stacking | `ma20_plus_hwm_12pct` | 58 | 24 | 34 | 58.33% | 5.80% | -1.15% | 5.82 | 3.44x | 22 | 0 | 2.10% | 97.25% |
| Pure | `pure_time_20d_only` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 2.64% | 7.83% |
| Pure | `pure_hwm_8pct` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 2.64% | 7.83% |
| Pure | `pure_hwm_10pct` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 2.64% | 7.83% |
| Pure | `pure_hwm_12pct` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 2.64% | 7.83% |

## Read

- In stacking mode, `ma20_plus_hwm_8pct` is the only HWM variant that changes behavior. It adds 4 HWM exits, increases positions from 58 to 62, raises realized win rate from 58.33% to 60.71%, and improves MTM alpha from -1.15% to -0.92%.
- `ma20_plus_hwm_10pct` and `ma20_plus_hwm_12pct` are identical to `baseline_with_ma20`; their HWM thresholds never beat MA20 take-profit or hard stop to the exit.
- Pure mode is not directly comparable as a portfolio policy in this short window. With MA20 disabled, the first 5 positions consume all capital and remain open through 2026-04-22, so later Entry V1 signals never receive capital.
- Pure mode still matters diagnostically: HWM 8/10/12 never triggers on the first basket, which means the current alpha improvement comes from MA20-driven turnover plus a narrow HWM 8% secondary safety net.

## Conclusion

`baseline_with_ma20` should remain the production-style control. `ma20_plus_hwm_8pct` is the only live contender from this scan, but it should stay research-only until a longer window confirms the 4-exit improvement is stable. The pure runs show that the existing MA20 rule is the turnover engine, not an obvious drag.

## Artifacts

- CSV summary: `ml/reports/exit_policy_matrix_overlay_v1_20260409_20260421.csv`
- Per-policy replay folders: `ml/reports/exit_policy_matrix_overlay_v1_20260409_20260421/<policy>/`
