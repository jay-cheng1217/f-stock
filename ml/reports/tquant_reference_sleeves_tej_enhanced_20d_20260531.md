# TQuant Reference Sleeves Backtest

- Generated: `2026-05-31T23:06:22`
- Signal window: `2026-04-01` to `2026-04-29`
- Horizon: `20` trading days, entry next-day open to horizon close
- Status: `pass`
- Candidate rows / priced rows: `476` / `476`

> Offline/read-only. No production gate, model-selection, scheduler, DB schema, or paper-book mutation.

## Verdict

- Production strong-gate delta vs ML selected: `+9.09%`
- Guard delta vs ML selected: `+0.79%`
- TQuant strong delta vs ML selected: `+4.66%`
- ML selected but TQuant reject delta: `-2.78%`
- Failures: `none`
- Warnings: `none`
- Evidence: `ml_selected_reject_basket_dragged_returns`
- SA recommendation: Keep the enhanced TEJ/TQuant strong consensus as the production 20D entry gate; the 20D replay supports the strong threshold, while watch consensus remains a looser manual override mode.

## Basket Metrics

| Basket | Names | Avg | Median | Win | Avg 20D Pred | Avg TQuant Votes | Avg Risk Flags |
|---|---:|---:|---:|---:|---:|---:|---:|
| ml_selected | 59 | +8.37% | +4.22% | +54.24% | +10.61% | 4.32 | 1.66 |
| ml_not_selected | 417 | +12.95% | +6.49% | +59.95% | +6.97% | 5.29 | 1.28 |
| tquant_consensus_strong | 164 | +13.03% | +9.44% | +62.80% | +7.13% | 6.99 | 1.17 |
| tquant_consensus_watch_or_strong | 386 | +14.18% | +7.55% | +60.36% | +7.35% | 5.36 | 1.04 |
| ml_selected_and_tquant_strong | 12 | +17.46% | +6.84% | +58.33% | +8.34% | 6.58 | 1.33 |
| ml_selected_and_tquant_watch_or_strong | 46 | +9.16% | +5.59% | +54.35% | +10.60% | 4.67 | 1.46 |
| ml_selected_but_tquant_reject | 13 | +5.59% | +2.07% | +53.85% | +10.64% | 3.08 | 2.38 |
| tquant_strong_not_ml_selected | 152 | +12.68% | +9.48% | +63.16% | +7.04% | 7.03 | 1.16 |

## Sleeve Definitions

- `VAM`: 21D return divided by 21D daily-return volatility, top 30% cross-section.
- `MRAT`: MA21 / MA200 with close-to-high, higher-low, and range-compression checks.
- `BAZ`: three-timescale EWM MACD response score from the TQuant LambdaMART example.
- `EXP_MOM`: log-price slope times R-squared, inspired by Expanded Momentum Model.
- `KD` / `RSI` / `AROON`: TEJ/TQuant technical confirmation votes.
- `REVENUE`: point-in-time monthly revenue YoY and 3M YoY both positive.
- `FIN_QUALITY` / `FIN_TURN` / `VALUE`: conservative point-in-time financial and valuation votes.
- `INST`: recent institution net buying from local daily_k data.
- Risk flags: financing crowding, settlement/high-beta risk, high beta, gap risk, KD overheat, margin deterioration, valuation, and sector crowding.

## Factor Validation

- Method: `MAD5 winsorize -> z-score by date -> sector/log-amount neutralized -> Spearman RankIC`
- Status: `ok`

| Factor | Obs | Days | RankIC | IR | Top Q Avg | Bottom Q Avg | Spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| reference_score | 476 | 19 | -0.044 | -0.154 | +11.96% | +12.76% | -0.81% |
| technical_votes | 476 | 19 | -0.051 | -0.180 | +9.33% | +15.03% | -5.69% |
| fundamental_votes | 476 | 19 | -0.012 | -0.044 | +13.07% | +11.80% | +1.27% |
| support_votes | 476 | 19 | -0.076 | -0.314 | +11.16% | +13.32% | -2.17% |
| risk_flags_inverse | 476 | 19 | 0.120 | 0.526 | +19.28% | +9.60% | +9.67% |
| model_pred_return | 476 | 19 | -0.095 | -0.453 | +9.24% | +18.85% | -9.60% |
| model_prob_edge | 476 | 19 | 0.107 | 0.522 | +15.82% | +13.16% | +2.67% |

## Daily Replay

| Signal | Candidates | ML Names | ML Avg | ML Win | Guard Names | Guard Avg |
|---|---:|---:|---:|---:|---:|---:|
| 2026-04-01 | 30 | 0 | - | - | 0 | - |
| 2026-04-02 | 30 | 0 | - | - | 0 | - |
| 2026-04-07 | 30 | 0 | - | - | 0 | - |
| 2026-04-08 | 30 | 0 | - | - | 0 | - |
| 2026-04-09 | 27 | 0 | - | - | 0 | - |
| 2026-04-10 | 8 | 0 | - | - | 0 | - |
| 2026-04-13 | 30 | 0 | - | - | 0 | - |
| 2026-04-14 | 5 | 0 | - | - | 0 | - |
| 2026-04-15 | 9 | 0 | - | - | 0 | - |
| 2026-04-16 | 7 | 0 | - | - | 0 | - |
| 2026-04-17 | 30 | 0 | - | - | 0 | - |
| 2026-04-20 | 30 | 0 | - | - | 0 | - |
| 2026-04-21 | 30 | 0 | - | - | 0 | - |
| 2026-04-22 | 30 | 16 | +2.46% | +31.25% | 11 | +7.74% |
| 2026-04-23 | 30 | 19 | +10.96% | +52.63% | 15 | +7.88% |
| 2026-04-24 | 30 | 5 | +2.44% | +60.00% | 3 | +5.62% |
| 2026-04-27 | 30 | 6 | +11.20% | +83.33% | 5 | +12.38% |
| 2026-04-28 | 30 | 5 | +13.36% | +60.00% | 5 | +13.36% |
| 2026-04-29 | 30 | 8 | +12.53% | +75.00% | 7 | +10.35% |

## Best TQuant Strong

| signal_date | entry_date | mark_date | ticker | selected_by_ml | tquant_consensus | tquant_support_votes | tquant_risk_flags | tquant_support_reasons | tquant_risk_reasons | return_to_mark | pred_return_20d |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-07 | 2026-04-08 | 2026-05-06 | 6861 | False | strong | 7 | 0 | BAZ,KD,RSI,REVENUE,FIN_QUALITY,INST,ML_EDGE |  | +191.47% | +6.11% |
| 2026-04-01 | 2026-04-02 | 2026-05-04 | 3167 | False | strong | 9 | 0 | VAM,BAZ,EXP_MOM,RSI,AROON,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE |  | +104.31% | +15.36% |
| 2026-04-27 | 2026-04-28 | 2026-05-26 | 1597 | False | strong | 6 | 0 | BAZ,EXP_MOM,AROON,FIN_QUALITY,INST,ML_EDGE |  | +100.00% | +3.31% |
| 2026-04-23 | 2026-04-24 | 2026-05-22 | 3498 | True | strong | 6 | 1 | AROON,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP | +99.50% | +7.01% |
| 2026-04-08 | 2026-04-09 | 2026-05-07 | 6274 | False | strong | 6 | 1 | VAM,BAZ,EXP_MOM,AROON,FIN_QUALITY,INST | KD_OVERHEAT | +97.67% | +3.11% |
| 2026-04-02 | 2026-04-07 | 2026-05-05 | 8064 | False | strong | 9 | 0 | VAM,BAZ,EXP_MOM,KD,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE |  | +89.39% | +9.04% |
| 2026-04-29 | 2026-04-30 | 2026-05-28 | 4760 | False | strong | 6 | 0 | KD,RSI,REVENUE,FIN_QUALITY,INST,ML_EDGE |  | +79.16% | +5.40% |
| 2026-04-01 | 2026-04-02 | 2026-05-04 | 8046 | False | strong | 7 | 1 | VAM,MRAT,BAZ,EXP_MOM,FIN_TURN,INST,ML_EDGE | VALUATION | +67.69% | +1.01% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 3498 | True | strong | 7 | 1 | RSI,AROON,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP | +67.62% | +7.63% |
| 2026-04-28 | 2026-04-29 | 2026-05-27 | 1597 | False | strong | 8 | 0 | VAM,BAZ,EXP_MOM,KD,AROON,FIN_QUALITY,INST,ML_EDGE |  | +63.79% | +3.51% |
| 2026-04-01 | 2026-04-02 | 2026-05-04 | 2383 | False | strong | 8 | 1 | VAM,BAZ,EXP_MOM,AROON,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE | VALUATION | +62.84% | +6.12% |
| 2026-04-14 | 2026-04-15 | 2026-05-13 | 1597 | False | strong | 7 | 2 | VAM,BAZ,EXP_MOM,AROON,FIN_QUALITY,INST,ML_EDGE | FINANCING_CROWDING,KD_OVERHEAT | +62.23% | +0.71% |

## Worst ML Selected

| signal_date | entry_date | mark_date | ticker | selected_by_ml | tquant_consensus | tquant_support_votes | tquant_risk_flags | tquant_support_reasons | tquant_risk_reasons | return_to_mark | pred_return_20d |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 6788 | True | reject | 6 | 3 | VAM,BAZ,AROON,FIN_QUALITY,INST,ML_EDGE | FINANCING_CROWDING,GAP,KD_OVERHEAT | -22.82% | +4.87% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 6510 | True | strong | 6 | 2 | AROON,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP,KD_OVERHEAT | -22.27% | +4.32% |
| 2026-04-23 | 2026-04-24 | 2026-05-22 | 1305 | True | watch | 4 | 2 | KD,RSI,REVENUE,ML_EDGE | GAP,MARGIN_DETERIORATION | -18.42% | +6.76% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 4768 | True | reject | 3 | 3 | RSI,INST,ML_EDGE | HIGH_BETA,GAP,MARGIN_DETERIORATION | -17.71% | +9.94% |
| 2026-04-23 | 2026-04-24 | 2026-05-22 | 6739 | True | watch | 5 | 2 | EXP_MOM,AROON,FIN_QUALITY,FIN_TURN,ML_EDGE | GAP,VALUATION | -17.42% | +8.87% |
| 2026-04-24 | 2026-04-27 | 2026-05-25 | 6568 | True | watch | 5 | 1 | AROON,REVENUE,FIN_QUALITY,INST,ML_EDGE | GAP | -16.79% | +11.88% |
| 2026-04-29 | 2026-04-30 | 2026-05-28 | 6588 | True | watch | 4 | 2 | RSI,REVENUE,FIN_TURN,ML_EDGE | GAP,MARGIN_DETERIORATION | -16.30% | +10.55% |
| 2026-04-23 | 2026-04-24 | 2026-05-22 | 6510 | True | strong | 6 | 2 | AROON,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP,VALUATION | -12.92% | +5.05% |
| 2026-04-23 | 2026-04-24 | 2026-05-22 | 3324 | True | watch | 5 | 2 | RSI,AROON,FIN_QUALITY,INST,ML_EDGE | HIGH_BETA,GAP | -12.17% | +4.29% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 6805 | True | watch | 5 | 2 | RSI,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE | GAP,VALUATION | -11.92% | +4.41% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 6640 | True | watch | 4 | 1 | EXP_MOM,REVENUE,FIN_QUALITY,ML_EDGE | MARGIN_DETERIORATION | -11.58% | +4.50% |
| 2026-04-22 | 2026-04-23 | 2026-05-21 | 6226 | True | watch | 3 | 2 | FIN_TURN,INST,ML_EDGE | GAP,MARGIN_DETERIORATION | -11.35% | +11.76% |
