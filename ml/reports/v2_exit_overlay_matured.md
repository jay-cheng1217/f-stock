# V2 Exit Overlay Research Backtest

- Generated: `2026-04-21 22:43:44`
- Branch: `research/v2-exit-overlay`
- Prediction files: `4`
- Prediction date range: `2026-03-11` to `2026-03-18`
- Price data latest date: `2026-04-20`
- Entry: `Open[t+1]`
- Friction: `0.40%` round-trip
- Top N: `30` sector-capped production leaderboard
- Same-day TP/SL ambiguity: `stop_first`
- Include open MTM: `False`

> This is a research-only signal-basket replay. `Calmar*` is `basket cumulative return / |MDD|` for the short sample. Annualized CAGR is kept in CSV, but it is not capital-accurate overlapping-position CAGR.

## Summary

| Overlay | Trades | Signal Days | Basket Cum | MDD | Calmar* | Avg Trade | Win | Avg Hold | TP | SL | Timeout | Open MTM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tp10_no_sl | 120 | 4 | +14.9% | -0.3% | 47.304 | +3.6% | +75.0% | 9.94 | +70.8% | +0.0% | +29.2% | +0.0% |
| tp15_no_sl | 120 | 4 | +19.4% | -0.9% | 21.472 | +4.6% | +66.7% | 13.26 | +58.3% | +0.0% | +41.7% | +0.0% |
| tp20_no_sl | 120 | 4 | +26.2% | -2.6% | 10.247 | +6.1% | +64.2% | 14.78 | +49.2% | +0.0% | +50.8% | +0.0% |
| atr3_tp6 | 120 | 4 | +38.3% | -8.5% | 4.477 | +9.0% | +55.8% | 14.23 | +28.3% | +31.7% | +40.0% | +0.0% |
| time15 | 120 | 4 | +14.8% | -6.3% | 2.360 | +3.7% | +49.2% | 15.00 | +0.0% | +0.0% | +100.0% | +0.0% |
| atr2_tp4 | 120 | 4 | +17.8% | -7.8% | 2.275 | +4.5% | +46.7% | 9.65 | +37.5% | +49.2% | +13.3% | +0.0% |
| sl10_tp20 | 120 | 4 | +4.6% | -8.9% | 0.523 | +1.3% | +40.8% | 7.58 | +37.5% | +55.0% | +7.5% | +0.0% |
| sl07_tp14 | 120 | 4 | -0.5% | -6.8% | -0.080 | -0.1% | +37.5% | 4.73 | +36.7% | +61.7% | +1.7% | +0.0% |
| time10 | 120 | 4 | -7.2% | -16.3% | -0.444 | -1.4% | +41.7% | 10.00 | +0.0% | +0.0% | +100.0% | +0.0% |
| fixed20 | 120 | 4 | +82.5% | +0.0% | - | +16.5% | +63.3% | 20.00 | +0.0% | +0.0% | +100.0% | +0.0% |

## First Read

- Best by Calmar proxy: `tp10_no_sl`.
- Basket cumulative return: `+14.9%`.
- MDD: `-0.3%`.
- Average hold days: `9.94`.
- TP / SL / timeout / open-MTM rates: `+70.8%` / `+0.0%` / `+29.2%` / `+0.0%`.

## Exit Reason Counts

- `atr2_tp4`: `{'gap_stop': 13, 'gap_take_profit': 21, 'stop_loss': 46, 'take_profit': 24, 'timeout': 16}`
- `atr3_tp6`: `{'gap_stop': 10, 'gap_take_profit': 15, 'stop_loss': 28, 'take_profit': 19, 'timeout': 48}`
- `fixed20`: `{'timeout': 120}`
- `sl07_tp14`: `{'gap_stop': 21, 'gap_take_profit': 24, 'stop_loss': 53, 'take_profit': 20, 'timeout': 2}`
- `sl10_tp20`: `{'gap_stop': 16, 'gap_take_profit': 15, 'stop_loss': 50, 'take_profit': 30, 'timeout': 9}`
- `time10`: `{'timeout': 120}`
- `time15`: `{'timeout': 120}`
- `tp10_no_sl`: `{'gap_take_profit': 26, 'take_profit': 59, 'timeout': 35}`
- `tp15_no_sl`: `{'gap_take_profit': 34, 'take_profit': 36, 'timeout': 50}`
- `tp20_no_sl`: `{'gap_take_profit': 22, 'take_profit': 37, 'timeout': 61}`

## Audit Snapshot

- `WARN`: `2` checks
- `PASS`: `9` checks

## Output Files

- Summary CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_summary.csv`
- Signal return CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_signal_returns.csv`
- Trade detail CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_trades.csv`
- Audit MD: `F:\stock\ml\reports\v2_exit_overlay_matured_audit.md`
- Equity curve CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_equity_curve.csv`
- Rank breakdown CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_by_rank.csv`
- Sector breakdown CSV: `F:\stock\ml\reports\v2_exit_overlay_matured_by_sector.csv`
