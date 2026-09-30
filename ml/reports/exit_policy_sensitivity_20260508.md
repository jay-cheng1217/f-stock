# Exit Policy Full Sensitivity Replay

- Generated at: `2026-05-08T11:39:50.428054+00:00`
- Requested window: `2024-01-01` to `2026-05-08`
- Actual prediction artifacts: `2026-03-11` to `2026-05-07`
- Prediction files: `36`
- Include open MTM: `True`
- Top N: `30` sector-capped production picks

## Summary

| Exit version | Monthly ret | Alpha vs TWII | MDD | Avg hold | Turnover/mo | MA exits | Stop exits | 20D | MTM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current_MA5 | +0.55% | -1.23% | -22.20% | 3.71 | 318.7 | 740 | 126 | 3 | 87 |
| Variant_A_MA10 | +0.70% | -1.64% | -25.80% | 4.49 | 318.7 | 638 | 186 | 19 | 113 |
| Variant_B_StopOnly | +5.71% | -1.22% | -17.34% | 10.48 | 318.7 | 0 | 376 | 294 | 286 |
| Variant_C_MA5_2Day | +1.46% | -1.75% | -21.72% | 5.64 | 318.7 | 547 | 228 | 22 | 159 |

## Focus Tickers

| ticker | variant | prediction_date | return | alpha | hold | exit |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 1305 | Current_MA5 | 2026-04-17 | -3.45% | -5.50% | 2 | MA5_BREAK |
| 1305 | Variant_A_MA10 | 2026-04-17 | -3.45% | -5.50% | 2 | MA10_BREAK |
| 1305 | Variant_B_StopOnly | 2026-04-17 | -10.45% | -20.91% | 10 | STOP_LOSS_SLIPPAGE |
| 1305 | Variant_C_MA5_2Day | 2026-04-17 | -3.78% | -6.57% | 3 | MA5_BREAK_2D |
| 1305 | Current_MA5 | 2026-04-20 | -0.34% | -2.57% | 2 | MA5_BREAK |
| 1305 | Variant_A_MA10 | 2026-04-20 | -0.34% | -2.57% | 2 | MA10_BREAK |
| 1305 | Variant_B_StopOnly | 2026-04-20 | -10.45% | -21.48% | 11 | STOP_LOSS_SLIPPAGE |
| 1305 | Variant_C_MA5_2Day | 2026-04-20 | -0.68% | -2.47% | 3 | MA5_BREAK_2D |
| 1305 | Current_MA5 | 2026-04-21 | -1.18% | -1.42% | 2 | MA5_BREAK |
| 1305 | Variant_A_MA10 | 2026-04-21 | -1.18% | -1.42% | 2 | MA10_BREAK |
| 1305 | Variant_B_StopOnly | 2026-04-21 | -10.45% | -19.78% | 10 | STOP_LOSS_SLIPPAGE |
| 1305 | Variant_C_MA5_2Day | 2026-04-21 | -2.20% | -6.65% | 6 | MA5_BREAK_2D |
| 1305 | Current_MA5 | 2026-04-22 | -5.59% | -9.67% | 4 | MA5_BREAK |
| 1305 | Variant_A_MA10 | 2026-04-22 | -0.00% | -2.53% | 2 | MA10_BREAK |
| 1305 | Variant_B_StopOnly | 2026-04-22 | -10.45% | -17.65% | 7 | STOP_LOSS_SLIPPAGE |
| 1305 | Variant_C_MA5_2Day | 2026-04-22 | -4.93% | -8.44% | 5 | MA5_BREAK_2D |
| 1305 | Current_MA5 | 2026-04-23 | -5.59% | -9.97% | 3 | MA5_BREAK |
| 1305 | Variant_A_MA10 | 2026-04-23 | -2.30% | -6.94% | 2 | MA10_BREAK |
| 1305 | Variant_B_StopOnly | 2026-04-23 | -10.45% | -17.96% | 6 | STOP_LOSS_SLIPPAGE |
| 1305 | Variant_C_MA5_2Day | 2026-04-23 | -4.93% | -8.74% | 4 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-18 | +28.79% | +32.01% | 10 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-18 | +43.08% | +35.71% | 20 | TIME_20D |
| 3131 | Variant_B_StopOnly | 2026-03-18 | +43.08% | +35.71% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-18 | +45.93% | +37.61% | 19 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-19 | +29.65% | +31.26% | 9 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-19 | +42.04% | +32.43% | 20 | TIME_20D |
| 3131 | Variant_B_StopOnly | 2026-03-19 | +42.04% | +32.43% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-19 | +46.90% | +36.78% | 18 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-20 | +25.75% | +26.23% | 8 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-20 | +44.64% | +31.82% | 20 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-03-20 | +40.77% | +27.96% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-20 | +42.49% | +31.10% | 17 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-24 | +10.15% | +8.78% | 6 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-24 | +26.69% | +11.78% | 18 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-03-24 | +18.42% | +3.18% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-24 | +24.81% | +11.35% | 15 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-26 | +10.57% | +10.84% | 4 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-26 | +27.17% | +14.13% | 16 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-03-26 | +8.49% | -10.60% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-26 | +25.28% | +13.67% | 13 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-27 | +4.27% | +3.77% | 3 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-27 | +19.93% | +6.01% | 15 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-03-27 | +2.14% | -17.59% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-27 | +18.15% | +5.66% | 12 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-03-30 | +0.95% | -1.38% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-03-30 | +16.11% | +0.11% | 14 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-03-30 | -1.12% | -22.36% | 20 | TIME_20D |
| 3131 | Variant_C_MA5_2Day | 2026-03-30 | +14.38% | -0.15% | 11 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-04-08 | -7.73% | -9.67% | 3 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-08 | +0.15% | -7.97% | 9 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-08 | -10.45% | -22.39% | 12 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-08 | -1.34% | -8.10% | 6 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-04-09 | -3.27% | -4.72% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-09 | +4.98% | -2.61% | 8 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-09 | -10.45% | -23.80% | 12 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-09 | +3.43% | -2.81% | 5 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-04-13 | -7.78% | -10.98% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-13 | -10.45% | -13.65% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_B_StopOnly | 2026-04-13 | -10.45% | -13.65% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-13 | -10.45% | -13.65% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-17 | +0.90% | -1.15% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-17 | +0.90% | -1.15% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-17 | -10.45% | -16.10% | 5 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-17 | -10.45% | -16.10% | 5 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-20 | -6.82% | -11.90% | 4 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-20 | -6.82% | -11.90% | 4 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-20 | -10.45% | -15.53% | 4 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-20 | -10.45% | -15.53% | 4 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-21 | -10.45% | -10.68% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_A_MA10 | 2026-04-21 | -10.45% | -10.68% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_B_StopOnly | 2026-04-21 | -10.45% | -10.68% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-21 | -10.45% | -10.68% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-22 | -10.45% | -9.77% | 1 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_A_MA10 | 2026-04-22 | -10.45% | -9.77% | 1 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_B_StopOnly | 2026-04-22 | -10.45% | -9.77% | 1 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-22 | -10.45% | -9.77% | 1 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-23 | -0.32% | -4.95% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-23 | -0.32% | -4.95% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-23 | -10.45% | -15.08% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-23 | -10.45% | -15.08% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-24 | -7.99% | -7.14% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-24 | -7.99% | -7.14% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-24 | -10.45% | -9.61% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Variant_C_MA5_2Day | 2026-04-24 | -10.45% | -9.61% | 2 | STOP_LOSS_SLIPPAGE |
| 3131 | Current_MA5 | 2026-04-27 | +0.52% | +1.10% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-27 | +0.52% | +1.10% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-27 | +8.33% | +2.26% | 7 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-04-27 | +0.52% | +2.05% | 3 | MA5_BREAK_2D |
| 3131 | Current_MA5 | 2026-04-28 | +0.00% | +0.64% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-28 | +0.00% | +0.64% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-28 | +7.77% | +0.73% | 6 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-04-28 | +7.77% | +0.73% | 6 | OPEN_MTM |
| 3131 | Current_MA5 | 2026-04-29 | +3.28% | -0.68% | 4 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-29 | +4.15% | +1.28% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-29 | +7.77% | +1.80% | 5 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-04-29 | +7.77% | +1.80% | 5 | OPEN_MTM |
| 3131 | Current_MA5 | 2026-04-30 | -2.21% | -7.08% | 3 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-04-30 | -4.17% | -8.10% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-04-30 | +2.04% | -4.85% | 4 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-04-30 | +2.04% | -4.85% | 4 | OPEN_MTM |
| 3131 | Current_MA5 | 2026-05-04 | +2.05% | +0.99% | 2 | MA5_BREAK |
| 3131 | Variant_A_MA10 | 2026-05-04 | +2.05% | +0.99% | 2 | MA10_BREAK |
| 3131 | Variant_B_StopOnly | 2026-05-04 | +6.48% | +3.47% | 3 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-05-04 | +6.48% | +3.47% | 3 | OPEN_MTM |
| 3131 | Current_MA5 | 2026-05-05 | +0.81% | -1.51% | 2 | OPEN_MTM |
| 3131 | Variant_A_MA10 | 2026-05-05 | +0.81% | -1.51% | 2 | OPEN_MTM |
| 3131 | Variant_B_StopOnly | 2026-05-05 | +0.81% | -1.51% | 2 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-05-05 | +0.81% | -1.51% | 2 | OPEN_MTM |
| 3131 | Current_MA5 | 2026-05-06 | -2.50% | -4.13% | 1 | OPEN_MTM |
| 3131 | Variant_A_MA10 | 2026-05-06 | -2.50% | -4.13% | 1 | OPEN_MTM |
| 3131 | Variant_B_StopOnly | 2026-05-06 | -2.50% | -4.13% | 1 | OPEN_MTM |
| 3131 | Variant_C_MA5_2Day | 2026-05-06 | -2.50% | -4.13% | 1 | OPEN_MTM |
| 6568 | Current_MA5 | 2026-04-28 | -1.20% | -0.56% | 2 | MA5_BREAK |
| 6568 | Variant_A_MA10 | 2026-04-28 | -1.20% | -0.56% | 2 | MA10_BREAK |
| 6568 | Variant_B_StopOnly | 2026-04-28 | -2.00% | -9.04% | 6 | OPEN_MTM |
| 6568 | Variant_C_MA5_2Day | 2026-04-28 | -0.80% | -4.70% | 3 | MA5_BREAK_2D |
| 6568 | Current_MA5 | 2026-04-30 | -2.20% | -6.12% | 2 | MA5_BREAK |
| 6568 | Variant_A_MA10 | 2026-04-30 | -2.20% | -6.12% | 2 | MA10_BREAK |
| 6568 | Variant_B_StopOnly | 2026-04-30 | -2.20% | -9.09% | 4 | OPEN_MTM |
| 6568 | Variant_C_MA5_2Day | 2026-04-30 | -2.20% | -9.09% | 4 | OPEN_MTM |
| 6568 | Current_MA5 | 2026-05-04 | +0.20% | -2.81% | 3 | MA5_BREAK |
| 6568 | Variant_A_MA10 | 2026-05-04 | +0.20% | -2.81% | 3 | MA10_BREAK |
| 6568 | Variant_B_StopOnly | 2026-05-04 | +0.00% | -3.01% | 3 | OPEN_MTM |
| 6568 | Variant_C_MA5_2Day | 2026-05-04 | +0.00% | -3.01% | 3 | OPEN_MTM |
| 6568 | Current_MA5 | 2026-05-05 | -7.53% | -9.85% | 2 | MA5_BREAK |
| 6568 | Variant_A_MA10 | 2026-05-05 | -7.53% | -9.85% | 2 | MA10_BREAK |
| 6568 | Variant_B_StopOnly | 2026-05-05 | -7.72% | -10.04% | 2 | OPEN_MTM |
| 6568 | Variant_C_MA5_2Day | 2026-05-05 | -7.72% | -10.04% | 2 | OPEN_MTM |
| 6568 | Current_MA5 | 2026-05-06 | -0.20% | -1.84% | 1 | OPEN_MTM |
| 6568 | Variant_A_MA10 | 2026-05-06 | -0.20% | -1.84% | 1 | OPEN_MTM |
| 6568 | Variant_B_StopOnly | 2026-05-06 | -0.20% | -1.84% | 1 | OPEN_MTM |
| 6568 | Variant_C_MA5_2Day | 2026-05-06 | -0.20% | -1.84% | 1 | OPEN_MTM |

## Notes

- Existing saved production prediction artifacts only cover the actual artifact range above; 2024-2025 daily prediction CSVs are not present in this workspace.
- Incomplete 20D windows are included as `OPEN_MTM` when requested, so latest May rows are diagnostic, not final realized exits.
- Stop-loss simulation reuses production `simulate_intraday_stop`, including gap-stop and stop-loss slippage behavior.
