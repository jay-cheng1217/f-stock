# Restart Handoff: 2026-04-23

## Current Branch

- Branch: `research/v2-exit-overlay`
- Current purpose: V2 execution-aware research, alpha classifier validation, PR90 replay, and model attribution.

## Important Pre-Reboot Git State

At handoff start, the worktree already had uncommitted generated special-stock files:

- `disposition_active.csv`
- `ml/data/disposition_periods.csv`
- `ml/reports/special_stock_status_latest.json`

These were intentionally not included in the handoff commit because they are unrelated to the documentation change.

## Latest Research Conclusion

The V2 alpha classifier PR90 gate should **not** be promoted to production.

Same-window replay, `2026-04-09` to `2026-04-21`:

| Metric | Baseline | PR90 Classifier |
| --- | ---: | ---: |
| Positions | 48 | 10 |
| Filled | 48 | 10 |
| Realized win rate | 37.50% | 40.00% |
| MTM return | +2.72% | -0.62% |
| TWII same-window return | +6.95% | +6.95% |
| MTM alpha | -4.23% | -7.57% |
| Avg entry slippage | 2.67% | 3.14% |
| Stop-loss count | 2 | 2 |

Interpretation:

- PR90 slightly improved realized win rate, but not enough.
- PR90 concentrated the portfolio too aggressively.
- PR90 worsened MTM alpha and entry slippage.
- PR90 should remain research-only.

## Attribution Finding

The classifier became an adverse-selection filter. It over-ranked weak high-risk names and missed higher-quality winners.

False positives selected by PR90 and stopped out:

- `5351`
- `2344`

False negatives missed by PR90 but strong in baseline:

- `3529`
- `4989`
- `3049`
- `6861`
- `5289`

Key group deltas:

| Metric | PR90 False Positives | Missed Winners | Read |
| --- | ---: | ---: | --- |
| Alpha percentile | 93.30% | 81.55% | Classifier ranked losers higher |
| Pred return 20D | 3.18% | 6.35% | Regressor preferred winners |
| Price vs MA60 | 1.69% | 19.65% | Missed winners were mid-strength momentum, not overheat |
| Gap pct | 2.27% | -0.08% | False positives had more gap risk |
| Beta 60 | 1.70 | 1.17 | False positives had higher market risk |
| Foreign 20D | -75,666,955 | -3,762,278 | False positives had much heavier foreign selling |
| ROA annualized | -462.01% | +419.98% | False positives were fundamentally much weaker |

## Key Artifacts

- `ml/reports/alpha_pr90_replay_full/unified_execution_replay_latest.md`
- `ml/reports/alpha_pr90_replay_full/unified_execution_replay_positions_latest.csv`
- `ml/reports/baseline_replay_20260409_20260421/unified_execution_replay_latest.md`
- `ml/reports/baseline_replay_20260409_20260421/unified_execution_replay_positions_latest.csv`
- `ml/reports/v2_alpha_classifier_attribution_latest.md`
- `ml/reports/v2_alpha_classifier_attribution_latest.csv`
- `ml/reports/v2_alpha_classifier_attribution_summary_latest.csv`

## Latest Commits Of Interest

- `44eeebfe test(model): add alpha classifier attribution`
- `951c046f test(replay): refresh baseline comparison`
- `58f4c20a test(replay): record alpha pr90 full replay`
- `94183fdf fix(model): backfill liquidity gate context`
- `c9a8c91e feat(model): add csv alpha backfill fallback`
- `100b5b55 feat(model): add alpha prediction backfill`

## Scripts Added Or Changed In This Workstream

- `scripts/backfill_alpha_predictions.py`
  - Backfills `alpha_win_prob_20d`, percentile/rank, liquidity context, and beta into historical prediction artifacts.
  - Has CSV fallback when DuckDB is locked.

- `scripts/v2_alpha_classifier_attribution.py`
  - Builds false-positive and false-negative attribution reports for the alpha classifier.

- `scripts/unified_execution_replay.py`
  - Now accepts research signal filenames when passed explicitly via `--signal-file`.

- `scripts/build_unified_signals.py`
  - Supports alpha win gate modes:
    - `disabled`
    - `absolute`
    - `percentile`
    - `top_n`
  - Supports explicit research prediction filenames such as `predictions_alpha_cls_YYYY-MM-DD.csv`.

- `ml/predict.py`
  - Adds alpha classifier percentile/rank columns when classifier loading is explicitly enabled.
  - Classifier loading remains opt-in through `ENABLE_ALPHA_CLASSIFIER_GATE=1`.

## Operational Notes

- `PID 51868` was holding `stock.duckdb` and listening on web port `8001`.
- Attempts to stop it from this shell failed with `Access is denied`.
- Reboot should clear this lock.
- If DuckDB is free after reboot, backfill can use the faster DuckDB path. If not, CSV fallback works.

## Suggested Next Step After Reboot

Do **not** tune PR90 thresholds first.

Next research direction:

1. Design an execution-aware classifier target.
2. Penalize names with high entry gap/slippage risk.
3. Add explicit quality and foreign-selling constraints to the classifier training frame.
4. Re-test only after the classifier learns to avoid:
   - high beta with foreign selling,
   - weak ROA/operating quality,
   - gap-driven adverse selection.

Useful rerun commands:

```powershell
python scripts/v2_alpha_classifier_attribution.py

$argsList = @()
foreach ($d in @('2026-04-09','2026-04-10','2026-04-13','2026-04-14','2026-04-15','2026-04-16','2026-04-17','2026-04-20','2026-04-21')) {
    $argsList += '--signal-file'
    $argsList += "F:\stock\ml\models\unified_signals_alpha_pr90_$d.csv"
}
$argsList += '--output-dir'
$argsList += 'F:\stock\ml\reports\alpha_pr90_replay_full'
python scripts/unified_execution_replay.py @argsList
```

## Notification

The Codex ntfy notifier is configured and working:

```powershell
python scripts/notify_codex_done.py --summary "message"
```
