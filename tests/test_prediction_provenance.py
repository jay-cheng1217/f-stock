import ast
import glob
import hashlib
import json
import os
from pathlib import Path
import threading
import types

import pandas as pd
import pytest

from ml import prediction_provenance as provenance
from ml import snapshot_lineage as lineage


@pytest.fixture
def run_inputs(tmp_path, monkeypatch):
    source = tmp_path / "financial.csv"
    source.write_text("Ticker,EPS\n2330,10\n", encoding="utf-8")
    meta, model = tmp_path / "base_meta.json", tmp_path / "base_weights.txt"
    model.write_text("actual model A", encoding="utf-8")
    meta.write_text(json.dumps({"model_file": str(model)}), encoding="utf-8")
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [str(source)])
    monkeypatch.setattr(provenance, "_prediction_patterns", lambda paths: [str(meta), str(model)])
    paths = {"base": {"meta_path": str(meta), "model_path": str(model)}}
    snapshot = pd.DataFrame({"ticker": ["2330"], "Date": ["2026-09-04"], "feature": [1.2345678901234567]})
    run = provenance.capture_run("production", model_paths=paths)
    run = provenance.bind_snapshot(run, snapshot)
    predictions = pd.DataFrame({"ticker": ["2330"], "date": ["2026-09-04"], "close": [100.],
                                "up_prob": [.3], "flat_prob": [.3], "down_prob": [.4], "signal": ["hold"]})
    return source, meta, model, snapshot, run, predictions, tmp_path / "predictions_2026-09-04.csv"


def change_text(path, before, after, *, retain_stat=False):
    stat = path.stat()
    path.write_text(path.read_text(encoding="utf-8").replace(before, after), encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns if retain_stat else stat.st_mtime_ns + 1000000))


def test_successful_certificate_preserves_exact_csv_bytes_and_read_options(run_inputs):
    source, meta, model, snapshot, run, predictions, path = run_inputs
    certificate = provenance.publish_prediction_csv(predictions, path, run)
    expected = path.with_name("original_write.csv")
    predictions.to_csv(expected, index=False, encoding="utf-8-sig")
    assert path.read_bytes() == expected.read_bytes()
    assert certificate["csv_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    loaded, saved = provenance.load_prediction_csv(path, read_csv_kwargs={"float_precision": "round_trip"})
    pd.testing.assert_frame_equal(loaded, pd.read_csv(expected, dtype={"ticker": str}, float_precision="round_trip"))
    assert saved["snapshot"]["rows"] == 1 and len(saved["snapshot"]["sha256"]) == 64


@pytest.mark.parametrize("damage", ["missing_certificate", "source", "model_same_stat", "metadata", "csv", "slot", "missing_weights"])
def test_stale_or_incomplete_predictions_are_rejected(run_inputs, damage):
    source, meta, model, snapshot, run, predictions, path = run_inputs
    provenance.publish_prediction_csv(predictions, path, run)
    if damage == "missing_certificate":
        Path(str(path)+".manifest.json").unlink()
    elif damage == "source":
        change_text(source, ",10", ",11")
    elif damage == "model_same_stat":
        change_text(model, "model A", "model B", retain_stat=True)
    elif damage == "metadata":
        meta.write_text(meta.read_text()+" ", encoding="utf-8")
    elif damage == "csv":
        change_text(path, "100.0", "101.0")
    elif damage == "missing_weights":
        manifest = Path(str(path)+".manifest.json")
        evidence = json.loads(manifest.read_text())
        del evidence["models"]["base"]["model_path"]
        manifest.write_text(json.dumps(evidence), encoding="utf-8")
    else:
        assert provenance.load_prediction_csv(path, expected_model_slot="dataA") is None
        return
    assert provenance.load_prediction_csv(path) is None


def test_source_change_during_csv_serialization_leaves_existing_pair_unchanged(run_inputs, monkeypatch):
    source, meta, model, snapshot, run, predictions, path = run_inputs
    provenance.publish_prediction_csv(predictions, path, run)
    manifest_path = Path(str(path)+".manifest.json")
    before = path.read_bytes(), manifest_path.read_bytes()
    original = pd.DataFrame.to_csv
    def racing(frame, *args, **kwargs):
        result = original(frame, *args, **kwargs)
        change_text(source, ",10", ",11")
        return result
    monkeypatch.setattr(pd.DataFrame, "to_csv", racing)
    with pytest.raises(RuntimeError, match="changed during"):
        provenance.publish_prediction_csv(predictions, path, run)
    assert (path.read_bytes(), manifest_path.read_bytes()) == before
    assert provenance.load_prediction_csv(path) is None


def test_interrupted_pair_publish_fails_closed(run_inputs, monkeypatch):
    source, meta, model, snapshot, run, predictions, path = run_inputs
    provenance.publish_prediction_csv(predictions, path, run)
    predictions.loc[0, "up_prob"] = .4
    original = os.replace
    def interrupted(src, dst):
        if str(dst).endswith(".manifest.json"):
            raise OSError("simulated certificate swap failure")
        return original(src, dst)
    monkeypatch.setattr(provenance.os, "replace", interrupted)
    with pytest.raises(OSError, match="swap failure"):
        provenance.publish_prediction_csv(predictions, path, run)
    assert provenance.load_prediction_csv(path) is None


def test_rebinding_or_unbound_publication_is_rejected(run_inputs):
    source, meta, model, snapshot, run, predictions, path = run_inputs
    with pytest.raises(ValueError, match="rebound"):
        provenance.bind_snapshot(run, snapshot)
    run["snapshot"] = None
    with pytest.raises(ValueError, match="bound snapshot"):
        provenance.publish_prediction_csv(predictions, path, run)
    assert not path.exists()


def actual_function(filename, name, namespace):
    path = Path(os.environ.get("STOCK_PROVENANCE_SOURCE_DIR", Path(__file__).resolve().parents[1])) / filename
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def test_actual_run_prediction_failure_cannot_publish_certificate(run_inputs):
    source, meta_path, model, snapshot, run, predictions, path = run_inputs
    def fail(**kwargs):
        assert kwargs["capture_provenance"] is True
        raise RuntimeError("model inference failed after snapshot")
    function = actual_function("ml/predict.py", "run_prediction", {"predict_all": fail})
    with pytest.raises(RuntimeError, match="inference failed"):
        function(output_path=str(path))
    assert not path.exists() and not Path(str(path)+".manifest.json").exists()


def test_actual_run_prediction_publishes_only_the_returned_run(run_inputs):
    source, meta_path, model, snapshot, run, predictions, path = run_inputs
    def scored(**kwargs):
        assert kwargs["capture_provenance"] is True
        return predictions, {"model_file": str(model), "_prediction_provenance": run}
    namespace = {"predict_all": scored, "os": os, "SECTOR_CAP_RATIO": .2,
                 "get_slot_label": lambda slot: slot, "_safe_print": lambda text: None}
    function = actual_function("ml/predict.py", "run_prediction", namespace)
    result = function(output_path=str(path))
    assert result["output_path"] == str(path)
    assert "_prediction_provenance" not in result["meta"]
    assert provenance.load_prediction_csv(path) is not None


def test_api_does_not_return_previously_cached_csv_after_certificate_rejection(run_inputs):
    *_, predictions, path = run_inputs
    state = {"context_key": "K", "pred_df": predictions.copy(), "meta": {}}
    def unavailable():
        raise RuntimeError("fresh context required")
    namespace = {"_prediction_context_key": lambda: "K", "_PREDICTION_CACHE": state,
                 "_PREDICTION_CACHE_LOCK": threading.Lock(), "_load_predictions_csv_fallback": lambda: (None, None),
                 "_get_prediction_context": unavailable}
    function = actual_function("app.py", "_get_live_prediction_df", namespace)
    with pytest.raises(RuntimeError, match="fresh context required"):
        function()
    assert state["pred_df"] is None


@pytest.mark.parametrize("role,state", [
    ("v2", "empty"), ("v2", "auto"), ("v2", "explicit"),
    ("alpha", "disabled"), ("alpha", "empty"), ("alpha", "auto"),
    ("ranker", "disabled"), ("ranker", "empty"), ("ranker", "auto"), ("ranker", "explicit"),
])
def test_model_path_capture_preserves_existing_selection_rules(tmp_path, monkeypatch, role, state):
    for key in ("V2_MODEL_META", "ENABLE_ALPHA_CLASSIFIER_RESEARCH", "TWO_STAGE_RANKER_ENABLED", "TWO_STAGE_RANKER_META"):
        monkeypatch.delenv(key, raising=False)
    prefix = {"v2": "lgbm_v2", "alpha": "lgbm_alpha_cls_v2", "ranker": "lgbm_two_stage_ranker"}[role]
    names = ("20260901", "20260904") if state != "empty" else ()
    metadata = {"model_file": str(tmp_path / "fixture_weights.txt"), "candidate_size": "17", "label_bins": "3", "top_n": "4"}
    for stamp in names:
        (tmp_path / f"{prefix}_{stamp}_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    if role == "alpha" and state != "disabled":
        monkeypatch.setenv("ENABLE_ALPHA_CLASSIFIER_RESEARCH", " true ")
    if role == "ranker" and state != "disabled":
        monkeypatch.setenv("TWO_STAGE_RANKER_ENABLED", " yes ")
    expected = str(tmp_path / f"{prefix}_20260904_meta.json") if names and state != "disabled" else None
    if state == "explicit":
        expected = str(tmp_path / f"{prefix}_20260901_meta.json")
        monkeypatch.setenv("V2_MODEL_META" if role == "v2" else "TWO_STAGE_RANKER_META", expected)
    namespace = {"os": os, "glob": glob, "json": json, "MODEL_DIR": str(tmp_path),
                 "lgb": types.SimpleNamespace(Booster=lambda model_file: model_file),
                 "TWO_STAGE_CANDIDATE_SIZE": 50, "TWO_STAGE_LABEL_BINS": 5, "TWO_STAGE_TOP_N": 10}
    resolver = actual_function("ml/predict.py", f"_resolve_{role}_meta_path", namespace)
    loader_name = {"v2": "load_v2_model", "alpha": "load_v2_alpha_classifier", "ranker": "load_two_stage_ranker"}[role]
    loader = actual_function("ml/predict.py", loader_name, namespace)
    assert resolver() == expected
    loaded, meta = loader()
    if expected is None:
        assert loaded is None and meta is None
    else:
        assert loaded == metadata["model_file"]
        expected_meta = metadata.copy()
        if role == "ranker":
            expected_meta.update(candidate_size=17, label_bins=3, top_n=4)
        assert meta == expected_meta


@pytest.mark.parametrize("change_during", [None, "model_load", "snapshot_build"])
def test_actual_prediction_prefix_captures_before_load_and_binds_actual_frame(run_inputs, monkeypatch, change_during):
    source, meta, model, snapshot, old_run, predictions, path = run_inputs
    code = Path(os.environ.get("STOCK_PROVENANCE_SOURCE_DIR", Path(__file__).resolve().parents[1])) / "ml/predict.py"
    tree = ast.parse(code.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "predict_all")
    end = next(i for i, node in enumerate(function.body) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "snapshot_zs" for target in node.targets))
    function.body = function.body[:end] + [ast.Return(value=ast.Name(id="prediction_run", ctx=ast.Load()))]
    ast.fix_missing_locations(function)
    def load(**kwargs):
        if change_during == "model_load":
            change_text(source, ",10", ",11")
        return object(), {"feature_columns": ["feature"], "model_file": str(model)}
    def build(**kwargs):
        if change_during == "snapshot_build":
            change_text(source, ",10", ",11")
        return snapshot
    namespace = {"os": os, "_prediction_model_paths": lambda *args: {"base": {"meta_path": str(meta), "model_path": str(model)}},
                 "load_selected_model": load, "load_v2_model": lambda: (None, None),
                 "load_v2_alpha_classifier": lambda: (None, None), "load_two_stage_ranker": lambda: (None, None),
                 "load_sector_mapping": lambda: None, "build_latest_snapshot": build}
    for suffix in ("GATE", "TOP_N", "MIN_OPEN", "MIN_VOLUME", "MIN_AMOUNT"):
        namespace[f"VALIDATED_CHIP_MOMENTUM_{suffix}_ENV"] = f"FIXTURE_{suffix}"
    monkeypatch.setattr(lineage, "clear_source_memo_caches", lambda: None)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(code), "exec"), namespace)
    if change_during:
        with pytest.raises(RuntimeError, match="changed during"):
            namespace["predict_all"](save_snapshot=False, capture_provenance=True)
    else:
        run = namespace["predict_all"](save_snapshot=False, capture_provenance=True)
        assert run["snapshot"]["sha256"] == old_run["snapshot"]["sha256"]
        assert run["models"] == old_run["models"]
    assert not path.exists()
