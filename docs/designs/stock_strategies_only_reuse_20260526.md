# stock-strategies-only Reuse Plan

As of: 2026-05-26

## SA Decision

`kevin801221/stock-strategies-only` is useful as a lightweight presentation and
watchlist UX reference, not as a replacement for the V2 Champion model stack.

Adoptable ideas:

- BUY/WATCH/SKIP-style explanation copy for humans.
- Watchlist shape: `stock_id`, `name`, `category`, `enabled`.
- Compact market-summary / stock-detail / action-summary report structure.
- Optional fanout notification patterns.

Rejected for production logic:

- Do not import its 65/50 BUY/WATCH thresholds into the order gate.
- Do not replace current exit policy with fixed +10% / -8% / 20D rules.
- Do not move weekly retrain, model artifacts, or local DB jobs to GitHub Actions.

## Step 1 Implemented

Added advisory-only signal explainability labels:

- `ENTRY_READY`, `MODEL_CANDIDATE`, `WATCH`, `BLOCKED`, `SKIP`
- driver text: rank, expected return, dual confirmation, beta, MA20/MA60, ROE, historical win rate
- warning text: high beta, MA stretch, gap risk, penalty reasons, tradability blocks, guardrail/risk tags

These labels are reporting-only and must not feed model training, target labels,
production gates, allocation sizing, or scheduler decisions.

## Next Steps

1. Add a manual Watchlist tab backed by local JSON/SQLite or an import flow.
2. Add optional Telegram/LINE fanout after email/ntfy behavior remains stable.
3. Evaluate a small "single-stock what-if" view using the existing local data, not external strategy thresholds.
