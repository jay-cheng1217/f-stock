# RD-005 Phase B Timing Split (2026-05-17)

## Status
- Status: `pass_for_pm_review`
- Scope: Phase B timing split only.
- Phase C remains HOLD.
- No production allocation, sector cap, guardrail, or model change is recommended.

## Target Lock
- Target metric: NAV unexplained residual
- Target window: 2026-05-11 to 2026-05-15
- Target value: -5.64%
- Source field: `attribution.residual_split.unexplained_reconciliation_residual_pct`

## Four-Bucket Summary
| Bucket | Contribution |
|---|---:|
| B1 overnight gap timing | +5.26% |
| B2 rebalance-day intraday slippage | -1.68% |
| B3 held intraday mark timing | +1.26% |
| B4 mark/weight synchronization residual | -10.47% |

## Reconciliation
- B1+B2+B3+B4: -5.64%
- Target: -5.64%
- Error: -0.0000%
- Tolerance: +0.50%
- Pass: `True`

## Reporting Definition Labels
- C1 basis label: `nav_unexplained_target`
- B4 dominance flag: `true`
- Cross-window sign stability: `insufficient_windows`
- Label version: `rd006_minimal_v1`

## Daily Contribution Table
| date | gross exposure | B1 | B2 | B3 | B4 | total |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-11 | +100.00% | +0.65% | +0.01% | +1.04% | -2.89% | -1.19% |
| 2026-05-12 | +80.64% | +1.28% | -1.32% | +0.64% | -1.56% | -0.96% |
| 2026-05-13 | +95.65% | -0.64% | +0.72% | +1.93% | -3.14% | -1.14% |
| 2026-05-14 | +97.83% | +3.51% | -0.39% | -1.13% | -3.15% | -1.16% |
| 2026-05-15 | +99.39% | +0.46% | -0.70% | -1.22% | +0.28% | -1.18% |

Daily B4 is exposure-weight allocated for the table. The window-level B4 total is authoritative.

## 6419 Appendix
- Found: `True`
- Order type: `INTRADAY_CHASE`
- Entry date: 2026-05-12
- Entry slippage: +9.85%
- Target weight at entry: +12.68%
- B1 contribution: +1.56%
- B2 contribution: -1.25%
- B3 contribution: +2.15%
- B4 contribution: +0.00%
- Classification: `as_expected`

portfolio-level residual, not assigned to individual tickers

6419 remains diagnostic only and must not produce a production add/cut decision.

## Data Sufficiency
- Holding-day rows: 132
- Missing required price rows: 0
- Missing required exposure: +0.00%
- Missing stop threshold: +0.56%
- New data source required: `False`
- Stop reasons: `[]`

## Kill Criteria Result
- Result: `actionable_for_pm_ticket`
- Material buckets abs >= 3.0pp: 2
- All buckets abs < 1.0pp: `False`
- Observable |B1+B2+B3| >= half |target|: `True`
- Phase C status: `HOLD`

Diagnostic only; any production action requires a separate PM ticket.

## Prohibited Conclusions
- No production allocation change.
- No sector cap change.
- No 14-guardrail threshold change.
- No model retraining.
- No RD-002 Stage B production adoption.
- No RD-005 Phase C execution without PM approval.
