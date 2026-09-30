# RD-006 Sync Residual Offline Diagnostic (2026-05-17)

## Status
- Status: `actionable_diagnostic_pm_review`
- Scope: offline diagnostic only.
- Production patch, schema change, and historical retrofit remain HOLD.

## Window Summary
| Window | target | B1 | B2 | B3 | B4 | observable B1+B2+B3 | avg gross exposure |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-04-23 to 2026-04-29 | -1.26% | +2.39% | -18.92% | -0.91% | +16.19% | -17.45% | +135.44% |
| 2026-05-11 to 2026-05-15 | -5.64% | +5.26% | -1.68% | +1.26% | -10.47% | +4.83% | +94.70% |

## Candidate Explanatory Power
| Candidate | mock proxy | mock power | formal proxy | formal power | >=30% both |
|---|---:|---:|---:|---:|---|
| C1 target/bucket basis mismatch | +16.19% | +100.00% | -10.47% | +100.00% | `True` |
| C2 transition weight snapshot lag | -17.25% | +100.00% | -1.26% | +12.00% | `False` |
| C3 mark refresh / stale mark timing | +0.00% | +0.00% | +0.00% | +0.00% | `False` |
| C4 cash/gross exposure normalization | +0.08% | +0.52% | -4.15% | +39.59% | `False` |
| C5 corporate action / price basis | +0.00% | +0.00% | +0.00% | +0.00% | `False` |

## C1 Mature vs Young Test
- Mock mode: `young_transition`, driver -17.25%, power +100.00%
- Formal mode: `mature_held`, driver +6.09%, power +58.15%
- PM combination rule pass: `True`

Interpretation: mock B4 offsets a young/transition-heavy observable timing proxy, while formal B4 offsets a mature-held observable timing proxy. That supports C1 plus window-conditional portfolio state, not a universal one-direction sync drag.

## Recommended Interpretation
- Dominant root cause: C1 target/bucket basis mismatch with window-conditional portfolio state
- One-time vs structural: Not universal structural daily drag. It is structural to the current attribution decomposition when B4 is used to bridge a NAV unexplained target to mark-level timing proxies, but the sign and dominance are window-dependent.
- RD-002 Stage B impact: Execution-slippage case studies such as 6419 remain usable for RD-002. NAV-level B4 residuals must not be used to score Stage B benefit until the reporting-definition issue is fixed.

## Fix Scope Options
| Scope | Recommendation | Blast radius |
|---|---|---|
| minimal_reporting_definition | Add C1 basis labels, B4 dominance, and cross-window sign-stability checks to NAV attribution outputs. | Low: report schema and docs only; no DB mutation. |
| medium_reporting_refactor | Split NAV unexplained target from observable timing proxy target and report young-transition vs mature-held sub-bases. | Medium: attribution scripts and tests; regenerate only approved windows first. |
| ledger_schema_or_write_path | Add explicit as-of weight snapshot or mark timestamp fields only if row-level proof shows C2/C3 cannot be fixed in reporting. | High: DB schema/write path, historical compatibility, and regression risk. |

## RD Gate
- Production patch allowed: `False`
- Schema change allowed: `False`
- Historical retrofit allowed: `False`
- Next step: PM review of offline diagnostic before any implementation patch.
