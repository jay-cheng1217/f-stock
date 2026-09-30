# Model Artifact Retention Runbook

## Why This Exists

`TW_Stock_Weekly_Retrain` writes operational model bundles under `ml/models`.
Those files may be untracked because model binaries are large, but they are not
disposable cache files. Removing one can break the morning V2 pipeline if
`model_selection.json`, the pin registry, incident reports, or A/B tooling still
reference it.

## Protected Files

Normal cleanup must not delete:

- `ml/models/lgbm*.txt`
- `ml/models/lgbm*_meta.json`
- `ml/models/model_selection.json`
- `ml/models/registry.json`
- `config/champion_pin_manifest.yaml`
- `archive/model_pins/*.sha256.txt`

This includes weekly retrain outputs that are not currently promoted. They may
still be needed for comparison, rollback, incident reconstruction, or future
pinning.

## Cleanup Procedure

Before deleting anything under `ml/models` or `archive/model_pins`, run:

```powershell
python scripts/model_cleanup_precheck.py <candidate paths>
```

If the command prints `ABORT`, do not delete the file or directory.

## Model Retirement Procedure

Retiring a model is a controlled change, not disk cleanup.

Required steps:

1. Confirm no live `model_selection.json` slot references the meta file.
2. Confirm the model is not listed in `config/champion_pin_manifest.yaml` or
   `ml/models/registry.json`.
3. Confirm no active incident, A/B report, or PM-signed audit trail depends on
   the artifact.
4. If production behavior can change, produce the required same-snapshot A/B
   closure artifact before changing pins or guardrails.
5. Commit the manifest or documentation update that records the retirement.

When in doubt, keep the model bundle.
