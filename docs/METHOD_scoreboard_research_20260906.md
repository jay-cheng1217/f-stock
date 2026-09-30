# Method Lock: SCOREBOARD-RESEARCH-20260906

Locked before implementation. Research measurement repair only; no production
model, selection, signal threshold, risk gate or exit-policy change. Preserve
the original five signal equations as explicitly named legacy proxies.

- Baseline: `strategy_scoreboard.py` rejects an entire ticker using its latest
  60-row mean volume, forces both 20D/60D samples to mature for 60D, uses raw
  returns and labels an obsolete MA20/foreign-buy proxy as canonical main.
- Candidate measurements: row-wise historical 20-row mean volume >=500,000;
  next-session close entry, each horizon matures independently at close[e+h];
  factor-adjusted economic returns using `ml.corporate_actions` over (e,e+h];
  report gross and net (default aggregate friction 0.4%, with 0.8% sensitivity).
  No stop, discretionary fill/hold, model, historical operating-margin,
  disposition, ChipK, theme, or portfolio-sizing assumptions are invented.
- New diagnostic `main_pattern` uses the current generator `_pattern_ok` and
  exactly the persisted MA/RSI/MACD/VOL_MA fields consumed by its `_technical`,
  plus its historical 20-row mean-volume requirement. This is only the current
  technical gate, not a replay of either the full discretionary main lane or
  Champion. Missing inputs remain documented unknowns; no threshold tuning.
- Freeze each input file once with SHA-256; both legacy-method and repaired-
  method evidence use that same per-ticker frame. Record calendar and generator
  hashes, max actual data date, horizon-specific latest mature entry date, and
  six-month recent cutoff relative to actual data date (not max mature trade).
- Metrics: completed signals, unique entry dates/tickers, gross/net mean,
  median, win rate, repeat-signal nonoverlap subset by strategy/ticker/horizon,
  yearly cohorts, and explicit open/censored sample counts. No portfolio MDD
  or Sharpe from overlapping signal returns; no automatic lane downgrade.
- Acceptance: future-volume changes cannot alter past eligibility; 20D sample
  survives without 60D history; an ex-dividend fixture produces zero adjusted
  economic loss; exact `_pattern_ok` parity; nonoverlap sample has no
  simultaneous per-ticker holdings; empty/invalid input is not a green report.
- Output: dedicated research directory; do not overwrite monthly production
  artifacts or alter schedules. Final result may show no edge; that is accepted
  evidence and must not trigger optimization until it looks favorable.
