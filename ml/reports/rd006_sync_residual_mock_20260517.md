# RD-006 Sync Residual Mock (2026-05-17)

## Status
- Status: `mock_inconsistent_needs_pm_review`
- Scope: existing-data mock only.
- No production code, ledger schema, model, allocation, sector cap, or guardrail change is recommended.

## Window Comparison
| Window | B1 | B2 | B3 | B4 | B1+B2+B3 | B4 dominance |
|---|---:|---:|---:|---:|---:|---|
| 2026-04-23 to 2026-04-29 | +2.39% | -18.92% | -0.91% | +16.19% | -17.45% | `False` |
| 2026-05-11 to 2026-05-15 | +5.26% | -1.68% | +1.26% | -10.47% | +4.83% | `True` |

## Cross-Window Checks
- B4 same sign across mock/formal: `False`
- Mock B4 dominance triggered: `False`
- Formal B4 dominance triggered: `True`
- Mock abs(B4) / abs(B2): +85.58%

## Candidate Readout
### C1 target/bucket basis mismatch
- Mock support: `high`
- Reason: B4 flips sign across the mock and formal windows, so a stable one-direction daily sync drag is not established.

### C2 weight snapshot lag
- Mock support: `medium`
- Reason: Mock gross exposure averages above 100%, while formal exposure is below 100%; exposure normalization likely changes B4 behavior.

### C3 mark refresh / stale mark timing
- Mock support: `low_to_medium`
- Reason: No missing required price rows are observed in the mock, but timestamp semantics still require row-level inspection.

### C4 cash/gross exposure normalization
- Mock support: `medium`
- Reason: Mock leverage and formal near-cash exposure differ materially, making gross/cash normalization a plausible B4 driver.

### C5 corporate action / price basis
- Mock support: `low`
- Reason: No candidate evidence from existing aggregate rows yet; defer unless row-level outliers appear.

## SA Gate
- RD patch allowed: `False`
- Reason: Mock does not reproduce the same B4 sign/dominance pattern. RD should not patch production attribution before PM accepts a broader diagnostic scope.

Mock interpretation: RD-005 remains closed, but RD-006 should not assume a stable one-direction sync drag from the formal window alone.
