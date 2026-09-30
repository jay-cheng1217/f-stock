# DataA output provenance and canonical consumer contract

Authorized scope: certify successful DataA inference and reject unverified current outputs. This is artifact integrity, not a change to model mathematics, rere conditions, entry gates, ranking, or position rules. Root owns formal rebuilding, deployment and serial commits. The implementation was verified using isolated fixtures; no production inference, ledger write or source promotion was executed by this subtask.

Before loading the scoring metadata, model or snapshot, `shadow_dataA_tracker.record` resolves the fixed model path and calls the shared `capture_run('dataA', model_paths=...)`. It then reloads the captured metadata. A cache-based run must obtain its frame from `snapshot_lineage.load_snapshot`; the manifest must remain unchanged across loading. A refresh run clears source memo caches before constructing its own frame and leaves the shared canonical cache untouched. Both branches bind the actual in-memory frame and revalidate the captured source/model inputs before scoring. Only successful `score_snapshot` output reaches the shared atomic CSV/certificate publisher. `predictions_only=True` still returns before any ledger write.

The frame's newly serialized SHA and the cache file's SHA are distinct receipts. Pickle load/save can change bytes without changing the DataFrame, so they are not asserted equal. The verified cache loader checks its original serialized bytes; `bind_snapshot` separately records the actual scoring frame and retains the original manifest as provenance. A failure during scoring never adds current evidence to an old CSV. When auxiliary sources have changed, the old CSV/certificate pair remains untouched and its current reader validation rejects it.

Canonical candidate loading calls `load_prediction_csv` for the selected DataA or production slot and uses the returned frame, preserving the former pandas parser options. An invalid preferred artifact raises an explicit data error; it does not trigger a model fallback or an empty candidate list. A missing snapshot certificate also raises. Existing filename selection policy for a genuinely absent DataA file is unchanged; any selected production artifact still needs its own valid certificate. All source-date extraction, recommendation derivation, operating-margin handling and downstream strategy functions retain their existing semantics.

The formal output auditor now validates both production and DataA certificates and records their manifests. It passes `float_precision='round_trip'` to the trusted reader, checks CSV/manifest receipts across validation, and compares the returned frames with the snapshot. DataA's `source_date` remains valid and conflicting date aliases fail.

Validation command:

```powershell
python -B -m pytest tests/test_dataa_prediction_provenance.py tests/test_formal_prediction_provenance.py tests/test_formal_data_consistency.py tests/test_generate_entry_candidates.py tests/test_strategy_pipeline_integrity.py tests/test_shadow_dataA_contract.py -q
```

Result: 71 passed. Fixtures cover real certificate creation/loading against temporary source/model files, same-date auxiliary correction with unchanged Close, scoring failure preserving old CSV/certificate/ledger, refresh ordering and shared-cache preservation, invalid preferred DataA rejection, missing certificates, alias conflicts, and receipt replacement during audit. Booster arithmetic is isolated by a fixture; this result does not claim a production model replay. Prior cache fixtures now create actual lineage certificates instead of bypassing the production guard.

Before editing, the ASTs of `score_snapshot`, the complete `generate` function, `_technical`, `_pattern_ok`, `_rere_lane_ok`, and `_apply_data_quality` were hashed. All six remain identical; regression hashes are in `tests/test_dataa_prediction_provenance.py`. Consequently this change adds no model-negative-score or missing-operating-margin veto to rere. A final certified DataA scoring run and formal output audit remain root's acceptance steps after all shared provenance code is stable.
