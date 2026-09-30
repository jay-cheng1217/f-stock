---
name: model-artifact-protection
description: Protect operational model artifacts from cleanup or deletion.
---

# Model Artifact Protection

Use this skill before any cleanup, archive pruning, disk-space recovery, or file
deletion task that touches `ml/models`, `archive/model_pins`, model manifests, or
weekly retrain outputs.

## Hard Rule

Do not delete model artifacts produced by `TW_Stock_Weekly_Retrain` or used by
live prediction, model selection, pins, incident forensics, or A/B reports.

Protected artifacts include:

- `ml/models/lgbm*.txt`
- `ml/models/lgbm*_meta.json`
- `ml/models/model_selection.json`
- `ml/models/registry.json`
- `config/champion_pin_manifest.yaml`
- `archive/model_pins/*.sha256.txt`

## Required Precheck

Before deleting anything under `ml/models` or `archive/model_pins`, run:

```powershell
python scripts/model_cleanup_precheck.py <candidate paths>
```

If the precheck prints `ABORT`, stop immediately. Do not bypass it by deleting
files manually.

## Retiring Models

Model retirement is not routine cleanup. It requires an explicit PM/SA decision,
replacement pins or `model_selection.json` updates, registry validation, and a
same-snapshot A/B closure artifact when production behavior can change.
