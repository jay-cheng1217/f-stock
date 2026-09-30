# RETRAIN-02 TDCC gap-aware feature impact

## Verdict

**PASS**

## Scope

- Source: 2020-01-03 through 2026-07-09
- Source weeks / complete weekly grid: 298 / 341
- Global missing weeks: 43
- Training tickers: 1,893
- Tickers with at least one gap: 1,893
- Total ticker-level missing weeks: 81,927

## Impact

- Observed ticker-week samples: 530,182
- Affected observed samples: 380,420
  (71.75%)
- Global 43-gap 12-week neighborhood share: 75.17%
- Legacy numeric cells correctly converted to unknown: 309,134

| Feature | Affected observed rows | Old numeric to NaN |
|---|---:|---:|
| `retail_pct` | 0 | 0 |
| `whale_pct` | 0 | 0 |
| `whale_pct_chg` | 60,317 | 60,317 |
| `retail_pct_chg` | 60,317 | 60,317 |
| `holders_chg_pct` | 60,317 | 60,317 |
| `whale_retail_ratio` | 0 | 0 |
| `whale_trend_4w` | 122,885 | 3,739 |
| `retail_capitulation` | 300,148 | 32 |
| `whale_acc_weeks` | 42,319 | 0 |
| `whale_trend_8w` | 242,277 | 1,908 |
| `whale_acc_momentum` | 168,363 | 62,155 |
| `whale_pct_rank_12w` | 314,373 | 32 |
| `whale_retail_diverge` | 60,317 | 60,317 |

## Invariants

- Tickers with no weekly gaps: 0
- Maximum absolute delta on no-gap tickers: `0.000e+00`
- NaN mismatches on no-gap tickers: 0
- Observed samples before each ticker's first gap: 7,438
- Maximum absolute delta in continuous prefixes: `0.000e+00`
- NaN mismatches in continuous prefixes: 0
- Thursday holiday observations retain their actual effective date; only missing weeks use Friday placeholders.

## Reproduction

```powershell
python scripts/audit_tdcc_gap_features.py
```
