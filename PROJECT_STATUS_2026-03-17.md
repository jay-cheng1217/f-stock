# Project Status 2026-03-17

## Completed in this round

- Unified `ML prediction` and `model analysis` to use the same live prediction context.
- Hid missing-data placeholder reasons from the main factor lists.
- Added richer diagnostics for stocks excluded from ML coverage.
- Lowered the average-volume universe filter from `500,000` to `250,000` shares so names like `5292` can enter live inference.
- Verified the live snapshot now covers `1,068` stocks instead of `839`, and `5292` now returns both ML prediction and explanation output.
- Expanded the prediction snapshot cache key to include model filters and feature-building code fingerprints, so threshold/code changes rebuild the snapshot instead of serving stale results.

## Pending follow-ups

- Restart `start_web.bat` or the process bound to `127.0.0.1:8000` to load the new code.
- Regenerate exported `predictions_*.csv` artifacts if file-based outputs must match the live API after the new universe filter.
- Re-run backtest and ideally retrain the model under the `250,000`-share universe for clean validation on newly included lower-liquidity stocks.
- Split or clean the large unrelated worktree/data changes before release; the repository is still broadly dirty outside this task.

## Notes

- This change improves live inference coverage immediately, but it does not by itself revalidate historical performance under the expanded universe.
