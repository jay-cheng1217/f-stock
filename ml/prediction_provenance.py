"""Certificates for predictions produced by a completed, verified scoring run.

Capture inputs before loading/constructing them, bind the actual scoring frame,
then publish only after scoring succeeds. This module never certifies an old CSV
using current sources. Source-stat reuse has the same contract as snapshot_lineage;
loaded model files and the CSV additionally receive content-hash verification.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import tempfile

from ml import snapshot_lineage as lineage

SCHEMA = 1


def _prediction_patterns(model_paths):
    from ml.config import BASE_DIR, MODEL_DIR
    base = Path(BASE_DIR)
    code = Path(__file__).resolve().parents[1]
    patterns = [str(code / name) for name in (
        "ml/predict.py", "ml/prediction_provenance.py", "ml/thresholds.py",
        "ml/model_selection.py", "ml/two_stage_model.py", "ml/entry_shortwave.py",
        "ml/chipk.py", "backend/services/macro_event_service.py")]
    if "dataA" in model_paths:
        patterns.append(str(code / "scripts/shadow_dataA_tracker.py"))
    else:
        # Only the two documented non-secret market-data files used by ChipK.
        from ml.chipk import _appviewer_root, CHIPK_MAIN_FORCE_FILENAME, CHIPK_DATE_RELATIVE
        viewer = _appviewer_root()
        patterns += [str(viewer / "*" / "UBSKChart" / CHIPK_MAIN_FORCE_FILENAME),
                     str(viewer / "*" / "UBSKChart" / CHIPK_DATE_RELATIVE)]
    patterns += [str(base / "disposition_active.csv"),
                 str(base / "ml/data/external_sentiment/*.json"),
                 str(Path(MODEL_DIR) / "model_selection.json")]
    patterns += [str(Path(MODEL_DIR) / pattern) for pattern in (
        "lgbm_[0-9]*_meta.json", "lgbm_v2_*_meta.json",
        "lgbm_alpha_cls_v2_*_meta.json", "lgbm_two_stage_ranker_*_meta.json")]
    patterns += [str(path) for spec in model_paths.values() for path in spec.values()]
    return sorted(set(os.path.abspath(path) for path in patterns))


def capture_run(model_slot: str, *, model_paths: dict, source_patterns=None, context=None) -> dict:
    """Freeze actual metadata/weight paths before loading the model or snapshot."""
    if not model_slot or not model_paths:
        raise ValueError("Prediction provenance requires a slot and actual model artifacts")
    normalized = {}
    for role, spec in model_paths.items():
        if not {"meta_path", "model_path"}.issubset(spec):
            raise ValueError("Each model requires metadata and weights")
        normalized[role] = {key: str(Path(spec[key]).resolve()) for key in ("meta_path", "model_path")}
        for path in normalized[role].values():
            if not Path(path).is_file():
                raise FileNotFoundError(path)
    sources = lineage.source_state(include_hashes=True, patterns=source_patterns)
    dependencies = lineage.source_state(include_hashes=True, patterns=_prediction_patterns(normalized))
    by_path = {item["path"]: item for item in dependencies["files"]}
    models = {role: {key: copy.deepcopy(by_path[path]) for key, path in spec.items()}
              for role, spec in normalized.items()}
    run = {"schema": SCHEMA, "scope": "completed_prediction_run", "model_slot": model_slot,
           "started_at": datetime.now(timezone.utc).isoformat(), "sources": sources,
           "prediction_dependencies": dependencies, "models": models,
           "context": context or {}, "snapshot": None}
    assert_run_unchanged(run)
    return run


def assert_run_unchanged(run: dict) -> None:
    if run.get("schema") != SCHEMA or not run.get("models"):
        raise ValueError("Invalid prediction input evidence")
    lineage.assert_sources_unchanged(run["sources"])
    lineage.assert_sources_unchanged(run["prediction_dependencies"])
    dependencies = {item["path"]: item for item in run["prediction_dependencies"]["files"]}
    for spec in run["models"].values():
        if set(spec) != {"meta_path", "model_path"}:
            raise ValueError("Prediction evidence must retain metadata and weights")
        for artifact in spec.values():
            if dependencies.get(artifact["path"]) != artifact:
                raise ValueError("Model evidence is not bound to prediction dependencies")
            if lineage._file_hash(artifact["path"]) != artifact["sha256"]:
                raise RuntimeError("Loaded prediction model artifact changed")


def bind_snapshot(run: dict, snapshot, *, snapshot_manifest=None) -> dict:
    """Bind the exact in-memory frame supplied to scoring, before model math."""
    if run.get("snapshot") is not None:
        raise ValueError("A run cannot be rebound to a different snapshot")
    assert_run_unchanged(run)
    buffer = io.BytesIO()
    snapshot.to_pickle(buffer, protocol=pickle.HIGHEST_PROTOCOL)
    bound = copy.deepcopy(run)
    bound["snapshot"] = {"sha256": hashlib.sha256(buffer.getvalue()).hexdigest(),
                         "rows": int(len(snapshot)), "serialization": "pandas_pickle_highest_protocol",
                         "source_manifest": copy.deepcopy(snapshot_manifest)}
    assert_run_unchanged(bound)
    return bound


def publish_prediction_csv(frame, path, run: dict, *, csv_kwargs=None) -> dict:
    """Atomically publish CSV then its certificate after successful scoring."""
    if not run.get("snapshot") or not run["sources"].get("content_signature"):
        raise ValueError("Only a completed run with bound snapshot/source evidence may publish")
    assert_run_unchanged(run)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary, meta_tmp = Path(name), Path(name+".json")
    options = {"index": False, "encoding": "utf-8-sig"}
    options.update(csv_kwargs or {})
    try:
        frame.to_csv(temporary, **options)
        certificate = copy.deepcopy(run)
        certificate.update(completed_at=datetime.now(timezone.utc).isoformat(),
                           csv_sha256=lineage._file_hash(temporary), rows=int(len(frame)))
        meta_tmp.write_text(json.dumps(certificate, ensure_ascii=False, indent=2), encoding="utf-8")
        assert_run_unchanged(run)
        os.replace(temporary, path)
        os.replace(meta_tmp, Path(str(path)+".manifest.json"))
        return certificate
    finally:
        temporary.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)


def load_prediction_csv(path, *, expected_model_slot="production", read_csv_kwargs=None):
    """Read certified CSV bytes once; missing/stale/incomplete pairs reject."""
    import pandas as pd
    path = Path(path)
    try:
        certificate = json.loads(Path(str(path)+".manifest.json").read_text(encoding="utf-8"))
        if (certificate.get("schema") != SCHEMA or certificate.get("scope") != "completed_prediction_run"
                or certificate.get("model_slot") != expected_model_slot or not certificate.get("snapshot")):
            return None
        if (len(certificate["snapshot"].get("sha256", "")) != 64
                or not isinstance(certificate["snapshot"].get("rows"), int)):
            return None
        for state in (certificate["sources"], certificate["prediction_dependencies"]):
            if (state.get("content_signature") != lineage._json_hash(state["files"])
                    or any(len(item.get("sha256", "")) != 64 for item in state["files"])):
                return None
        if lineage.source_state()["signature"] != certificate["sources"]["signature"]:
            return None
        assert_run_unchanged(certificate)
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != certificate["csv_sha256"]:
            return None
        options = {"dtype": {"ticker": str}}
        options.update(read_csv_kwargs or {})
        frame = pd.read_csv(io.BytesIO(blob), **options)
        if len(frame) != certificate["rows"]:
            return None
        assert_run_unchanged(certificate)
        return frame, certificate
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, EOFError):
        return None
