# V2 Exit Overlay Validity Audit

- Generated: `2026-04-28`
- Scope: PRD commit 2, comparison validity only
- Conclusion wording mode for the decision script: `strategy_comparison`

## 200-Word Summary

The current V2 exit overlay watch is a research replay over saved production prediction CSVs, not a strict exit-only overlay applied to the actual V2 champion paper-portfolio positions. `scripts/v2_exit_overlay_watch.py` delegates to `run_backtest`, and the backtest lists `ml/models/predictions_YYYY-MM-DD.csv`, rebuilds a sector-capped Top-N list, then simulates each overlay from `Open[t+1]`. That makes the watch useful for V2-family strategy comparison, but it does not prove entry equivalence against `paper_portfolio_v2_champion.db`.

Version equivalence is also not confirmed. The champion DB stores `unified_*` tables with `rule_version = unified-v2-order-sim:paper:champion`; the watch reads production `predictions_*.csv` files and does not reference `paper_portfolio_v2_champion.db`. Therefore the decision script should not describe the result as an "exit-only overlay A/B against V2 champion." It should use "V2-family overlay strategy comparison" until a later implementation either replays the champion DB positions directly or proves the prediction files are identical to the champion run inputs.

## Validity Fields

- `entry_equivalent`: `partial`
- `version_equivalent`: `no`
- `wording_mode`: `strategy_comparison`

## Evidence

### Watch Is A Thin Research Wrapper

- `scripts/v2_exit_overlay_watch.py:L1-L5` states the runner is research-only and keeps production dashboard/trading pipeline untouched.
- `scripts/v2_exit_overlay_watch.py:L22-L27` imports `run_backtest` from `scripts.v2_exit_overlay_backtest`.
- `scripts/v2_exit_overlay_watch.py:L109-L120` passes CLI options into the backtest, including `top_n`, `max_hold_days`, friction, and open MTM behavior.
- `scripts/v2_exit_overlay_watch.py:L123-L128` calls `run_backtest(...)` and archives the generated outputs.

### Overlay Entry Set Comes From Prediction CSV Replay

- `scripts/v2_exit_overlay_backtest.py:L1-L9` says the replay reuses saved `predictions_YYYY-MM-DD.csv`, rebuilds the same sector-capped Top-N list, and enters at `Open[t+1]`.
- `scripts/v2_exit_overlay_backtest.py:L104-L116` lists files from `MODEL_DIR` matching `predictions_*.csv`.
- `scripts/v2_exit_overlay_backtest.py:L143-L153` reads each prediction CSV and runs `apply_sector_cap(df, top_n=top_n)` before assigning `selection_rank`.
- `scripts/v2_exit_overlay_backtest.py:L731-L760` loops over the replayed Top-N picks and simulates trades for every overlay.
- `ml/predict.py:L1163-L1216` shows `apply_sector_cap` filters to buy recommendations, applies institutional sell-pressure exclusion, and enforces sector caps.

### Champion DB Uses A Different Source Shape

- Local inspection of `paper_portfolio_v2_champion.db` shows tables: `unified_runs`, `unified_positions`, and `unified_marks`; it does not expose the PRD-named `portfolio_positions` / `portfolio_marks` tables.
- The latest champion run rows use `rule_version = unified-v2-order-sim:paper:champion` and `signals_file_path = F:\stock\ml\models\unified_signals_YYYY-MM-DD.csv`.
- The watch metadata in `ml/reports/v2_exit_overlay_watch_latest.json` reports `prediction_start = 2026-03-11`, `prediction_end = 2026-04-27`, `top_n = 30`, and `include_open_mtm = true`, but it does not record a champion DB source or unified signal source.

## Decision

Use `strategy_comparison` wording for v0.1:

> V2-family overlay strategy comparison.

Do not use `exit_only_ab` wording yet:

> exit-only overlay A/B against V2 champion.

To upgrade this audit to `exit_only_ab`, the decision system needs one of two stronger inputs:

1. Read `paper_portfolio_v2_champion.db` directly and apply candidate exits to the actual `unified_positions` / `unified_marks` universe.
2. Add an automated equivalence check proving each watch replay date has the same tickers, ranks, weights, and entry dates as the corresponding champion DB run.
