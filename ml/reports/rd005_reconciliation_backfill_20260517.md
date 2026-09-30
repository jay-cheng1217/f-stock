# RD-005 Reconciliation Backfill (2026-05-17)

## Status
- Scope: A1-A4 retroactive backfill only.
- B/C timing and OOS remain paused.
- No production allocation, model, sector cap, or guardrail change is recommended.

## A1 Method Lock Doc
- Path: `F:\stock\docs\designs\rd005_residual_deepdive_method_lock_20260517.md`
- Exists: `True`

## A2 Metric Reconciliation
- NAV-level unexplained reconciliation residual: -5.64%
- Holding-level residual after locked factors: +6.04%
- Difference (holding residual - NAV unexplained): +11.67%
- Gross exposure / cash residual: -4.15%
- Selection effect: +11.66%
- Allocation effect: -3.70%
- Execution effect: +1.07%
- Exit effect: +0.43%
- Ledger reconciliation error: +9.6212%

Interpretation:
The two residuals are different diagnostics. Holding-level residual_after_factor is a selection/idiosyncratic return of marked holdings versus locked market/sector factors. NAV unexplained is the remaining gross-exposure/cash reconciliation bucket after execution and exit effects. They should not be treated as the same target without an explicit bridge.

Practical gate:
B/C cannot start until PM chooses whether the next target is NAV unexplained, holding-level residual, or a bridge metric.

## A3 6419 Idiosyncratic Deepdive
- Weighted residual contribution: +3.40%
- Share of formal residual: +56.29%
- Sector: 光電業
- Entry date / price: 2026-05-12 / 150.5
- Order type / fill status: INTRADAY_CHASE / FILLED
- Entry slippage: +9.85%
- Entry rank 20D: 3.0
- Target weight: +12.68%
- Position marked close return since entry: +29.24%
- Daily-K window close return (2026-05-11 to 2026-05-15): +41.97%
- Max volume date / volume: 2026-05-14 / 2646611.0
- Max volume vs prior 20D avg: 4.24x
- Formal-window foreign/dealer flow: 361282.0 / 361282.0
- Latest local financial row: 2025Q4 revenue=498.24M, gross_margin=26.6%, operating_margin=12.07%
- Valuation at formal end: PE=15.47, PB=4.93, dividend_yield=1.39
- Disposition overlap in formal window: none
- Local news rows found: 0

Assessment:
6419 is an idiosyncratic concentration, not broad factor exposure. It entered as INTRADAY_CHASE with high entry slippage, then rallied sharply with volume and dealer flow spikes during the formal window.

6419 result is attribution-only and must not be used as a production add/cut decision.

## A4 Window Sensitivity
- Mock window: 2026-04-23 to 2026-04-29, residual -11.25%
- Formal window: 2026-05-11 to 2026-05-15, residual +6.04%
- Sign flipped: `True`

The locked residual_after_factor metric is window-sensitive. Any future citation must include the date window and should not infer a stable structural residual from one week.

## SA Gate
- B/C timing/OOS remains blocked.
- PM must choose the target metric before any further RD work.
