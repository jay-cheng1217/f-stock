# Entry Filter Benchmark Runbook

Purpose: provide a read-only benchmark layer for unified entry-filter decisions before changing any production gate.

The report compares:

- `ml_selected`: rows with `target_units > 0`.
- `ml_blocked_or_none`: candidates present in unified signals but blocked by tradability or downgraded to `NONE`.
- `simple_consensus_strong`: transparent rules using trend, foreign-flow, gap, quality, beta, and `prob_edge`.
- `simple_consensus_watch_or_strong`: broader rule-based sanity basket.
- `ml_selected_but_consensus_reject`: names selected by ML while simple rules reject them.
- `shadow_guard_*`: read-only repair policy simulations applied to ML-selected rows.

Run example:

```powershell
python scripts\entry_filter_benchmark.py --start-date 2026-05-22 --end-date 2026-05-28 --mark-date 2026-05-29
```

Outputs:

- `ml/reports/entry_filter_benchmark_latest.md`
- `ml/reports/entry_filter_benchmark_latest.json`
- `ml/reports/entry_filter_benchmark_latest_candidates.csv`

Interpretation:

- `status=fail` means the selected basket failed at least one sanity check.
- `ml_selected_did_not_beat_blocked_counterfactual` means the selected names underperformed names the gate blocked.
- `ml_selected_did_not_beat_simple_consensus_strong` means selected names underperformed simple transparent rules.
- `ml_selected_win_rate_below_45pct` means selected basket hit rate is too weak for promotion or trust.

Legacy repair guard:

- `shadow_guard_prob_edge_or_strong_consensus`
- Keep ML-selected names only when `prob_edge >= -10%` or `benchmark_votes >= 5`.
- This uses only ex-ante fields. It does not use realized returns, NAV/B4 labels, or post-entry outcomes.
- As of 2026-06-01, the production entry filter is the TQuant reference gate.
  `ENTRY_BENCHMARK_GUARD` remains in `unified_signals` for audit columns, but
  is advisory-only when `TQUANT_REFERENCE_GATE_ENABLED=1`
  (`entry_benchmark_guard_enforced=False`).
- Tunables for historical replay: `ENTRY_BENCHMARK_PROB_EDGE_FLOOR` and
  `ENTRY_BENCHMARK_STRONG_VOTE_MIN`.

Scope lock:

- This script is offline/read-only.
- It does not mutate model files, paper portfolio DBs, scheduler tasks, or production gate logic.
- Use it as PM/SA evidence before promoting any entry-filter change.
