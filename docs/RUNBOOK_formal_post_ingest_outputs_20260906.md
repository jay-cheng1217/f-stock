# Formal post-ingest output rebuild: 2026-09-04 close / 2026-09-07 plan

This is a reviewed command sequence, not an execution receipt. Root owns execution, backups, notifications, and final acceptance. Run from `F:\stock` after final source promotion, successful ingest, and the newly certified canonical 20D snapshot/predictions. Keep source files and model pins stable throughout. Do not run the complete daily/nightly pipeline to perform this bounded rebuild.

The source close is 2026-09-04. A snapshot or prediction row keeps its company's actual source date; a batch filename dated September 4 does not turn a stopped/quiet stock's older row into September 4 data. The entry plan trade date is 2026-09-07. This is a current-source rebuild, not a historical knowledge-time replay.

Before an independent production 20D run, call `scripts.smart_update_auto._enable_two_stage_champion_defaults()` in the same process, exactly as the nightly entry point does. The bare prediction loader retains a latest-V2 fallback for its existing callers. On September 6, the first independent driver omitted those defaults and selected the September 5 weekly V2 without the pinned ranker; the fixed-E comparison correctly failed. Its output/receipt was preserved without Web deployment. The second driver applied the existing validated defaults and produced 1,098 predictions byte-identical to E. Do not change pins to make a comparison pass. The Web lifespan now independently applies the same validated registry defaults and checks its selected model pairs against the prediction certificate.

| Step | Writes and relevant reads | Ledger / training / notification behavior |
| --- | --- | --- |
| Certified snapshot precheck | Reads `snapshot_cache.pkl`, its manifest and source stat signatures | Read-only; rejects missing or changed lineage |
| T1 inference | Replaces `ml/models/snapshot_t1_cache.pkl` and `predictions_t1_2026-09-04.csv`; reads the latest existing T1 model and 20D predictions; reads existing T1 paper book for circuit-breaker state | No training or book writes; SQLite handle is opened with default mode but only SELECT is issued |
| DataA predictions-only | Replaces `ml/models/dataA_predictions_2026-09-04.csv` and its `.manifest.json`; uses the certified canonical snapshot and existing DataA model | `--predictions-only` returns before ledger write; omit `--refresh-snapshot` to preserve shared raw lineage |
| Regime and unified | Replaces `market_regime_latest.json/.md` and `unified_signals_2026-09-04.csv`; reads production model registry, gates and data | No book synchronization; importing `smart_update_auto` opens its dated log for append |
| Sector strength | Replaces `ml/data/sector_strength.json` using daily Close and sector mapping | No model/ledger writes. Existing file format has no as-of date; see limitation below |
| Canonical entry | Atomically replaces `logs/entry_list_20260907.json`, `frontend/static/entry_canonical.json`, and each `.manifest.json` sidecar | Explicit `persist_watchlist=False` and `include_live=False`; no watchlist pruning or network quote request |
| Entry HTML | Replaces `ml/reports/entry_dashboard_latest.html`; reads plan, market context and existing holdings/research artifacts | No sending, publishing, or ledger recording; reads holdings through SELECT |
| Output audit | Writes a new JSON/MD receipt under `output/data_layer_consistency_20260906` | Read-only sources/outputs; nonzero exit on any failed assertion |

Existing model files, metadata, registry and selection are inputs. The two snapshot cache files are derived feature artifacts, not trained model weights. Save before/after hashes of the authorized outputs and all protected books/model pins in the root execution receipt.

## 1. Verify the new 20D snapshot certificate

```powershell
@'
from pathlib import Path
import pandas as pd
from ml.snapshot_lineage import load_snapshot
snapshot = load_snapshot(Path('ml/models/snapshot_cache.pkl'))
if snapshot is None or snapshot.empty:
    raise RuntimeError('Canonical snapshot lineage is absent or stale')
if pd.to_datetime(snapshot['Date']).max().strftime('%Y-%m-%d') != '2026-09-04':
    raise RuntimeError('Unexpected canonical snapshot market date')
if not Path('ml/models/predictions_2026-09-04.csv').is_file():
    raise RuntimeError('New 20D predictions are missing')
print('Certified canonical snapshot:', len(snapshot), 'rows')
'@ | python -B -X utf8 -
if ($LASTEXITCODE -ne 0) { throw 'Snapshot precheck failed' }
```

Root must already have rebuilt and validated the actual 20D predictions; the filename existence check above is only a dependency check. The final output auditor checks their per-ticker dates against the certified snapshot.

## 2. Rebuild the T1 sidecar with failure propagation

Use the callable because the module CLI catches a missing model and prints SKIP with a successful exit. The callable propagates failure. T1 keeps only the actual latest-date cohort; it is a separate product and is not enabled in the Champion unified build below.

```powershell
@'
from pathlib import Path
from ml.predict_t1 import predict_t1_all
predictions, metadata = predict_t1_all(save_csv=True, verbose=True)
if predictions.empty or set(predictions['date'].astype(str)) != {'2026-09-04'}:
    raise RuntimeError('T1 predictions have an invalid source date or are empty')
if Path(metadata['prediction_file']).name != 'predictions_t1_2026-09-04.csv':
    raise RuntimeError('T1 output filename mismatch')
print('T1 source close 2026-09-04:', len(predictions), 'rows')
'@ | python -B -X utf8 -
if ($LASTEXITCODE -ne 0) { throw 'T1 inference failed' }
```

## 3. Rebuild DataA scores from the same raw snapshot

```powershell
python -B -X utf8 scripts/shadow_dataA_tracker.py record --predictions-only --as-of 2026-09-04
if ($LASTEXITCODE -ne 0) { throw 'DataA inference failed' }
```

DataA deliberately uses `source_date`, not `date`. Do not rename it to a batch date or require every company row to be September 4. The formal auditor supports `Date`, `date`, and `source_date`, rejects conflicting aliases, and compares each identity with the snapshot. Bare `record` would write the shadow ledger; `check` would run a separate evaluation and write its reports.

After the P2 provenance repair, DataA captures source/model evidence before loading and scoring, and publishes a certificate only after successful scoring. Both the canonical reader and final auditor require that certificate; they also require a production certificate if a production prediction artifact is selected. Do not manufacture a certificate for pre-existing scores. Rebuild using the real writer after shared source/provenance code has stabilized. See `docs/METHOD_dataa_prediction_provenance_20260906.md`.

## 4. Rebuild the existing nightly Champion unified configuration

The sanctioned production branch in `smart_update_auto.exec_paper_portfolio` uses `disable_t1=True`, with penalty overlay default enabled and version `v1`, respecting existing environment overrides. The bare unified function/CLI and `daily_pipeline.run_unified_signals` have different defaults. Do not use those defaults or call the complete `exec_paper_portfolio`, which continues into book synchronization.

```powershell
@'
import os
from scripts.smart_update_auto import _enable_two_stage_champion_defaults, _env_flag
from ml.market_regime import update_market_regime_report
from scripts.build_unified_signals import build_unified_signals
_enable_two_stage_champion_defaults()
regime = update_market_regime_report(as_of_date='2026-09-04')
if regime['as_of_date'] != '2026-09-04':
    raise RuntimeError('Market regime date mismatch')
overlay = _env_flag('UNIFIED_PENALTY_OVERLAY', True)
version = os.environ.get('UNIFIED_PENALTY_OVERLAY_VERSION', 'v1').strip().lower() or 'v1'
result = build_unified_signals(
    pred_date='2026-09-04',
    prediction_20d_path='ml/models/predictions_2026-09-04.csv',
    penalty_overlay=overlay,
    penalty_overlay_version=version,
    disable_t1=True,
    verbose=True,
)
if result.prediction_date != '2026-09-04' or result.t1_only_count or result.dual_count:
    raise RuntimeError('Unexpected unified date or T1-enabled cohort')
print('Unified output:', result.output_path, 'overlay:', overlay, version)
'@ | python -B -X utf8 -
if ($LASTEXITCODE -ne 0) { throw 'Unified build failed' }
```

An empty/blocked current cohort can be a valid strategy outcome. Preserve and report its gate reason; do not change thresholds to produce candidates. The unified loader retains only current-date predictions for its current cohort, while the original 20D/DataA files retain legitimate older company rows.

## 5. Refresh the existing sector-strength context

```powershell
python -B -X utf8 scripts/sector_strength.py
if ($LASTEXITCODE -ne 0) { throw 'Sector-strength build failed' }
```

This existing calculation reads each four-digit company's last and 21st-last raw Close, requires at least 25 rows and five companies per sector, and writes no date metadata. It does not provide a common-date or corporate-action-adjusted historical certificate. Record its execution time, input source receipt, output SHA and this limitation; this runbook does not change the strategy calculation.

## 6. Build both canonical JSON artifacts without mutating the watchlist

The generator CLI's normal output mode persists/prunes the watchlist; its `--dry` mode does not save the JSON. Use the explicit callable for this bounded publication.

```powershell
@'
from pathlib import Path
from scripts.generate_entry_candidates import generate_certified, to_entry_json, _atomic_json
result = generate_certified(as_of='2026-09-04', trade_date='2026-09-07',
                  persist_watchlist=False, include_live=False)
if not result.get('as_of_close'):
    raise RuntimeError('Canonical model source is missing')
payload = to_entry_json(result, '2026-09-07')
if payload['as_of_date'] != '2026-09-04' or payload['model_source_date'] != '2026-09-04':
    raise RuntimeError('Canonical source date mismatch')
_atomic_json(Path('logs/entry_list_20260907.json'), payload)
_atomic_json(Path('frontend/static/entry_canonical.json'), payload)
print('Canonical plan 2026-09-07:', len(payload['rows']), 'candidates')
'@ | python -B -X utf8 -
if ($LASTEXITCODE -ne 0) { throw 'Canonical entry build failed' }
```

Do not call the nightly canonical-plan wrapper: it also records/checks rere and performs additional workflow actions. Rere's original signal and trading-eligibility conditions remain as implemented by the canonical generator.

The successful generation now has its own proof: `generate_certified` captures actual inputs before calling the unchanged raw generator once, and `_atomic_json` publishes the plan with its generation sidecar. Raw `generate` remains available for research but its converted JSON cannot be published through the formal writer. Existing date-only plans must be regenerated; copying a current prediction certificate onto an older plan is invalid. Readers call `scripts.entry_artifact_lineage.load_entry_artifact(path)` and reject missing/stale plan evidence.

## 7. Rebuild HTML and inspect the final output contract

```powershell
python -B -X utf8 scripts/entry_dashboard.py --date 2026-09-07
if ($LASTEXITCODE -ne 0) { throw 'Entry HTML build failed' }
python -B -X utf8 scripts/audit_formal_data_consistency.py --stage outputs --as-of 2026-09-04 --trade-date 2026-09-07 --output output/data_layer_consistency_20260906/formal_outputs_after_publish_20260906.json
if ($LASTEXITCODE -ne 0) { throw 'Final output consistency failed; inspect JSON/MD receipt' }
```

Use a new audit output filename if that receipt already exists. The audit checks certified snapshot values and each 20D/DataA ticker/date, the plan's source/trade dates, and every candidate represented in HTML `script#data`. Root performs visual/HTTP acceptance and the separately authorized completion notice. These commands send no mail or other notifications and do not start/stop a service.

## Numeric comparison evidence

Ingest uses DuckDB `read_csv_auto(..., header=true, union_by_name=true, filename=true)` and obtains ticker identity from each filename. The auditor reads the literal OHLCV numbers with pandas `float_precision='round_trip'` and compares finite values exactly, without tolerance. On September 6, a separate in-memory parser probe of all 2,031 accepted CSVs' latest OHLCV tokens found zero differences for either pandas default or round-trip parsing against DuckDB. Receipt: `output/data_layer_consistency_20260906/float_precision_probe_20260906/report.json`; token SHA `c0e750decc8c4edbc7f058749f405bf9f6e38ed46858bafd0356308aa3149a6c`. This is a latest-row parser check, not proof that every historical float token has identical parser behavior. The regression fixture deliberately changes a value by one ULP and requires failure.

The separately executed formal DB audit passed 13 checks on 2026-09-06 22:08:40–22:08:54: 2,031 source/stock-list names, 2,990,060 daily rows, zero duplicate rows, zero latest OHLCV mismatches, five identical committed generations, financial max 2026Q2, TWII September 4. Its receipt is `output/data_layer_consistency_20260906/formal_db_after_ingest_20260906.json`. This DB receipt does not claim the later output commands above have been executed.

## Completed bounded execution receipts

The actual pinned 20D run passed at 22:44. T1/DataA/unified/canonical passed in `formal_downstream_pinned_20260906`; the original HTML CLI import failure remains in that run's FAIL summary. A separate `formal_downstream_html_resume_20260906` passed after the direct CLI import-path repair.

After canonical generation and renderer lineage protection was added, root ran `output/data_layer_consistency_20260906/run_final_certified_entry_20260906.py` once under the new `formal_certified_entry_20260906` label. Preflight, a single `generate_certified` call, both JSON/sidecar publications, HTML and all **23** final output checks passed at 23:58. The old model score files, source files, watchlist and protected ledgers stayed unchanged. E18/rere6 order and semantic fields matched exactly. Earlier raw-generator drivers remain preserved execution history and must not be used to publish new uncertified plans.

The final actual Web check is `ml/reports/research/data_layer_consistency_20260906/final_web_acceptance_v4_20260906.json`: 9 GET and 51 comparisons passed at 2026-09-07 00:08, PID 15672. Prior HTTP failures are separate retained receipts. This acceptance includes real model raw/final values, current canonical proof and the served frontend, and does not republish the fixed Claude Artifact. The complete delivery index is `ml/reports/research/data_layer_consistency_20260906/final_data_layer_delivery_20260906.json`.
