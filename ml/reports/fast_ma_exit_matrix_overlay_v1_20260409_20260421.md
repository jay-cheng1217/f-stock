# Fast MA Break Exit Matrix: Entry Overlay V1, 2026-04-09 to 2026-04-21

## Scope

- Entry population: Penalty Overlay V1 unified signals (`unified_signals_overlay_YYYY-MM-DD.csv`).
- Signal window: 2026-04-09 to 2026-04-21.
- Replay mark window: 2026-04-10 to 2026-04-22.
- Every policy keeps the 10% hard stop simulation.
- Fast MA break rule: if a held position's daily close falls below MA5/MA10, queue a next-open exit.
- `ma10_break_plus_hwm_8pct` and `ma5_break_plus_hwm_8pct` disable the legacy MA20 overheat take-profit so the fast MA rule owns attribution.

## Matrix Results

| Mode | Policy | Positions | Closed | Open | Win rate | MTM return | MTM alpha | Hold days | Turnover | MA20 exits | MA10 exits | MA5 exits | HWM exits | Giveback | Giveback ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | `baseline_with_ma20` | 58 | 24 | 34 | 58.33% | 5.80% | -1.15% | 5.82 | 3.44x | 22 | 0 | 0 | 0 | 2.10% | 97.25% |
| Control | `ma20_plus_hwm_8pct` | 62 | 28 | 34 | 60.71% | 6.02% | -0.92% | 5.79 | 3.46x | 22 | 0 | 0 | 4 | 2.43% | 94.92% |
| Fast MA | `ma10_break_plus_hwm_8pct` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 0 | 0 | 2.64% | 7.83% |
| Fast MA | `ma5_break_plus_hwm_8pct` | 72 | 39 | 33 | 38.46% | 11.86% | 4.91% | 5.11 | 3.92x | 0 | 0 | 39 | 0 | 2.68% | 68.72% |
| Pure | `pure_ma10_break` | 5 | 0 | 5 | n/a | 24.89% | 17.94% | 9.00 | 2.22x | 0 | 0 | 0 | 0 | 2.64% | 7.83% |
| Pure | `pure_ma5_break` | 72 | 39 | 33 | 38.46% | 11.86% | 4.91% | 5.11 | 3.92x | 0 | 0 | 39 | 0 | 2.68% | 68.72% |

## Read

- `ma10_break_plus_hwm_8pct` does not trigger in this short window. It behaves like the pure 20D first basket: the initial 5 names consume all capital and remain open.
- `ma5_break_plus_hwm_8pct` is the first exit policy in this stream to flip same-window MTM alpha positive: -1.15% control alpha to +4.91%.
- `ma5_break_plus_hwm_8pct` and `pure_ma5_break` are identical here: 39 MA5 exits, 0 HWM exits, and 33 open positions. That means the alpha move comes from MA5 break itself, not from the HWM safety net.
- Realized win rate drops to 38.46% under MA5, but that is not automatically bearish: the policy cuts many small losers quickly while the larger winners remain open and are counted in MTM rather than realized win rate.
- MA5 increases capital turnover from 3.44x to 3.92x and lowers average entry slippage from 2.36% to 1.88%, which supports the capital-starvation diagnosis.

## Conclusion

`ma5_break_plus_hwm_8pct` becomes the strongest research contender from this matrix. It should not be promoted from one short, strongly bullish window alone, but it is clearly worth the next larger-window replay. `ma10_break_plus_hwm_8pct` is too slow for the current Entry V1 basket.

## Artifacts

- CSV summary: `ml/reports/fast_ma_exit_matrix_overlay_v1_20260409_20260421.csv`
- Per-policy replay folders: `ml/reports/fast_ma_exit_matrix_overlay_v1_20260409_20260421/<policy>/`
