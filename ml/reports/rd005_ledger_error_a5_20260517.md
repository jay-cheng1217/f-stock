# RD-005 A5 Ledger Error Closure (2026-05-17)

## Status
- Scope: A5 ledger reconciliation error only.
- B/C timing and OOS remain HOLD.
- No production allocation, model, sector cap, or guardrail change is recommended.

## Finding
- Legacy NAV window return: +5.90%
- Legacy cumulative ledger total at window end: -3.72%
- Legacy reported ledger error: +9.6212%

The +9.62% error came from a calculation-definition mismatch: the old check compared a 5/11~5/15 NAV window delta with cumulative ledger P&L through 5/15.

## Corrected Window-Aware Check
- Baseline date: 2026-05-10
- Ledger baseline cumulative P&L: -9.6212%
- Ledger end cumulative P&L: -3.7234%
- Ledger window delta: +5.8978%
- Corrected reconciliation error: -0.0000%
- Legacy error equals negative baseline: `True`
- NAV return matches ledger window delta: `True`

## Source Classification
- Data source inconsistency: `False`
- Sampling timestamp gap: `False`
- Calculation definition mismatch: `True`

The legacy ledger check compared a window NAV delta with cumulative ledger P&L as of the window end. Both values came from the same Champion DB and marks; no missing source table was needed to close the gap.

## One-Time vs Structural
- Classification: `structural_for_non_inception_windows`

Any report whose start date is after ledger inception can create a false ledger error if the start baseline cumulative P&L is not subtracted. The magnitude equals the negative pre-window ledger baseline.

## Fix Direction
- Selected: `adjust_definition`
- Implemented in: `scripts/champion_nav_attribution.py`

Ledger reconciliation is now start-aware: it records the pre-window baseline cumulative P&L, the window-end cumulative P&L, and reconciles NAV return against the ledger window delta.

## SA Gate
- A5 may be sent to PM for closure review.
- B/C remains blocked until PM accepts A5 and chooses the target metric.
