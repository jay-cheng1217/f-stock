# Agent Arena Multi-Model Investment Competition

Date: 2026-06-02  
Owner: SA/RD  
Status: v3 web split into Main League / Watchlist / Source Library, production gate untouched

## Objective

Run an indefinite paper competition between multiple investment agents.

- Starting capital per agent: TWD 300,000
- Target capital: TWD 10,000,000
- Market: Taiwan equities, using local `daily_k` data
- Rule: an agent must pass a no-lookahead backtest before it is admitted to the daily contest
- Output: Web tab showing Main League standings, Watchlist / Failed Backtest candidates, current paper holdings, source strategy mapping, and a 60+ repo Source Library

## Why This Is Additive

The arena does not mutate:

- production model files
- Champion entry gate
- order gate
- DB schema for production portfolios
- protected weekly retrain artifacts
- `model_selection.json`

It creates an isolated paper contest database: `agent_arena.db`.

## Source Model Review

The user-provided repositories are too broad and dependency-heavy to merge directly into the local Taiwan production system. They are a source pool, not 60 ready-to-run Taiwan strategies. The safe SA choice is to map them into auditable strategy families first, then split each family into multiple deterministic agents that can be backtested on local Taiwan data.

| Source family | Example repos | v1 arena use |
|---|---|---|
| Multi-agent LLM decision committee | `TauricResearch/TradingAgents`, `hsliuping/TradingAgents-CN`, `HKUDS/Vibe-Trading` | Persona / review layer only. LLM output is not a trade unless backed by deterministic rules and backtest. |
| Backtesting engines | `QuantConnect/Lean`, `quantopian/zipline`, `pyalgotrade`, `pybroker` | No direct dependency in v1. Local no-lookahead replay is lighter and Taiwan-data aligned. |
| ML / RL trading | `stefan-jansen/machine-learning-for-trading`, `AI4Finance-Foundation/FinRL-Trading`, `tensortrade-org/tensortrade` | Converted into an interpretable ML Edge Proxy until same-snapshot A/B and local labels are ready. |
| Portfolio optimization | `Riskfolio-Lib`, `skfolio`, `cvxportfolio`, `deepdow` | Converted into risk-adjusted ranking. Allocation remains equal-notional in v1 for auditability. |
| Technical / discretionary strategies | `fmzquant/strategies`, `StockSharp`, `qtpylib`, `Stock.Indicators`, `CasualTrader` | Converted into trend, breakout, volume, and RSI agents with Taiwan liquidity filters. |
| Taiwan / TEJ / TQuant ideas | `tejtw/TQuant-Lab`, `FactorLibrary-manual`, `TEJ_TOOL_API` family | Converted into VAM momentum and TQuant-style support sleeves using existing local data. |

## Executable Agent Roster

Current executable roster: 29 candidate agents. The latest local run admitted 9 into the Main League and kept 20 in Watchlist / Failed Backtest. More source repositories can be split into additional agents later, but only after the strategy has a deterministic Taiwan-data implementation and a no-lookahead backtest.

| Agent family | Representative agents | Holding |
|---|---|---:|
| TQuant / VAM momentum | TQuant VAM Momentum, VAM Fast 5D, VAM Slow 20D | 5/10/20 trading days |
| Trend following | Dual MA Trend, MA Fast Trend, MA Slow Trend, Long Compounder | 10/20 trading days |
| Taiwan chip flow | Institutional Flow Rider, Foreign Flow Rider, Trust Flow Rider, Dealer Flow Swing, Defensive Flow | 5/10/20 trading days |
| Breakout / volume | Volume Breakout, Volume Surge Swing, Quiet Breakout, ATR Breakout, Bollinger Upper Trend | 5/10 trading days |
| Risk-adjusted allocation | Low Vol Quality Proxy, Risk-Parity Momentum, Low Beta Momentum, Gap-Safe Momentum, High Beta Momentum | 10/20 trading days |
| ML / RL proxy | ML Edge Proxy plus risk/momentum variants | 20 trading days |
| Oscillator / contrarian stress tests | Mean Reversion RSI, RSI Strength, KD Strength, Contrarian Reversal | 5/10 trading days |

Failure is intentional. A failed agent remains visible as `BENCH_FAILED` so the system does not pretend every borrowed GitHub idea works on Taiwan equities.

## Backtest Contract

- Signal uses only same-day historical features.
- Entry price is next trading day's open.
- Exit price is governed by each agent's `exit_policy`, with the fixed holding horizon acting as max-hold.
- Round-trip cost: 0.4%.
- Universe filters: liquidity, price, volume.
- Admission floor:
  - at least 80 trades
  - total return > +5%
  - average trade return > 0
  - max drawdown better than -65%

## Daily Contest Contract

- Daily paper orders are multi-step for buys and immediate-open for sells:
  - after close, agents create `PENDING` intent orders from same-day signals
  - on the next available trading day, buy orders check actual `Open` gap and full-day follow-through
  - if the day opens high but closes weak (`close < open` or `close < signal close`), the buy is skipped as `open_fade_no_follow_through`
  - only after follow-through confirmation does a buy become `READY_OPEN`; it fills at the following available `Open` with slippage
- Each admitted agent starts with TWD 300,000.
- Current v1 allocation is equal-notional across available slots.
- Buy orders also skip if next-open gap is above the configured tolerance; sell orders fill at open because risk control has priority.
- Each agent has its own exit policy:
  - `fast_swing`: short stop/take-profit and MACD fade
  - `trend_trailing`: longer max-hold with trailing giveback and MA20 break
  - `breakout_trailing`: failed-breakout and trailing rules
  - `flow_decay`: exits when institutional flow reverses
  - `risk_control`: tighter stop and lower take-profit
  - `oscillator_*`: shorter oscillator hot/rebound exits
- The contest can be run indefinitely by the morning pipeline.
- Output JSON: `ml/reports/agent_arena_latest.json`
- API: `/api/agent-arena/status`
- Web tab: `Agent 投資賽`

Historical close-filled paper trades before this rule are retained as legacy contest audit data; new runs use pending orders and next-open fills.

## Web Layering Contract

- Main League: admitted agents only; these have paper capital and daily positions.
- Watchlist / Failed Backtest: executable agents that did not clear the admission gate; they remain visible with return, avg trade, MDD, trade count, and rejection reason.
- Source Library: 60+ reviewed repositories and model sources. Each row is marked `CONVERTED`, `CONCEPT_ONLY`, `NOT_LANDED`, or `INFRA_REFERENCE`, with mapped agents shown when applicable.

This split prevents a count mismatch: a source repo is not automatically an executable Taiwan-stock agent, and an executable agent is not automatically admitted into the daily paper contest.

## Follow-Up Candidates

1. Add real Taiwan odd-lot / board-lot constraints.
2. Add sector caps and risk-budget allocation for portfolio-optimization agents.
3. Add shadow-only LLM analyst commentary after deterministic agent picks are known.
4. Add Champion-vs-arena comparison chart.
5. Add daily email summary section after the Web tab proves stable for several sessions.
