# RD-005 Phase B Timing Method Lock (2026-05-17)

## Purpose

This document pre-registers RD-005 Phase B before RD execution.

PM has approved B kickoff only after this method lock is delivered. Phase C remains HOLD.

## Target Lock

- Target metric: NAV unexplained residual
- Target value: -5.64pp
- Exact source value: `ml/reports/rd005_residual_split_20260517.json`
- Exact field: `attribution.residual_split.unexplained_reconciliation_residual_pct`
- Numeric value: `-0.05638164814819354`

Do not switch the target to holding-level factor residual, 6419 residual contribution, or any bridge metric.

## Window Lock

- Start date: 2026-05-11
- End date: 2026-05-15
- Window expansion is forbidden.
- Do not include 2026-05-04 to 2026-05-15 or any adjacent dates.
- Do not shrink to a single day in Phase B. Single-day narrowing belongs to Phase C and still requires PM approval.

## Required Inputs

- `paper_portfolio_v2_champion.db`
  - `unified_positions`
  - `unified_marks`
- `ml/reports/rd005_residual_split_20260517.json`
- `ml/reports/rd005_ledger_error_a5_20260517.json`
- Daily K files from the configured `DAILY_K_DIR`
- TWII index from the configured index path

Do not request tick data, minute bars, broker fill logs, or intraday order-book data during Phase B. If the method cannot proceed without a new data source, stop and return to PM.

## Fixed Four Buckets

RD must decompose the NAV unexplained residual into exactly these four timing buckets:

| Bucket | Definition | Expected data |
|---|---|---|
| B1 overnight gap timing | Prior trading close to current day open for position-day exposure in the locked window | daily K prior close/open |
| B2 rebalance-day intraday slippage | Entry or rebalance day execution/mark slippage visible from ledger fields and daily K open/close | `entry_date`, `entry_price`, `entry_slippage_pct`, daily K open/close |
| B3 held intraday mark timing | Current day open to close movement for already-held exposure | daily K open/close, marks |
| B4 mark/weight synchronization residual | Residual from stale marks, weight timing, cash/gross exposure synchronization, and daily attribution alignment after B1-B3 | ledger marks, target weights, NAV attribution rows |

The bucket list is closed. RD may add diagnostics inside a bucket, but must not add a fifth top-level bucket or rename these buckets.

## Reconciliation Rule

The Phase B report must show:

```text
B1 + B2 + B3 + B4 ~= NAV unexplained residual (-5.64pp)
```

Tolerance: 0.50pp absolute reconciliation error.

If the four buckets cannot reconcile to the target within tolerance using the approved data sources, return `status: data_or_definition_insufficient` and stop.

## 6419 Tracking Requirement

6419 must be explicitly traceable in the Phase B report because it dominated the Phase A holding-level residual.

Required 6419 fields:

- ticker
- order type: `INTRADAY_CHASE`
- entry date: 2026-05-12
- entry slippage: +9.85%
- target weight at entry
- contribution to B1/B2/B3/B4, even if zero or not applicable

Expected classification: 6419 entry slippage should land in B2 rebalance-day intraday slippage. If it lands elsewhere, RD must explain why without changing the bucket definitions.

6419 remains diagnostic only and must not produce a production add/cut decision.

## Data Sufficiency Rules

Stop and return to PM if any of these occur:

- Required daily K open/close rows are missing for contribution-weighted exposure above 10% of the target magnitude.
- 6419 cannot be traced to a bucket.
- A new data source such as tick data or minute bars is required to classify a material bucket.
- Reconciliation error exceeds 0.50pp after using the locked four buckets.

Non-material missing rows may be reported as an explicit `unknown_small` line inside B4, but cannot become a fifth bucket.

## Kill Criteria

PM kill criteria are locked as:

1. If any bucket is `>= 3.0pp` in absolute contribution, mark it `actionable_for_pm_ticket`; do not recommend or execute production changes.
2. If all buckets are `< 1.0pp` in absolute contribution and the four buckets cannot explain at least half of the -5.64pp target, mark B `inconclusive`; recommend Phase C as P0 with single-day narrowing, but do not start C.
3. If a new data source is needed, stop and return to PM before spending more RD time.

## Output Specification

RD must produce:

- `ml/reports/rd005_phase_b_timing_split_20260517.md`
- `ml/reports/rd005_phase_b_timing_split_20260517.json`

Required report sections:

- Target lock and window lock
- Four-bucket summary table
- Reconciliation equation and error
- Daily contribution table
- 6419 appendix
- Data sufficiency section
- Kill criteria result
- Explicit statement that C remains HOLD

## Prohibited Conclusions

The Phase B report must not recommend:

- production allocation changes
- sector cap changes
- 14 guardrail threshold changes
- model retraining
- RD-002 Stage B production adoption
- RD-005 Phase C execution without PM approval

Any action conclusion requires a new PM ticket.
