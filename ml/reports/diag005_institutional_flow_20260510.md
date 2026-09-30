# DIAG-005 Institutional Flow Backtest

## Rule

- Foreign net buy for 3 consecutive ticker trading days
- Investment trust net buy on signal day
- Close above MA20 on signal day
- Entry: next trading day open
- Primary exit: 20 trading-day timeout
- Secondary diagnostic exit: foreign net sell, otherwise 20D timeout
- Immature signals without a full forward window are excluded.

## Summary

| Variant | Trades | Months | Monthly return | TWII same-window | Monthly alpha | Position win rate | Beat TWII rate | MDD | Avg hold days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed20 | 2746 | 28 | 1.77% | 2.98% | -1.21% | 48.00% | 40.02% | -19.04% | 20.00 |
| foreign_flip | 3839 | 28 | 0.25% | 0.27% | -0.03% | 40.30% | 39.57% | -1.22% | 2.98 |

## Champion Reference

Existing Two-Stage CL3 summary is included for context only; this DIAG is not a production proposal.

- Source: `F:\stock\ml\reports\two_stage_cl3_backtest_20260509_summary.csv`
- monthly_alpha: 3.54%
- mdd: -5.57%
- sharpe: 2.969360601268965
- calmar: 25.4705689785858

## Liquidity Bucket Read

The full-universe rule is diluted by many small/mid names. Use the bucket CSV to judge whether the signal has edge specifically in large-ticket names.

## Artifacts

- JSON: `F:\stock\ml\reports\diag005_institutional_flow_20260510.json`
- Liquidity bucket CSV: `F:\stock\ml\reports\diag005_institutional_flow_20260510_liquidity_buckets.csv`
- fixed20 monthly CSV: `F:\stock\ml\reports\diag005_institutional_flow_20260510_fixed20_monthly.csv`
- foreign_flip monthly CSV: `F:\stock\ml\reports\diag005_institutional_flow_20260510_foreign_flip_monthly.csv`
- fixed20 trades CSV: `F:\stock\ml\reports\diag005_institutional_flow_20260510_fixed20_trades.csv`
- foreign_flip trades CSV: `F:\stock\ml\reports\diag005_institutional_flow_20260510_foreign_flip_trades.csv`
