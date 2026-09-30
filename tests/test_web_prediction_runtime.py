"""Real Web boundaries exercised with temp artifacts, without server/DB startup."""
import ast
import asyncio
import copy
import glob
import json
import os
from pathlib import Path
import re
import sys
import types

import numpy as np
import pandas as pd
import pytest

from ml import prediction_provenance as provenance
from ml import snapshot_lineage as lineage


def app_tree():
    path = Path(os.environ.get("STOCK_WEB_RUNTIME_APP_SOURCE", Path(__file__).resolve().parents[1] / "app.py"))
    return path, ast.parse(path.read_text(encoding="utf-8-sig"))


def app_function(name, namespace):
    path, tree = app_tree()
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("explicit", [False, True])
def test_startup_defaults_match_nightly_and_preserve_explicit_environment(monkeypatch, explicit):
    defaults = {"V2_MODEL_META": "pinned-v2", "TWO_STAGE_RANKER_ENABLED": "1", "TWO_STAGE_RANKER_META": "pinned-ranker"}
    for key in defaults:
        monkeypatch.delenv(key, raising=False)
    if explicit:
        monkeypatch.setenv("V2_MODEL_META", "explicit-v2")
    registry = types.SimpleNamespace(champion_defaults=lambda: defaults, validate_registry=lambda: {"passed": True})
    monkeypatch.setitem(sys.modules, "scripts.model_pin_registry", registry)
    app_function("_enable_web_champion_defaults", {"os": os})()
    assert os.environ["V2_MODEL_META"] == ("explicit-v2" if explicit else "pinned-v2")
    assert os.environ["TWO_STAGE_RANKER_ENABLED"] == "1"
    assert os.environ["TWO_STAGE_RANKER_META"] == "pinned-ranker"


def test_invalid_pin_registry_cannot_set_environment(monkeypatch):
    monkeypatch.delenv("V2_MODEL_META", raising=False)
    registry = types.SimpleNamespace(champion_defaults=lambda: {"V2_MODEL_META": "pinned"},
                                     validate_registry=lambda: {"passed": False, "failures": ["checksum mismatch"]})
    monkeypatch.setitem(sys.modules, "scripts.model_pin_registry", registry)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        app_function("_enable_web_champion_defaults", {"os": os})()
    assert "V2_MODEL_META" not in os.environ


def test_real_lifespan_validates_defaults_before_any_database_or_warmup():
    calls = []
    def fail():
        calls.append("validate")
        raise RuntimeError("fixture startup validation")
    lifespan = app_function("lifespan", {"FastAPI": object, "_enable_web_champion_defaults": fail})
    async def run():
        with pytest.raises(RuntimeError, match="fixture startup validation"):
            await anext(lifespan(None))
    asyncio.run(run())
    assert calls == ["validate"]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_text("ticker,feature\n2330,1\n", encoding="utf-8")
    model_paths = {}
    metadata = {}
    for role in ("base", "v2", "ranker"):
        meta, model = tmp_path / f"{role}.json", tmp_path / f"{role}.txt"
        model.write_text(f"actual {role} weights", encoding="utf-8")
        metadata[role] = {"model_file": str(model), "feature_columns": ["feature"]}
        meta.write_text(json.dumps(metadata[role]), encoding="utf-8")
        model_paths[role] = {"meta_path": str(meta), "model_path": str(model)}
    metadata["base"]["meta_path"] = model_paths["base"]["meta_path"]
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [str(source)])
    monkeypatch.setattr(provenance, "_prediction_patterns", lambda paths: [path for spec in paths.values() for path in spec.values()])
    snapshot = pd.DataFrame({"ticker": ["2330"], "Date": pd.to_datetime(["2026-09-04"]), "Close": [100.], "feature": [1.]})
    run = provenance.capture_run("production", model_paths=model_paths)
    run = provenance.bind_snapshot(run, snapshot)
    frame = pd.DataFrame({"ticker": ["2330"], "date": ["2026-09-04"], "close": [100.],
                          "pred_return_20d": [.1], "recommendation": ["建議買進"]})
    path = tmp_path / "predictions_2026-09-04.csv"
    certificate = provenance.publish_prediction_csv(frame, path, run)
    state = {"selected": copy.deepcopy(model_paths), "load_calls": 0, "during_prediction": None}
    class V2:
        def predict(self, values, pred_contrib=False):
            if state["during_prediction"]:
                state["during_prediction"]()
            return np.array([[.04, .06]]) if pred_contrib else np.array([.1])
    v2_model = V2()
    def load_base():
        state["load_calls"] += 1
        return object(), metadata["base"].copy()
    predictor = types.SimpleNamespace(_prediction_model_paths=lambda *args: copy.deepcopy(state["selected"]),
                                      load_latest_model=load_base, _sort_prediction_df=lambda df: df,
                                      load_v2_model=lambda: (v2_model, metadata["v2"].copy()),
                                      _DIMENSION_MAP={}, _load_disposition_set=lambda: set(),
                                      _model_needs_zscore=lambda meta: bool(meta.get("cross_sectional_zscore")))
    monkeypatch.setitem(sys.modules, "ml.predict", predictor)
    from ml import config
    monkeypatch.setattr(config, "MODEL_DIR", str(tmp_path))
    namespace = {"os": os, "glob": glob, "PRODUCTION_PREDICTION_RE": re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")}
    for name in ("_assert_prediction_runtime", "_load_predictions_csv_fallback", "_assert_prediction_snapshot",
                 "_load_explain_prediction_models", "_model_scoring_snapshot"):
        app_function(name, namespace)
    return types.SimpleNamespace(source=source, metadata=metadata, paths=model_paths, state=state, predictor=predictor,
                                  snapshot=snapshot, frame=frame, csv=path, certificate=certificate, namespace=namespace, v2_model=v2_model)


@pytest.mark.parametrize("mismatch", ["missing_ranker", "meta_path", "model_path", "loaded_weights"])
def test_runtime_rejects_different_roles_metadata_or_weights(runtime, mismatch):
    loaded = {"base": runtime.metadata["base"].copy()}
    if mismatch == "missing_ranker":
        del runtime.state["selected"]["ranker"]
    elif mismatch == "loaded_weights":
        loaded["base"]["model_file"] += ".different"
    else:
        runtime.state["selected"]["v2"][mismatch] += ".different"
    with pytest.raises(RuntimeError, match="模型"):
        runtime.namespace["_assert_prediction_runtime"](runtime.certificate, loaded_models=loaded)


def test_matching_csv_runtime_keeps_normal_frame_and_metadata(runtime):
    frame, meta, certificate = runtime.namespace["_load_predictions_csv_fallback"](include_certificate=True)
    pd.testing.assert_frame_equal(frame, pd.read_csv(runtime.csv, dtype={"ticker": str}))
    assert meta == runtime.metadata["base"] and certificate == runtime.certificate
    assert runtime.state["load_calls"] == 1


@pytest.mark.parametrize("include_certificate", [False, True])
def test_csv_mismatch_is_rejected_before_loading_another_model(runtime, include_certificate):
    runtime.state["selected"]["v2"]["model_path"] += ".other"
    result = runtime.namespace["_load_predictions_csv_fallback"](include_certificate=include_certificate)
    assert result == ((None, None, None) if include_certificate else (None, None))
    assert runtime.state["load_calls"] == 0


def test_explain_loader_keeps_recommendation_and_actual_v2_from_same_run(runtime):
    model, metadata, frame, certificate = runtime.namespace["_load_explain_prediction_models"](runtime.metadata["base"])
    assert model is runtime.v2_model and metadata == runtime.metadata["v2"]
    pd.testing.assert_frame_equal(frame, runtime.frame)
    assert certificate == runtime.certificate


def test_explain_rejects_model_switch_during_loading(runtime):
    def switching():
        runtime.state["selected"]["v2"]["model_path"] += ".changed"
        return runtime.v2_model, runtime.metadata["v2"]
    runtime.predictor.load_v2_model = switching
    with pytest.raises(RuntimeError, match="模型"):
        runtime.namespace["_load_explain_prediction_models"](runtime.metadata["base"])


@pytest.mark.parametrize("change", [None, "model", "source"])
def test_real_explain_math_keeps_normal_result_or_returns_explicit_data_error(runtime, monkeypatch, change):
    if change == "model":
        runtime.state["during_prediction"] = lambda: runtime.state["selected"]["v2"].update(model_path="changed-model")
    elif change == "source":
        def update():
            runtime.source.write_text("ticker,feature\n2330,2\n", encoding="utf-8")
        runtime.state["during_prediction"] = update
    monkeypatch.setitem(sys.modules, "ml.dataset", types.SimpleNamespace(load_single_stock=lambda *args, **kwargs: runtime.snapshot,
                                                                        _load_twii=lambda: None, apply_snapshot_zscore=lambda frame: frame.copy()))
    monkeypatch.setitem(sys.modules, "ml.features.institutional", types.SimpleNamespace(INSTITUTIONAL_FEATURE_COLS=[]))
    path, tree = app_tree()
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prediction_explain")
    function.decorator_list = []
    end = next(i for i, node in enumerate(function.body) if isinstance(node, ast.FunctionDef) and node.name == "_fmt_lots")
    function.body = function.body[:end] + ast.parse("return {'pred_return': float(pred_return), 'recommendation': recommendation, 'contribs': feat_contribs.tolist()}").body
    ast.fix_missing_locations(function)
    namespace = runtime.namespace
    namespace.update(_get_prediction_context=lambda: (runtime.snapshot, object(), runtime.metadata["base"]))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    result = namespace["prediction_explain"]("2330")
    if change:
        assert result["status"] == "data_error" and result["ticker"] == "2330"
        assert "recommendation" not in result and "pred_return" not in result
    else:
        assert result == {"pred_return": .1, "recommendation": "建議買進", "contribs": [.04]}


def test_explain_rejects_same_date_post_certificate_feature_mutation(runtime):
    modified = runtime.snapshot.copy(deep=True)
    modified.loc[0, "feature"] = 2.
    with pytest.raises(RuntimeError, match="快照"):
        runtime.namespace["_load_explain_prediction_models"](runtime.metadata["base"], modified)


def test_context_keeps_complete_canonical_frame_without_quarter_override(runtime, monkeypatch):
    import logging
    import threading
    state = {key: None for key in ("context_key", "snapshot", "model", "meta", "pred_df", "last_error")}
    state["loading"] = False
    namespace = {"_PREDICTION_CACHE": state, "_PREDICTION_CACHE_LOCK": threading.Lock(),
                 "_prediction_context_key": lambda: "fixed-current-context", "_load_pipeline_snapshot": lambda: runtime.snapshot,
                 "pipeline_log": logging.getLogger("web-frame-contract"),
                 "_apply_live_latest_quarter_overrides": lambda frame: pytest.fail("Canonical frame must never be overwritten")}
    monkeypatch.setitem(sys.modules, "ml.dataset", types.SimpleNamespace(build_latest_snapshot=lambda **kwargs: pytest.fail("No build needed")))
    before = runtime.snapshot.copy(deep=True)
    actual, _, _ = app_function("_get_prediction_context", namespace)()
    assert actual is runtime.snapshot
    pd.testing.assert_frame_equal(actual, before, check_exact=True)


def test_processor_receives_full_universe_and_never_replaces_raw_financial_facts(runtime, monkeypatch):
    raw = pd.DataFrame({"ticker": ["2330", "2603", "3605"], "Date": pd.to_datetime(["2026-09-04"]*3),
                        "operating_margin_latest": [.1, .2, .9]})
    before = raw.copy(deep=True)
    calls = []
    def zscore(frame):
        calls.append(frame.copy(deep=True))
        assert len(frame) == 3
        output = frame.copy()
        output["operating_margin_latest"] = [-.7, -.5, 1.2]
        return output
    monkeypatch.setitem(sys.modules, "ml.dataset", types.SimpleNamespace(apply_snapshot_zscore=zscore))
    processed = runtime.namespace["_model_scoring_snapshot"](raw, {"cross_sectional_zscore": True})
    assert processed.loc[processed.ticker.eq("2330"), "operating_margin_latest"].iloc[0] == -.7
    pd.testing.assert_frame_equal(raw, before, check_exact=True)
    assert len(calls) == 1
    assert runtime.namespace["_model_scoring_snapshot"](raw, {"cross_sectional_zscore": False}) is raw


def test_real_explain_keeps_raw_facts_separate_from_normalized_model_input(runtime, monkeypatch):
    runtime.metadata["v2"]["cross_sectional_zscore"] = True
    def zscore(frame):
        assert frame is runtime.snapshot
        output = frame.copy()
        output["feature"] = -2.
        return output
    monkeypatch.setitem(sys.modules, "ml.dataset", types.SimpleNamespace(load_single_stock=lambda *args, **kwargs: runtime.snapshot,
                    _load_twii=lambda: None, apply_snapshot_zscore=zscore))
    monkeypatch.setitem(sys.modules, "ml.features.institutional", types.SimpleNamespace(INSTITUTIONAL_FEATURE_COLS=[]))
    observed = []
    def calculate(values, pred_contrib=False):
        observed.append(values.copy())
        return np.array([[.04, .06]]) if pred_contrib else np.array([.1])
    runtime.v2_model.predict = calculate
    path, tree = app_tree()
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prediction_explain")
    function.decorator_list = []
    end = next(i for i, node in enumerate(function.body) if isinstance(node, ast.FunctionDef) and node.name == "_fmt_lots")
    function.body = function.body[:end] + ast.parse("return {'facts': feat_values.tolist(), 'display': feat_display_values.tolist(), 'model_return': float(model_pred_return), 'final_return': float(pred_return)}").body
    ast.fix_missing_locations(function)
    namespace = runtime.namespace
    namespace.update(_get_prediction_context=lambda: (runtime.snapshot, object(), runtime.metadata["base"]))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["prediction_explain"]("2330") == {"facts": [1.], "display": [1.], "model_return": .1, "final_return": .1}
    assert len(observed) == 2 and all(values[0, 0] == -2. for values in observed)


@pytest.mark.parametrize("reassemble", [False, True])
@pytest.mark.parametrize("include_source_manifest", [False, True])
def test_normal_snapshot_publish_bind_csv_load_roundtrip_accepts_exact_frame(runtime, reassemble, include_source_manifest):
    frame = pd.DataFrame({"ticker": ["2330", "3605"], "Date": pd.to_datetime(["2026-09-04"]*2),
                          "numeric": np.array([.125, np.nan], dtype=np.float32),
                          "label": ["獨立建構的字串", "獨立建構的字串"], "boolean": [True, False]})
    if reassemble:
        frame = pd.concat([frame.iloc[:1].copy(deep=True), frame.iloc[1:].copy(deep=True)], ignore_index=True)
        frame["object_extra"] = pd.Series(["first", None], dtype=object)
    frame.attrs["zscore_applied"] = False
    snapshot_path = runtime.csv.with_name("canonical_snapshot.pkl")
    run = provenance.capture_run("production", model_paths=runtime.paths)
    manifest = lineage.save_snapshot(frame, snapshot_path, run["sources"])
    bound = provenance.bind_snapshot(run, frame, snapshot_manifest=manifest if include_source_manifest else None)
    certificate = provenance.publish_prediction_csv(runtime.frame, runtime.csv, bound)
    assert (certificate["snapshot"]["source_manifest"] is not None) is include_source_manifest
    runtime.predictor.SNAPSHOT_CACHE_PATH = str(snapshot_path)
    loaded = lineage.load_snapshot(snapshot_path)
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded, frame, check_exact=True)
    _ = loaded["ticker"]
    _ = loaded[loaded["ticker"].eq("2330")].tail(1)
    runtime.namespace["_assert_prediction_snapshot"](loaded, certificate)
    for damage in ("value", "dtype", "index", "processing_attributes"):
        changed = loaded.copy(deep=True)
        if damage == "value":
            changed.loc[0, "numeric"] = .25
        elif damage == "dtype":
            changed["numeric"] = changed["numeric"].astype(np.float64)
        elif damage == "index":
            changed.index.name = "different-index-contract"
        else:
            changed.attrs["zscore_applied"] = True
        with pytest.raises(RuntimeError, match="快照"):
            runtime.namespace["_assert_prediction_snapshot"](changed, certificate)
    snapshot_path.write_bytes(snapshot_path.read_bytes()+b"different original reference")
    with pytest.raises(RuntimeError, match="快照"):
        runtime.namespace["_assert_prediction_snapshot"](loaded, certificate)


@pytest.mark.parametrize("raw,final,changed", [(.1, .1, False), (.5093228977184205, .5072547122836113, True)])
def test_return_payload_separates_raw_model_and_existing_postprocessed_return(raw, final, changed):
    path, tree = app_tree()
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prediction_explain")
    branch = next(node for node in reversed(function.body) if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "is_regression")
    end = next(i for i, node in enumerate(branch.body) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_risk_tags" for target in node.targets))
    namespace = {"np": np, "pred_return": final, "model_pred_return": raw, "result": {}}
    exec(compile(ast.Module(body=branch.body[:end], type_ignores=[]), str(path), "exec"), namespace)
    result = namespace["result"]
    assert result["pred_return_20d"] == round(final*100, 2)
    assert result["model_pred_return_20d"] == round(raw*100, 2)
    assert result["prediction_postprocessed"] is changed
    assert ("prediction_postprocess_note" in result) is changed


@pytest.mark.parametrize("state", ["valid", "missing", "stale"])
def test_canonical_entry_api_uses_verified_loader_and_fails_with_503(tmp_path, monkeypatch, state):
    from fastapi.responses import JSONResponse
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "entry_list_2026-09-07.json"
    if state != "missing":
        path.write_text('{"unverified":"must not read directly"}', encoding="utf-8")
    # The producer now publishes a same-prefix JSON sidecar; it must never be
    # mistaken for a newer plan, including when no plan exists.
    path.with_name(path.name + ".manifest.json").write_text('{"certificate":true}', encoding="utf-8")
    calls = []
    payload = {"as_of": "2026-09-04", "trade_date": "2026-09-07", "candidates": [{"ticker": "3605"}]}
    def verified(actual):
        calls.append(str(actual))
        if state == "stale":
            raise ValueError("canonical lineage stale")
        return copy.deepcopy(payload)
    monkeypatch.setitem(sys.modules, "scripts.entry_artifact_lineage", types.SimpleNamespace(load_entry_artifact=verified))
    namespace = {"os": os, "__file__": str(tmp_path / "app.py"), "JSONResponse": JSONResponse}
    result = app_function("entry_canonical_list", namespace)()
    body = json.loads(result.body)
    if state == "valid":
        assert result.status_code == 200 and body == {**payload, "source_file": path.name}
    else:
        assert result.status_code == 503 and body["status"] == "data_error" and "rows" not in body
    assert len(calls) == (0 if state == "missing" else 1)
