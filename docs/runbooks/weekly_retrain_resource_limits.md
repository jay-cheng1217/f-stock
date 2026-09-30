# Weekly Retrain Resource Limits

`TW_Stock_Weekly_Retrain` already uses GPU for the V2 LightGBM regression
trainer when the installed LightGBM build supports it. GPU training still uses
CPU for dataset building, binning, validation, reporting, prediction, and ledger
updates, so CPU temperature can remain high even when the model trainer is on
GPU.

## Default Policy

LightGBM training must use a finite CPU thread cap instead of `n_jobs=-1`.

Default:

```powershell
STOCK_LGBM_THREADS=8
```

The shared resolver also accepts `LGBM_NUM_THREADS` as a fallback name. If either
value is missing, non-numeric, zero, or negative, the code falls back to `8`.

## Tuning

Lower this value if CPU temperature is too high during Saturday retrain:

```powershell
$env:STOCK_LGBM_THREADS = "6"
python scripts/run_weekly_retrain_guard.py --dry-run
```

Raise it only after confirming CPU temperature is stable.

## Scope

The cap applies to the main operational LightGBM trainers:

- legacy V1 classifier in `ml/train.py`
- V2 regression in `scripts/train_v2.py`
- V2 alpha classifier research trainer
- T+1 backtest/retrain trainer
- two-stage and audit paths that inherit `scripts/train_v2.V2_PARAMS`

This setting does not eliminate CPU work in pandas, SQLite/DuckDB, report
generation, prediction, or email rendering.

## Production Safety

Changing this cap affects future training resource usage. It does not retrain or
promote a model by itself. Do not claim model-quality closure from this setting;
any production model or feature-processor incident still requires the fixed
snapshot A/B closure artifact before changing production model rules or
guardrails.
