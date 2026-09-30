# Method lock: rere verification integrity (2026-09-06)

Written before implementation. User authorized repairs and backtest verification while preserving rere strategy. No model training, model selection, strategy thresholds, live ledger migration, or capital allocation is authorized by this experiment.

## Frozen strategy and boundaries

- Production predicates remain `generate_entry_candidates._rere_shakeout_ok` and `_rere_ignition_ok`, including high10 / close - 1 >= 0.10, MA20 band +/- 0.05 for shakeout, volume ratio < 1.2 / >= 1.5, MA60, foreign turn-buy and ignition trust-buy conditions. Twenty-session average volume >= 500,000 shares remains the universe condition.
- Entry is next trading close; stop is signal MA60 * 0.97, evaluated from the session after entry; expiry is entry + 60 observed trading sessions. Existing additive cash/rights adjustment is held fixed to isolate this repair; it is not a claim that this convention models every corporate action perfectly.
- Research historical signals are conditional pattern samples. They do not reconstruct historical model universes, sector priority, top-six selection, special-stock gates, manual ChipK evidence, intraday fills, discretionary exits, or a capital-constrained portfolio.

## Independently measured repairs

| Repair | Old behavior | Candidate behavior | Sole variable / acceptance |
|---|---|---|---|
| Record eligibility | Any row with rere text becomes pending, including veto | Only allowed small/go rows (legacy missing kind retained) | Kind exclusion; veto/watch fixture must add zero records; ordinary rows identical |
| Expiry | Catch-up stop search can extend beyond day 60 | Search ends at min(latest, entry+60) | Search bound only; post-expiry stop cannot override expiry; within-horizon stop unchanged |
| Cohort statistics | Closed trades compared before surviving peers mature | Only entries with 60 later observed sessions enter comparable cohort; early stops included once cohort matures | Reporting population only; immature early-stop-heavy sample cannot produce a strategy verdict |
| Baseline alignment | Wash=(1-close/high), daily volume floor, missing ignition, truncated outcomes | Use frozen production predicates, PIT 20-session volume floor, subtype split, complete horizon | Separate A/B modes for wash formula, volume floor and maturity on same historical frames; no tuned parameters |

## Evidence and invariants

- Same input daily frames and ex-dividend calendar for each A/B mode; SHA-256 manifest saved with research output. Model values are never recomputed: `prob_edge` delta is not applicable to a ledger/report-only repair.
- Test fixtures: post-expiry crash; expiry-day stop; veto/watch; old missing kind; mixed early stop/survivor cohort; subtype; dividend event boundary.
- Preserve every pre-existing production-ledger row; experiments use a copied ledger and a separate report destination. Production ledger file hash must remain unchanged.
- Historical output includes ticker + signal date + subtype, maturity eligibility, gross and assumed-cost net return, stop status, signal counts, entry-date counts and annual splits. Overlapping signals are not independent trades and cannot justify a statistical significance claim.
- Costs are sensitivity assumptions: sell tax 0.3%, buy/sell commission 0.1425% each, slippage 0/0.1%/0.25% each side. No day-trading tax rate applies to this 60-session strategy.
- Passing repairs establish correct measurement, not profitability. Existing +4.58% / 35.8% is retained only as a dated historical reference pending a fully comparable baseline; no automatic lane downgrade or threshold change.
