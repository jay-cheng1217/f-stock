"""Exercise the real app cache transition without importing its DB/server."""
import ast
import logging
import os
from pathlib import Path
import sys
import threading
import types

import pandas as pd
import pytest


def context_harness(monkeypatch, *, load_model):
    source = Path(os.environ.get("STOCK_CONTEXT_APP_SOURCE", Path(__file__).resolve().parents[1] / "app.py"))
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_get_prediction_context")
    old = pd.DataFrame({"ticker": ["2330"], "value": [1]})
    current = pd.DataFrame({"ticker": ["2330"], "value": [2]})
    state = {"context_key": "K1", "snapshot": old, "model": object(), "meta": {"source": "K1"},
             "pred_df": old.copy(), "loading": False, "last_error": None}
    source_key = {"value": "K2"}
    namespace = {
        "_prediction_context_key": lambda: source_key["value"],
        "_PREDICTION_CACHE": state, "_PREDICTION_CACHE_LOCK": threading.Lock(),
        "_load_pipeline_snapshot": lambda: current,
        "_load_snapshot_cache_from_disk": lambda key: pytest.fail("No disk writes/reads needed"),
        "_apply_live_latest_quarter_overrides": lambda frame: frame,
        "pipeline_log": logging.getLogger("cache-transition-test"),
    }
    monkeypatch.setitem(sys.modules, "ml.predict", types.SimpleNamespace(load_latest_model=load_model))
    monkeypatch.setitem(sys.modules, "ml.dataset", types.SimpleNamespace(build_latest_snapshot=lambda **kwargs: pytest.fail("No production build")))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_get_prediction_context"], state, source_key, old, current


def test_failed_rebuild_never_relabels_old_snapshot_as_new_key(monkeypatch):
    attempts = []
    def failing_model():
        attempts.append(True)
        raise RuntimeError("fresh rebuild failed")
    get_context, state, _, old, _ = context_harness(monkeypatch, load_model=failing_model)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="fresh rebuild failed"):
            get_context()
        assert state["context_key"] == "K2" and not state["loading"]
        assert state["snapshot"] is None and state["model"] is None and state["meta"] is None
        assert state["pred_df"] is None and state["last_error"] == "fresh rebuild failed"
    assert len(attempts) == 2


def test_concurrent_request_during_rebuild_cannot_return_old_snapshot(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    fresh_model = object()
    def blocked_model():
        entered.set()
        assert release.wait(5), "test failed to release builder"
        return fresh_model, {"source": "K2"}
    get_context, state, _, _, current = context_harness(monkeypatch, load_model=blocked_model)
    results, errors = [], []
    def build():
        try:
            results.append(get_context())
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=build)
    thread.start()
    try:
        assert entered.wait(5)
        assert state["loading"] and state["snapshot"] is None
        with pytest.raises(RuntimeError, match="建置中"):
            get_context()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert len(results) == 1 and results[0][0] is current and results[0][1] is fresh_model
    assert get_context()[0] is current


def test_changed_key_waits_for_previous_builder_then_rebuilds_without_cross_publish(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked_model():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5), "test failed to release builder"
        return object(), {"attempt": len(calls)}
    get_context, state, source_key, _, current = context_harness(monkeypatch, load_model=blocked_model)
    errors = []
    def build_k2():
        try:
            get_context()
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=build_k2)
    thread.start()
    try:
        assert entered.wait(5)
        source_key["value"] = "K3"
        with pytest.raises(RuntimeError, match="建置中"):
            get_context()
        assert state["context_key"] == "K2" and state["loading"] and len(calls) == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(errors) == 1
    assert "來源或模型" in str(errors[0])
    assert state["snapshot"] is None and not state["loading"]
    assert get_context()[0] is current
    assert state["context_key"] == "K3" and state["last_error"] is None and len(calls) == 2
