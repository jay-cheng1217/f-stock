# RETRAIN-03 official OHLC and price-continuity audit

- Verdict: **PASS**
- Source: `F:\stock\日K資料`
- Backup: `F:\stock_backups\日K資料_20260717_RETRAIN03`
- Corporate-action rows: 16,203
- Scan start: `2020-01-01`; extreme threshold: `35%`

## Integrity

| Check | Current | Backup |
|---|---:|---:|
| CSV files | 2,002 | 2,002 |
| Total rows | 3,025,328 | 3,121,514 |
| Duplicate ticker/date | 0 | 0 |
| OHLC invariant violations | 0 | 9,701 |
| Non-positive OHLC rows (all history) | 208 | 211 |
| Non-positive OHLC rows since scan start | 0 | 3 |
| Raw >35% jumps | 176 | 385 |
| Action-adjusted sequence >35% jumps (before suspension reset) | 11 | 538 |
| Adjusted action-day >35% jumps | 0 | 167 |
| Adjusted >35% jumps after >30d suspension | 3 | 1 |
| Technical-effective adjusted >35% jumps | 8 | 537 |

## Regression checks

- No-event feature frame exact: `{'passed': True, 'ticker': '0051', 'rows': 100}`
- New adjusted extremes versus backup: `2`
- New unexplained adjusted extremes: `0`
- Current action-day adjusted extremes: `0`
- Historical non-positive rows before scan start: `208` (reported for follow-up; outside this ticket's 2020+ repair scope)

## Event samples

| Ticker | Scheduled | Observed | Gap days | Raw 1D | Adjusted 1D | MA20 | RSI14 | ATR14 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 0050 | 2025-06-18 | 2025-06-18 | 8 | -74.78% | +0.87% | 45.44 | 70.58 | 0.73 |
| 8093 | 2026-01-12 | 2026-01-12 | 13 | +60.00% | -3.93% | 23.49 | 29.93 | 1.16 |
| 2429 | 2024-07-02 | 2024-07-02 | 1 | +9.90% | +9.90% | 26.32 | 89.02 | 1.92 |
| 3293 | 2024-07-24 | 2024-07-26 | 3 | -46.35% | +9.93% | 690.36 | 73.39 | 30.39 |
| 3073 | 2021-02-19 | 2021-02-19 | 185 | +58.96% | reset | reset | reset | reset |
| 8101 | 2024-11-19 | 2024-11-19 | 90 | +450.00% | reset | reset | reset | reset |

## Ticket-named breakpoint closure

| Ticker | Date | Mechanism | Backup raw | Current raw | Current adjusted | Status |
|---|---|---|---:|---:|---:|---|
| 0050 | 2025-06-18 | ETF split | -74.78% | -74.78% | +0.87% | resolved |
| 8093 | 2025-10-16 | wrong-basis local bar | +69.51% | +1.71% | +1.71% | resolved |
| 5228 | 2021-02-22 | pre-listing non-regular row | +45.51% | - | - | removed_absent_official_regular_tape |
| 6546 | 2020-11-09 | pre-listing non-regular row | +299.30% | - | - | removed_absent_official_regular_tape |

## Remaining adjusted extremes

| Ticker | Date | Raw | Adjusted | Gap days | Reset | Official action | Factor |
|---|---|---:|---:|---:|---|---|---:|
| 8101 | 2024-11-19 | +450.00% | +450.00% | 90 | True | False | - |
| 00715L | 2026-03-09 | +62.34% | +62.34% | 3 | False | False | - |
| 3073 | 2021-02-19 | +58.96% | +58.96% | 185 | True | False | - |
| 00715L | 2020-03-20 | +52.01% | +52.01% | 1 | False | False | - |
| 00673R | 2020-04-22 | +49.41% | +49.41% | 1 | False | False | - |
| 00643K | 2024-09-23 | -48.33% | -48.33% | 1035 | True | False | - |
| 2254 | 2023-10-25 | +43.54% | +43.54% | 1 | False | False | - |
| 00715L | 2020-03-09 | -41.38% | -41.38% | 3 | False | False | - |
| 6925 | 2025-05-29 | +38.37% | +38.37% | 1 | False | False | - |
| 00673R | 2020-03-09 | +36.81% | +36.81% | 3 | False | False | - |
| 00715L | 2020-04-22 | -35.86% | -35.86% | 1 | False | False | - |

## Method

- Raw OHLCV remains exchange-reported and unadjusted.
- Training labels use official economic reference factors.
- Technical indicators use official opening/starting-trade basis factors.
- No model training, model selection, scheduler promotion, or protected artifact change was performed.
