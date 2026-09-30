# Method Lock: DISP-CONTRACT-20260906

Locked before implementation on 2026-09-06. Scope: L1 fetch repair and L2 official
period corrections; a downstream eligibility gate is L3 and is separately
integrated by the main task. User authorized repairs and backtest validation.

- Existing algorithm: `scripts/fetch_disposition.py` parses by column position,
  silently skips malformed rows, accepts single-market partial results, and
  overwrites the last complete CSV. It drops announcement dates. Consumers may
  confuse the health JSON with security records. Historical TWSE periods retain
  pre-transition end dates after the 2026-08-10 regulation transition.
- Candidate algorithm: validate named official fields, reject malformed or
  incomplete responses, preserve both-market successful data on any failure,
  retain source and announcement dates, and expose a read-only eligibility
  contract. Resolve versioned corrections only when known at the query cutoff.
- Unique variables for separate A/B comparisons: (A) metadata-vs-period consumer
  wiring; (B) official end-date correction. No price, model, signal, threshold,
  rere selection, or exit-policy changes in this workstream.
- Fixed snapshots: read the same existing `disposition_periods.csv`,
  `disposition_active.csv`, and health JSON into memory for both branches;
  SHA-256 hashes recorded in the report. Any ticker/date join uses the same
  raw DuckDB trading-day frame read-only. Output goes to research paths only.
- Primary metrics: complete-market success/failure, malformed-row rejection,
  old/new period-end differences, active eligibility flips by ticker/date,
  number of changes before correction publication, unchanged unrelated periods.
  `prob_edge` delta must be zero because no model or feature input is modified.
  No performance superiority claim is made from corrected eligibility alone.
- Acceptance: all changed dates match the official 15-row TWSE correction table;
  zero pre-publication changes, zero unrelated period changes, repeated
  correction idempotence, failure leaves existing CSV bytes unchanged, a genuine
  empty result requires the correct schema and explicit zero count, dual-market
  health required, and source dates/target dates are validated.
- Official source: https://www.twse.com.tw/downloads/zh/about/company/dispositionlist.pdf
  (both pages inspected; table timestamp 2026-08-07 18:00 Taiwan time; policy
  effective 2026-08-10). Its `公布日期` is the original security announcement,
  not the later correction publication. Those dates must not be conflated.
- Strategy separation: a disposition blocks current trading eligibility only;
  it is not evidence that a pure rere chart-pattern signal is invalid. The
  caller must preserve the signal and report eligibility separately.

No model artifacts, production CSVs, production ledgers, or schedules are
modified by the validation commands for this workstream.

## Additional pre-implementation lock: next-session coverage

Official read-only query inspection on 2026-09-06 confirmed `startDate/endDate`
select disposition-period overlap, not announcement dates. With end=2026-09-05,
TWSE omits 2455 and the new 6933 period announced 2026-09-04 for 2026-09-07.
The otherwise-identical query ending 2026-09-07 contains both rows. Therefore
comparison C changes only the requested disposition coverage end to the next
Taiwan session, then filters announcement_date <= the original knowledge end.
Acceptance is recovery of exactly those already-announced future periods, no
post-cutoff announcements, and UNKNOWN for legacy snapshots whose next-session
coverage cannot be established. This is distinct from corrections A/B above.
