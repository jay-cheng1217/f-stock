# DIAG-002 Entry Gate Audit

- Generated at: `2026-05-09T17:03:43`
- Signal file: `ml\models\unified_signals_2026-05-08.csv`
- Ticker trace: `4577`

## Production Entry Gates

| Gate | Code line | Condition | Action |
|---|---|---|---|
| 20D candidate filter / sector cap | `ml.predict.apply_sector_cap, build_unified_signals.py:716` | recommendation in buy set, institutional sell pressure filter, max 20% per sector in Top30 | selection only; non-selected names never reach unified entry artifact |
| Penalty overlay hard block | `build_unified_signals.py:716` | penalty_overlay enabled and penalty_hard_block == true | removed before Top30 candidate selection |
| T+1 risk tag block | `build_unified_signals.py:994` | T+1 risk_tags or dual 20D hard risk tags map to a hard block reason | drop T1-only or downgrade Dual to 20D-only |
| EXPENSIVE_MOMENTUM_RISK | `build_unified_signals.py:1037` | PE > 50 and price_vs_ma60 > 25% | cap target_weight_ratio to 3%; not a full block |
| LOW_LIQUIDITY | `build_unified_signals.py:1085` | 20D avg_20d_volume < 500 or avg_20d_amount < 20000000 | full block to signal_type NONE |
| T1_LIQUIDITY_BLOCKED | `build_unified_signals.py:1085` | T1 avg_5d_volume < 1000 or avg_5d_amount < 30000000 | block T1-only or downgrade Dual to 20D-only |
| OVERHEAT_RISK / HIGH_BETA_RISK / MISSING_RISK_CONTEXT | `build_unified_signals.py:1147` | price_vs_ma60 > sector threshold; beta_60 > 1.80; or missing price_vs_ma60/beta context | full block to signal_type NONE |
| LOW_ALPHA_WIN_PROB | `build_unified_signals.py:1207` | ALPHA_WIN_GATE_MODE=disabled; threshold/prob/rank based when enabled | full block or Dual downgrade; currently disabled unless env enables it |
| MARKET_REGIME_CLOSED / MARKET_REGIME_CAUTION | `build_unified_signals.py:1245` | market regime action BLOCK, or LIMIT_20D_TOP10_AND_BLOCK_T1 with rank_20d > 10 | full block to signal_type NONE |
| DISPOSITION_PERIOD | `build_unified_signals.py:1316` | ticker is in official disposition-period list for planned entry date | full block to signal_type NONE |

## Missing Technical Entry Checks

- price_vs_ma5_at_entry / entry below MA5
- MA5 slope or MA5 direction
- MA10/MA20 slope confirmation
- MACD histogram direction / MACD cross state
- volume trend confirmation beyond absolute liquidity floors
- institutional accumulation confirmation or sell-pressure veto at final gate
- gap-down / weak open filter at entry execution time

## 4577 Gate Trace

| Gate | Result | Detail |
|---|---|---|
| candidate_selection | PASS | rank_20d=5 recommendation=建議買進 |
| expensive_momentum | PASS | pe=None price_vs_ma60=30.13% target_weight=16.67% |
| liquidity | PASS | avg20_volume=1722.3297 avg20_amount=261285868.85 |
| overheat_beta_context | PASS | price_vs_ma60=30.13% threshold=40.00% beta_60=1.1115417 group=electronics_other |
| alpha_win_prob | PASS_OR_DISABLED | mode=disabled threshold=nan |
| market_regime | PASS | state=CAUTION action=LIMIT_20D_TOP10_AND_BLOCK_T1 rank_20d=5 |
| disposition | PASS | is_disposition=False |
| final_signal | 20D_only | tradability_status=OPEN reasons=- |
