# TQuant Reference Sleeves Backtest

- Generated: `2026-05-31T23:06:35`
- Signal window: `2026-04-01` to `2026-05-22`
- Horizon: `5` trading days, entry next-day open to horizon close
- Status: `warn`
- Candidate rows / priced rows: `892` / `892`

> Offline/read-only. No production gate, model-selection, scheduler, DB schema, or paper-book mutation.

## Verdict

- Production strong-gate delta vs ML selected: `+0.64%`
- Guard delta vs ML selected: `-0.07%`
- TQuant strong delta vs ML selected: `+1.40%`
- ML selected but TQuant reject delta: `+0.14%`
- Failures: `none`
- Warnings: `rejected_basket_outperformed_ml_selected`
- Evidence: `loose_watch_guard_not_promoted`
- SA recommendation: Keep the enhanced TEJ/TQuant strong consensus as the production 20D entry gate; the 5D replay supports the strong threshold, while watch consensus remains a looser manual override mode.

## Basket Metrics

| Basket | Names | Avg | Median | Win | Avg 20D Pred | Avg TQuant Votes | Avg Risk Flags |
|---|---:|---:|---:|---:|---:|---:|---:|
| ml_selected | 187 | +0.86% | +0.42% | +51.87% | +7.34% | 4.27 | 1.69 |
| ml_not_selected | 705 | +2.26% | +1.23% | +54.04% | +5.96% | 5.18 | 1.59 |
| tquant_consensus_strong | 252 | +2.26% | +1.64% | +57.14% | +5.94% | 6.93 | 1.21 |
| tquant_consensus_watch_or_strong | 622 | +2.41% | +1.23% | +54.34% | +6.40% | 5.22 | 1.13 |
| ml_selected_and_tquant_strong | 30 | +1.50% | +1.55% | +66.67% | +5.76% | 6.67 | 1.17 |
| ml_selected_and_tquant_watch_or_strong | 128 | +0.79% | +0.89% | +53.12% | +7.76% | 4.60 | 1.30 |
| ml_selected_but_tquant_reject | 59 | +1.00% | -1.51% | +49.15% | +6.45% | 3.56 | 2.53 |
| tquant_strong_not_ml_selected | 222 | +2.36% | +1.82% | +55.86% | +5.97% | 6.97 | 1.21 |

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
| reference_score | 892 | 34 | -0.033 | -0.187 | +1.50% | +1.67% | -0.17% |
| technical_votes | 892 | 34 | -0.062 | -0.227 | +1.54% | +2.81% | -1.27% |
| fundamental_votes | 892 | 34 | 0.050 | 0.294 | +2.16% | +1.33% | +0.83% |
| support_votes | 892 | 34 | -0.056 | -0.271 | +2.03% | +2.33% | -0.30% |
| risk_flags_inverse | 892 | 34 | 0.002 | 0.013 | +2.31% | +1.13% | +1.18% |
| model_pred_return | 892 | 34 | 0.008 | 0.041 | +3.09% | +1.51% | +1.57% |
| model_prob_edge | 892 | 34 | 0.095 | 0.498 | +4.05% | +2.13% | +1.92% |

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
| 2026-04-22 | 30 | 16 | -6.70% | +18.75% | 11 | -4.97% |
| 2026-04-23 | 30 | 19 | +1.22% | +36.84% | 15 | -0.09% |
| 2026-04-24 | 30 | 5 | +2.89% | +40.00% | 3 | +6.20% |
| 2026-04-27 | 30 | 6 | +17.04% | +100.00% | 5 | +10.45% |
| 2026-04-28 | 30 | 5 | +4.95% | +80.00% | 5 | +4.95% |
| 2026-04-29 | 30 | 8 | +5.94% | +100.00% | 7 | +6.28% |
| 2026-04-30 | 30 | 6 | +1.79% | +66.67% | 5 | +1.78% |
| 2026-05-04 | 30 | 7 | +2.96% | +71.43% | 4 | -2.93% |
| 2026-05-05 | 30 | 20 | +1.81% | +65.00% | 12 | +3.25% |
| 2026-05-06 | 30 | 5 | -1.66% | +40.00% | 3 | -8.20% |
| 2026-05-07 | 30 | 18 | +2.75% | +55.56% | 14 | +2.45% |
| 2026-05-08 | 30 | 8 | +4.95% | +62.50% | 6 | +8.24% |
| 2026-05-11 | 30 | 5 | -1.15% | +40.00% | 5 | -1.15% |
| 2026-05-12 | 30 | 6 | -2.66% | +50.00% | 4 | -7.73% |
| 2026-05-13 | 30 | 6 | -11.02% | +16.67% | 4 | -11.01% |
| 2026-05-14 | 30 | 8 | -2.35% | +25.00% | 4 | -3.06% |
| 2026-05-15 | 30 | 0 | - | - | 0 | - |
| 2026-05-18 | 20 | 8 | +5.07% | +75.00% | 4 | +2.72% |
| 2026-05-19 | 19 | 0 | - | - | 0 | - |
| 2026-05-21 | 19 | 9 | +1.00% | +55.56% | 5 | +3.81% |
| 2026-05-22 | 28 | 22 | -1.61% | +40.91% | 12 | -1.19% |

## Best TQuant Strong

| signal_date | entry_date | mark_date | ticker | selected_by_ml | tquant_consensus | tquant_support_votes | tquant_risk_flags | tquant_support_reasons | tquant_risk_reasons | return_to_mark | pred_return_20d |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-27 | 2026-04-28 | 2026-05-05 | 1597 | False | strong | 6 | 0 | BAZ,EXP_MOM,AROON,FIN_QUALITY,INST,ML_EDGE |  | +56.56% | +3.31% |
| 2026-04-01 | 2026-04-02 | 2026-04-10 | 3167 | False | strong | 9 | 0 | VAM,BAZ,EXP_MOM,RSI,AROON,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE |  | +51.44% | +15.36% |
| 2026-04-27 | 2026-04-28 | 2026-05-05 | 6830 | False | strong | 6 | 2 | VAM,EXP_MOM,KD,REVENUE,INST,ML_EDGE | GAP,MARGIN_DETERIORATION | +49.77% | +14.27% |
| 2026-04-28 | 2026-04-29 | 2026-05-06 | 1597 | False | strong | 8 | 0 | VAM,BAZ,EXP_MOM,KD,AROON,FIN_QUALITY,INST,ML_EDGE |  | +46.50% | +3.51% |
| 2026-05-08 | 2026-05-11 | 2026-05-15 | 6419 | False | strong | 7 | 0 | VAM,EXP_MOM,AROON,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE |  | +34.14% | +5.44% |
| 2026-04-28 | 2026-04-29 | 2026-05-06 | 3455 | False | strong | 6 | 2 | VAM,BAZ,EXP_MOM,FIN_QUALITY,INST,ML_EDGE | GAP,MARGIN_DETERIORATION | +33.93% | +3.70% |
| 2026-04-07 | 2026-04-08 | 2026-04-14 | 1735 | False | strong | 7 | 0 | VAM,BAZ,EXP_MOM,KD,FIN_QUALITY,INST,ML_EDGE |  | +33.77% | +11.78% |
| 2026-04-02 | 2026-04-07 | 2026-04-13 | 1735 | False | strong | 6 | 0 | VAM,BAZ,EXP_MOM,KD,FIN_QUALITY,ML_EDGE |  | +31.52% | +9.12% |
| 2026-04-08 | 2026-04-09 | 2026-04-15 | 6274 | False | strong | 6 | 1 | VAM,BAZ,EXP_MOM,AROON,FIN_QUALITY,INST | KD_OVERHEAT | +29.41% | +3.11% |
| 2026-04-01 | 2026-04-02 | 2026-04-10 | 3081 | False | strong | 7 | 2 | VAM,BAZ,EXP_MOM,AROON,FIN_QUALITY,FIN_TURN,ML_EDGE | FINANCING_CROWDING,VALUATION | +28.97% | +10.63% |
| 2026-04-02 | 2026-04-07 | 2026-04-13 | 5439 | False | strong | 6 | 0 | EXP_MOM,KD,FIN_QUALITY,FIN_TURN,INST,ML_EDGE |  | +27.94% | +4.71% |
| 2026-04-27 | 2026-04-28 | 2026-05-05 | 4991 | False | strong | 6 | 2 | EXP_MOM,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP,VALUATION | +26.53% | +10.73% |

## Worst ML Selected

| signal_date | entry_date | mark_date | ticker | selected_by_ml | tquant_consensus | tquant_support_votes | tquant_risk_flags | tquant_support_reasons | tquant_risk_reasons | return_to_mark | pred_return_20d |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-13 | 2026-05-14 | 2026-05-20 | 4577 | True | watch | 5 | 1 | KD,RSI,REVENUE,FIN_TURN,ML_EDGE | MARGIN_DETERIORATION | -17.48% | +7.14% |
| 2026-05-12 | 2026-05-13 | 2026-05-19 | 3529 | True | watch | 4 | 2 | AROON,FIN_QUALITY,INST,ML_EDGE | FINANCING_CROWDING,VALUATION | -17.04% | +4.49% |
| 2026-04-22 | 2026-04-23 | 2026-04-29 | 6788 | True | reject | 6 | 3 | VAM,BAZ,AROON,FIN_QUALITY,INST,ML_EDGE | FINANCING_CROWDING,GAP,KD_OVERHEAT | -16.95% | +4.87% |
| 2026-04-22 | 2026-04-23 | 2026-04-29 | 5291 | True | watch | 4 | 1 | KD,REVENUE,FIN_QUALITY,ML_EDGE | GAP | -16.08% | +4.49% |
| 2026-04-22 | 2026-04-23 | 2026-04-29 | 6510 | True | strong | 6 | 2 | AROON,REVENUE,FIN_QUALITY,FIN_TURN,INST,ML_EDGE | GAP,KD_OVERHEAT | -15.78% | +4.32% |
| 2026-05-13 | 2026-05-14 | 2026-05-20 | 3529 | True | watch | 4 | 1 | AROON,FIN_QUALITY,INST,ML_EDGE | FINANCING_CROWDING | -15.69% | +4.02% |
| 2026-05-11 | 2026-05-12 | 2026-05-18 | 6829 | True | watch | 4 | 1 | VAM,EXP_MOM,REVENUE,ML_EDGE | MARGIN_DETERIORATION | -15.12% | +4.16% |
| 2026-04-22 | 2026-04-23 | 2026-04-29 | 2485 | True | watch | 4 | 1 | KD,REVENUE,FIN_TURN,ML_EDGE | MARGIN_DETERIORATION | -14.71% | +26.63% |
| 2026-05-05 | 2026-05-06 | 2026-05-12 | 6568 | True | strong | 6 | 0 | KD,RSI,REVENUE,FIN_QUALITY,INST,ML_EDGE |  | -14.69% | +11.08% |
| 2026-05-13 | 2026-05-14 | 2026-05-20 | 4768 | True | reject | 2 | 1 | KD,ML_EDGE | MARGIN_DETERIORATION | -14.27% | +7.29% |
| 2026-05-08 | 2026-05-11 | 2026-05-15 | 6805 | True | watch | 5 | 1 | AROON,REVENUE,FIN_QUALITY,FIN_TURN,ML_EDGE | GAP | -14.11% | +6.89% |
| 2026-05-13 | 2026-05-14 | 2026-05-20 | 5475 | True | watch | 4 | 1 | KD,REVENUE,FIN_TURN,ML_EDGE | MARGIN_DETERIORATION | -13.93% | +13.87% |
