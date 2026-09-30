# Execution-Aware Classifier Smoke - 2026-04-24

## Scope

- Script: `scripts/train_v2_alpha_classifier.py`
- Dataset window: latest `dataset_cache_*.parquet`, tail `600,000` rows
- Device: CPU
- Save mode: `--no-save`
- Comparison:
  - `target_mode=execution_adjusted`
  - `target_mode=vanilla`

## Research Settings

- `target_buffer = 0.005`
- `gap_penalty_multiplier = 1.0`
- `foreign_selling_floor = 0.10`
- `quality_min_roa = 0.0`
- `quality_min_operating_margin = 0.0`
- positive-label depoisoning enabled

## Sample Preparation

### Execution-Aware

- Samples before context gates: `583,395`
- Samples after context gates: `325,665`
- Positive ratio before depoisoning: `32.63%`
- Positive ratio after depoisoning: `21.02%`
- Positive label flips: `37,828`

### Vanilla

- Samples before context gates: `583,395`
- Samples after context gates: `326,343`
- Positive ratio before depoisoning: `34.41%`
- Positive ratio after depoisoning: `22.19%`
- Positive label flips: `39,884`

## Summary Table

| mode | avg AP | avg AUC | avg top30 precision | avg top30 adjusted margin | avg top30 raw alpha |
| --- | ---: | ---: | ---: | ---: | ---: |
| execution_adjusted | 0.3492 | 0.7340 | 37.25% | -0.88% | +0.22% |
| vanilla | 0.3701 | 0.7419 | 36.52% | -0.88% | -0.38% |

## Readout

1. Execution-aware did not beat vanilla on classifier metrics alone (`AP`, `AUC`).
2. Execution-aware did improve the realized Top30 raw alpha on the same window.
3. Top30 adjusted margin remained weak in both settings, so this is not ready for promotion.
4. The right next artifact is a fixed-snapshot A/B:
   - same raw feature frame
   - prediction join by `ticker` + `date`
   - `prob_edge` deltas
   - replay side-by-side with the same ranking population

## Commands

```powershell
python scripts/train_v2_alpha_classifier.py --device cpu --no-save --target-mode execution_adjusted --max-rows 600000
python scripts/train_v2_alpha_classifier.py --device cpu --no-save --target-mode vanilla --max-rows 600000
```
