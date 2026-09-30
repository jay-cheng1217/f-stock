# Candidate Agent Backtest (20260603)

## Verdict

- This is Candidate Lab only; no agent was added to Main League or paper capital.
- Backtest uses signal close -> next open entry with costs; fixed-hold/stop/take-profit exit.
- TAIFEX macro candidate is not fully backtested because historical TAIFEX context is not yet collected.

## Results

| Agent | Status | Return | MDD | Avg Trade | Trades | Admission | Reason |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| efficient_frontier_allocator | BACKTESTED | +12.22% | -31.25% | +0.59% | 193 | ADMITTED | backtest profitable with adequate sample and drawdown control |
| var_budget_momentum | BACKTESTED | +69.16% | -13.83% | +1.59% | 287 | ADMITTED | backtest profitable with adequate sample and drawdown control |
| eps_growth_value | BACKTESTED | -3.32% | -29.62% | -0.03% | 133 | BENCH_FAILED | total return did not clear +5% admission floor |
| dividend_eps_quality | BACKTESTED | +21.89% | -13.12% | +1.31% | 126 | ADMITTED | backtest profitable with adequate sample and drawdown control |
| taifex_macro_regime_beta_overlay | NOT_BACKTESTED |  |  |  |  | NOT_READY | Needs historical point-in-time TAIFEX put/call, institutional positioning, and futures OI. Current public_market_context stores latest/recent snapshots only. |
