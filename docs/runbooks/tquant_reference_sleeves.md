# TQuant Reference Sleeves Runbook

Purpose: add an external, inspectable strategy reference layer inspired by
`tejtw/TQuant-Lab` without copying its framework wholesale.

The first implementation was offline/read-only:

- It reads existing `unified_signals_*.csv`, `stock.duckdb`, and monthly revenue CSVs.
- It writes replay artifacts under `ml/reports`.
- It does not mutate production gates, model selection, schedulers, DB schema,
  protected model artifacts, or paper portfolios.

As of 2026-05-31 user/PM direction, the same reference layer is promoted to a
production 20D entry gate inside `scripts/build_unified_signals.py`. It still
does not replace the pinned LightGBM model artifact or `model_selection.json`;
it replaces the previous `ENTRY_BENCHMARK_GUARD` hard-block role for the formal
20D entry leg. The older benchmark columns remain in the output for audit, but
when the TQuant gate is enabled they are advisory-only and
`entry_benchmark_guard_enforced=False`.

## Sleeves

- `VAM`: 21D return divided by 21D daily-return volatility. This catches cleaner
  momentum and penalizes high-volatility fake strength.
- `MRAT`: MA21 / MA200 distance plus price-action checks: close still near the
  20D high, higher-low structure, and recent range compression.
- `BAZ`: three-timescale EWM MACD response score from the TQuant LambdaMART
  example.
- `EXP_MOM`: log-price slope weighted by R-squared, inspired by the Expanded
  Momentum Model.
- `KD` / `RSI` / `AROON`: TEJ/TQuant technical confirmation votes. KD catches
  oversold or fresh K>D reversals, RSI requires positive 3-day slope, and Aroon
  requires a recent high with no recent low.
- `REVENUE`: point-in-time monthly revenue YoY and 3M YoY both positive.
- `FIN_QUALITY` / `FIN_TURN` / `VALUE`: conservative fundamental votes from
  local financials and valuation CSVs. Quarterly financials use a fixed
  75-calendar-day availability lag because the local table has fiscal quarter
  but not announcement timestamp.
- `INST`: recent institution net buying from local `daily_k` data.
- Risk flags: financing crowding, settlement/high-beta risk, high beta, gap
  risk, KD overheat, YoY margin deterioration, valuation risk, and sector
  crowding.
- Factor validation: replay reports compute FactorLibrary-style diagnostics
  using MAD5 winsorization, per-date z-score, sector/log-amount neutralization,
  Spearman RankIC, and top-minus-bottom quintile spread. This is diagnostic
  only; it does not mutate the pinned LightGBM model.

## Run

```powershell
python scripts\tquant_reference_sleeves.py --start-date 2026-04-01 --end-date 2026-05-22 --horizon-days 5
```

Optional mature 20D replay:

```powershell
python scripts\tquant_reference_sleeves.py --start-date 2026-04-01 --end-date 2026-04-29 --horizon-days 20 --output-prefix tquant_reference_sleeves_20d_latest
```

Outputs:

- `ml/reports/tquant_reference_sleeves_latest.md`
- `ml/reports/tquant_reference_sleeves_latest.json`
- `ml/reports/tquant_reference_sleeves_latest_candidates.csv`

## Production Gate

Production behavior:

- `strong`: at least 6 support votes and at most 2 risk flags.
- `watch`: at least 3 support votes and at most 2 risk flags.
- `reject`: insufficient independent support or too many risk flags.
- Default production setting: `TQUANT_REFERENCE_GATE_ENABLED=1`.
- Default minimum consensus: `TQUANT_REFERENCE_GATE_MIN_CONSENSUS=strong`,
  meaning only `strong` passes the production 20D leg. This setting is based
  on the 2026-05-31 enhanced TEJ replay where `support>=6` and `risk<=2`
  improved both the 5D and mature 20D windows while still leaving live entries.
- Looser mode: set `TQUANT_REFERENCE_GATE_MIN_CONSENSUS=watch` to allow both
  `watch` and `strong`.
- Rollback: set `TQUANT_REFERENCE_GATE_ENABLED=0`.
- Replacement behavior: while enabled, `ENTRY_BENCHMARK_GUARD` is not allowed to
  block 20D entries; it is retained only as an audit/reference score.

When a 20D-only row is `reject`, the formal signal becomes `NONE` and
`tradability_reason` includes `TQUANT_REFERENCE_GATE`. When a `Dual` row is
`reject`, the 20D leg is removed and the signal is downgraded to `T1_only` if
the T+1 leg is still present.

The output CSV includes auditable columns prefixed with `tquant_` plus:

- `tquant_reference_gate_enabled`
- `tquant_reference_gate_version`
- `tquant_reference_gate_min_consensus`
- `tquant_reference_gate_pass`
- `tquant_reference_gate_error`

The gate is fail-open only for infrastructure errors while recording
`tquant_reference_gate_error`; strategy `reject` decisions are fail-closed for
the 20D leg.
