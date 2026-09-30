# ChipK Shortwave Entry Flow - SA/RD Decision

Date: 2026-06-18

## Scope

This change adds a shortwave entry diagnostics layer for 20D stock selection:

- MA structure: MA5 / MA20 / MA60 support, slope, overheat, weakness
- Volume confirmation: volume ratio and same-day price confirmation
- Momentum: RSI, MACD, KD, bearish/bullish divergence flags
- ChipK local snapshot: main-force 1D / 5D / 20D score from CMoney local data

The layer is additive by default. It writes diagnostics and reasons into prediction and unified artifacts, but it does not replace the existing production ranking, scheduler, order gate, DB schema, or model artifacts.

## ChipK Data Boundary

Current local ChipK import reads the latest CMoney snapshot only:

- Source: `%APPDATA%\AppViewer\<profile>\UBSKChart\2056757959.txt`
- As-of detected: 2026-06-18
- Rows detected: 2376
- Historical series: not detected

Because no 180-day historical ChipK series is available yet, ChipK fields are not allowed to alter production gates or ranking. They are report/explanation fields until historical import and backtest exist.

## Production Decision

Not promoted to production主控 on 2026-06-18.

Reason: the required "all rules pass" condition was not met.

Backtest summary:

| Horizon | Top N | Control avg | Treatment avg | Delta | Status |
|---|---:|---:|---:|---:|---|
| 5D | 10 | -1.03% | -0.45% | +0.58% | fail |
| 5D | 5 | -1.18% | -1.30% | -0.13% | fail |
| 10D | 10 | -3.42% | +0.94% | +4.36% | pass |
| 10D | 5 | -4.10% | +0.47% | +4.58% | pass |
| 20D | 10 | -0.28% | +3.84% | +4.12% | pass |
| 20D | 5 | -0.00% | +4.69% | +4.70% | pass |

The 10D/20D signal is promising, but the 5D entry behavior is not robust enough to become the primary production entry filter.

## Implemented Artifact Fields

Prediction and unified artifacts can now carry:

- `shortwave_score`
- `shortwave_action`
- `shortwave_reason`
- `shortwave_risk_flags`
- `shortwave_ma_score`
- `shortwave_volume_score`
- `shortwave_momentum_score`
- `shortwave_chipk_score`
- `shortwave_rerank_enabled`
- `chipk_main_force_1d`
- `chipk_main_force_5d`
- `chipk_main_force_20d`
- `chipk_main_force_score`
- `chipk_history_available`

Unified artifacts also preserve the 20D-sourced versions as `*_20d`.

## Promotion Gate

To promote this layer from diagnostics to production主控, require:

1. 5D Top10 treatment average return > 0 and above control.
2. 5D Top5 treatment average return > 0 and above control.
3. 10D and 20D remain positive and above control.
4. ChipK historical import has at least 180 trading days, or ChipK remains excluded from production scoring.
5. Same-snapshot A/B report confirms no unintended model/feature drift if production ranking is changed.

Until those are met, `SHORTWAVE_PROD_RERANK_ENABLED` must stay disabled by default.
