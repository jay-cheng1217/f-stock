# Public Market Context Data Layer

This layer fetches official public-data snapshots for risk overlays and future
agent/model research. It is read-only: it does not alter production gates,
model weights, `daily_k`, or portfolio allocation.

## Canonical Source Order

1. TWSE / TPEx / TAIFEX / MOPS official sources
2. FinMind or other aggregators only as fallback or cross-check
3. Paid sources only after separate PM/SA approval

## Current Datasets

- TAIFEX: put/call ratio, institutional futures/options positioning, large
  trader futures OI, FCM futures volume
- TWSE: market-level 5-second order/trade proxy, SBL availability, ex-right
  forecast, attention/disposition securities, official news
- TPEx: intraday statistics proxy, short/SBL pressure, ex-right events,
  warning securities, valuation cross-check

## Outputs

- Raw latest snapshots:
  `ml/data/public_market_context/*_latest.json`
- Manifest:
  `ml/data/public_market_context/manifest_latest.json`
- Quality report:
  `ml/reports/public_market_context_latest.json`

## Scheduling

`scripts/fetch_public_market_context.py` is called by:

- `scripts/daily_pipeline.py` during the non-predict-only data refresh path
- `scripts/smart_update_auto.py` Phase 1

Failures in optional datasets mark the report `degraded`. Required TAIFEX
datasets failing mark the step failed so PM/SA can review the missing market
context before relying on settlement-week risk overlays.

## Boundaries

- No data from this layer becomes a production model feature without a separate
  leakage review, same-snapshot backtest, and PM/SA approval.
- Intraday endpoints here are proxies. They are not full tick, minute-bar, or
  order-book history.
- Existing daily K, institutional flow, and margin/short balances remain owned
  by `twstock.py`; this layer does not overwrite them.

## V2 / Selection Integration

The current integration is deliberately additive:

- `scripts/public_market_context_quality.py` checks source status, expected
  fields, and freshness.
- `scripts/train_v2.py` writes the quality result into every weekly V2 model
  metadata file and a matching
  `ml/reports/public_market_context_quality_v2_<timestamp>.json` artifact.
- `scripts/build_unified_signals.py` adds row-level metadata columns such as
  `public_market_context_status`, `public_market_context_time_basis`, and
  `public_market_context_model_feature_gap`.
- Unified signals also include `selection_model_primary_layer`,
  `selection_model_alignment`, and `selection_model_reason` so Web/PM review
  can tell whether a name was supported by TQuant reference, entry benchmark,
  or T+1 only.

Do not treat `ml/data/public_market_context/*_latest.json` as LightGBM
training features. They are latest snapshots, not point-in-time historical
panels. Promotion into V2 features requires historical backfill, leakage
review, fixed-snapshot A/B, and PM/SA approval.
