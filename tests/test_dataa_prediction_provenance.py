"""Real certificates around isolated DataA scoring and canonical reads."""
import ast
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml import prediction_provenance as provenance
from ml import snapshot_lineage as lineage
from scripts import generate_entry_candidates as entry
from scripts import shadow_dataA_tracker as tracker


@pytest.fixture
def case(tmp_path, monkeypatch):
    models = tmp_path / "ml/models"
    models.mkdir(parents=True)
    daily = tmp_path / "daily.csv"
    daily.write_text("Date,Close\n2026-09-04,100\n", encoding="utf-8")
    aux = tmp_path / "financial.csv"
    aux.write_text("Ticker,EPS\n2330,10\n", encoding="utf-8")
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [str(daily), str(aux)])
    monkeypatch.setattr(provenance, "_prediction_patterns", lambda paths: [p for spec in paths.values() for p in spec.values()])
    meta = models / "dataa_meta.json"
    weights = models / "dataa_model.txt"
    weights.write_text("fixture model bytes", encoding="utf-8")
    meta.write_text(json.dumps({"model_file": weights.name, "feature_columns": ["alpha"]}), encoding="utf-8")
    snapshot = pd.DataFrame({"ticker": ["2330", "5371"], "Date": ["2026-09-04", "2026-09-02"],
                             "Close": [100., 40.], "Volume": [1e6, 5e5], "alpha": [.1, -.2],
                             "operating_margin_latest": [np.nan, -3.]})
    cache = models / "snapshot_cache.pkl"
    lineage.save_snapshot(snapshot, cache, lineage.source_state(include_hashes=True))
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("date,side,rank,ticker,score,ret20_pct\n2026-09-03,shadow,1,2330,0.1,\n", encoding="utf-8")
    monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tracker, "SHADOW_META", meta)
    monkeypatch.setattr(tracker, "SNAPSHOT_PKL", cache)
    monkeypatch.setattr(tracker, "LEDGER", ledger)
    monkeypatch.setattr(entry, "MODEL_DIR", models)
    class Booster:
        def __init__(self, **kwargs):
            assert Path(kwargs["model_file"]) == weights
        def predict(self, values):
            return values["alpha"].to_numpy()
    monkeypatch.setattr("lightgbm.Booster", Booster)
    return {"models": models, "aux": aux, "daily": daily, "snapshot": snapshot,
            "cache": cache, "ledger": ledger,
            "output": models / "dataA_predictions_2026-09-04.csv"}


def publish(case):
    tracker.record(predictions_only=True, expected_asof="2026-09-04")
    return provenance.load_prediction_csv(case["output"], expected_model_slot="dataA")


def change_aux(case):
    old = case["aux"].stat()
    case["aux"].write_text("Ticker,EPS\n2330,11\n", encoding="utf-8")
    os.utime(case["aux"], ns=(old.st_atime_ns, old.st_mtime_ns + 1000000))


def test_successful_scoring_certifies_actual_frame_and_preserves_ledger(case):
    ledger_before = case["ledger"].read_bytes()
    loaded = publish(case)
    assert loaded is not None
    frame, certificate = loaded
    assert list(frame.source_date) == ["2026-09-04", "2026-09-02"]
    assert certificate["snapshot"]["source_manifest"]["snapshot_sha256"] == hashlib.sha256(case["cache"].read_bytes()).hexdigest()
    assert len(certificate["snapshot"]["sha256"]) == 64
    assert case["ledger"].read_bytes() == ledger_before
    canonical = entry._load_predictions(pd.Timestamp("2026-09-04").date())
    assert list(canonical.source_date) == list(frame.source_date)
    assert canonical.loc[canonical.ticker.eq("5371"), "pred_return_20d"].iloc[0] < 0
    assert entry._snapshot_fields(pd.Timestamp("2026-09-04").date())[0]["5371"] == "2026-09-02"


def test_same_date_aux_change_rejects_scores_without_changing_price(case):
    assert publish(case) is not None
    price_before = case["daily"].read_bytes()
    change_aux(case)
    assert case["daily"].read_bytes() == price_before
    assert provenance.load_prediction_csv(case["output"], expected_model_slot="dataA") is None
    with pytest.raises(ValueError, match="lineage rejected"):
        entry._load_predictions(pd.Timestamp("2026-09-04").date())
    with pytest.raises(ValueError, match="snapshot lineage"):
        entry._snapshot_fields(pd.Timestamp("2026-09-04").date())


def test_failed_score_never_recertifies_old_csv_for_new_aux(case, monkeypatch):
    assert publish(case) is not None
    manifest = Path(str(case["output"]) + ".manifest.json")
    before = {p: p.read_bytes() for p in (case["output"], manifest, case["ledger"])}
    change_aux(case)
    lineage.save_snapshot(case["snapshot"], case["cache"], lineage.source_state(include_hashes=True))
    def fail(*args, **kwargs):
        raise RuntimeError("fixture scoring failed")
    monkeypatch.setattr(tracker, "score_snapshot", fail)
    with pytest.raises(RuntimeError, match="scoring failed"):
        tracker.record(predictions_only=True, expected_asof="2026-09-04")
    assert before == {p: p.read_bytes() for p in before}
    assert provenance.load_prediction_csv(case["output"], expected_model_slot="dataA") is None


def test_refresh_clears_memos_and_binds_new_frame_without_overwriting_shared_cache(case, monkeypatch):
    shared_before = case["cache"].read_bytes()
    change_aux(case)
    fresh = case["snapshot"].copy()
    fresh["alpha"] += .01
    calls = []
    monkeypatch.setattr(lineage, "clear_source_memo_caches", lambda: calls.append("clear"))
    def build(**kwargs):
        assert calls == ["clear"]
        calls.append("build")
        return fresh
    monkeypatch.setattr("ml.dataset.build_latest_snapshot", build)
    tracker.record(predictions_only=True, refresh_snapshot=True, expected_asof="2026-09-04")
    loaded = provenance.load_prediction_csv(case["output"], expected_model_slot="dataA")
    assert loaded is not None and calls == ["clear", "build"]
    assert loaded[1]["snapshot"]["source_manifest"] is None
    assert case["cache"].read_bytes() == shared_before
    np.testing.assert_allclose(loaded[0].pred_return_20d, fresh.alpha.astype(np.float32))


def test_invalid_preferred_dataa_never_silently_falls_back_to_other_model(case):
    assert publish(case) is not None
    other = case["models"] / "predictions_2026-09-04.csv"
    other.write_text("ticker,date,pred_return_20d\n2330,2026-09-04,.9\n", encoding="utf-8")
    Path(str(case["output"]) + ".manifest.json").unlink()
    with pytest.raises(ValueError, match="dataA prediction lineage"):
        entry._load_predictions(pd.Timestamp("2026-09-04").date())


def test_scoring_and_all_strategy_functions_are_ast_unchanged():
    expected = {
        ("scripts/shadow_dataA_tracker.py", "score_snapshot"): "59a9c6c0ea2c742e34856d6e8719240a370dd46d61cc5caeae4d4e5e71c25c19",
        ("scripts/generate_entry_candidates.py", "_technical"): "94e8b918dc1b7fcee2b6db516db6f01251705c011b288df1ec281b6b35eda0f9",
        ("scripts/generate_entry_candidates.py", "_pattern_ok"): "3416b7eeb156182360441d87e6c1fb7e1927c6f70a7001738f3bd68a7941e268",
        ("scripts/generate_entry_candidates.py", "_rere_lane_ok"): "7bb5dd59315ef6494461adeaf6b5169d9f6b4bf2bf780f33b4b7a2709e0a48b4",
        ("scripts/generate_entry_candidates.py", "generate"): "2a249d73b240bc2b81c422735e563639b1f06a829db846d9965f48907323fcd1",
        ("scripts/generate_entry_candidates.py", "_apply_data_quality"): "893fdea3a97d2bec626576955fc90ee831c6884c4d343fe2479cfce288d49fad",
    }
    root = Path(__file__).resolve().parents[1]
    for (file, name), sha in expected.items():
        node = next(n for n in ast.parse((root / file).read_text(encoding="utf-8-sig")).body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        assert hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() == sha, name
