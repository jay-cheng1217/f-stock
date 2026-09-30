"""Completed generation proof using real prediction/snapshot certificates."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml import snapshot_lineage as lineage, prediction_provenance as predictions
from scripts import entry_artifact_lineage as proof
from scripts import generate_entry_candidates as gen

REAL_LIVE_DATES = proof._live_dates


@pytest.fixture
def case(tmp_path, monkeypatch):
    models = tmp_path / "ml/models"
    models.mkdir(parents=True)
    data = tmp_path / "ml/data"
    data.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    raw = tmp_path / "daily.csv"
    raw.write_text("Date,Close\n2026-09-04,90.5\n", encoding="utf-8")
    aux = tmp_path / "financial.csv"
    aux.write_text("Ticker,EPS\n1111,10\n", encoding="utf-8")
    sector = data / "sector_mapping.csv"
    sector.write_text("Ticker,Name,Sector\n1111,Fixture,fixture\n", encoding="utf-8")
    strength = data / "sector_strength.json"
    strength.write_text('{"fixture":{"tier":"strong"}}', encoding="utf-8")
    meta, model = models / "fixture_meta.json", models / "fixture.txt"
    meta.write_text('{"model_file":"fixture.txt"}', encoding="utf-8")
    model.write_text("fixture weights", encoding="utf-8")
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [str(raw), str(aux)])
    monkeypatch.setattr(predictions, "_prediction_patterns", lambda paths: [p for spec in paths.values() for p in spec.values()])
    monkeypatch.setattr(gen, "BASE_DIR", tmp_path)
    monkeypatch.setattr(gen, "MODEL_DIR", models)
    monkeypatch.setattr(gen, "REPORT_DIR", tmp_path / "ml/reports")
    monkeypatch.setattr(gen, "SECTOR_MAP", sector)
    monkeypatch.setattr(gen, "CHIPK_ARCHIVE_DIR", data / "chipk/archive")
    monkeypatch.setattr(proof, "_live_dates", lambda: ("2026-09-04", "2026-09-07"))
    monkeypatch.delenv("CHIPK_DESKTOP_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_ENABLE_CHIPK_DESKTOP", raising=False)
    snapshot = pd.DataFrame({"ticker": ["1111"], "Date": ["2026-09-04"], "Close": [90.5],
                             "Volume": [800000.], "operating_margin_latest": [np.nan]})
    lineage.save_snapshot(snapshot, models / "snapshot_cache.pkl", lineage.source_state(include_hashes=True))
    model_paths = {"dataA": {"meta_path": str(meta), "model_path": str(model)}}
    def score(value=-.1, prefix="dataA_predictions_", slot="dataA"):
        run = predictions.capture_run(slot, model_paths=model_paths)
        run = predictions.bind_snapshot(run, snapshot)
        frame = pd.DataFrame({"ticker": ["1111"], "source_date": ["2026-09-04"],
                              "pred_return_20d": [value], "operating_margin_latest": [np.nan]})
        path = models / f"{prefix}2026-09-04.csv"
        predictions.publish_prediction_csv(frame, path, run)
        return path
    selected = score()
    monkeypatch.setattr(gen, "load_disposition_gate", lambda *a, **k: {"complete": True, "blocked_tickers": [], "reasons": []})
    monkeypatch.setattr(gen, "_twii_above_ma60", lambda *a: True)
    def technical(*args):
        return {"signal_date": "2026-09-04", "close": 90.5, "v20": -.005, "v60": .08,
            "rsi": 55., "vol_ratio": .9, "macd_delta_rel": .001, "ma20": 91.,
            "elow": 89., "ehigh": 92., "foreign20": 10., "vol_today_lots": 800.,
            "wash_from_hi10": .105, "vr_today": .9, "foreign_prev5": -10., "foreign_today": 2.,
            "trust_today": 0., "day_ret": 0., "prev_below_ma20": True}
    monkeypatch.setattr(gen, "_technical", technical)
    return {"root": tmp_path, "models": models, "logs": logs, "aux": aux, "sector": strength,
            "selected": selected, "score": score, "plan": logs / "entry_list_20260907.json"}


def generate_and_publish(case, **kwargs):
    result = gen.generate_certified("2026-09-04", trade_date="2026-09-07", include_live=False, **kwargs)
    payload = gen.to_entry_json(result, "2026-09-07")
    gen._atomic_json(case["plan"], payload)
    return result, payload


def test_success_keeps_rere_negative_score_and_publishes_verified_copies(case):
    raw = gen.generate("2026-09-04", trade_date="2026-09-07", include_live=False)
    result, payload = generate_and_publish(case)
    assert result["rows"] == raw["rows"]
    assert result["rows"][0]["lane"] == "rere" and result["rows"][0]["kind"] == "small"
    assert result["rows"][0]["pred20"] == -.1
    verified = proof.load_entry_artifact(case["plan"])
    copy = case["root"] / "frontend/static/entry_canonical.json"
    gen._atomic_json(copy, payload)
    assert proof.load_entry_artifact(copy) == verified
    assert proof.INTERNAL_PAYLOAD not in verified
    assert verified[proof.PROOF]["scope"] == "successful_live_entry_generation"


def test_legacy_plan_cannot_be_stamped_or_silently_loaded(case):
    raw = gen.generate("2026-09-04", trade_date="2026-09-07", include_live=False)
    payload = gen.to_entry_json(raw, "2026-09-07")
    with pytest.raises(proof.EntryArtifactLineageError, match="without successful generation"):
        gen._atomic_json(case["plan"], payload)
    case["plan"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(proof.EntryArtifactLineageError, match="rejected or absent"):
        proof.load_entry_artifact(case["plan"])


@pytest.mark.parametrize("mutation", ["same_scores_new_certificate", "different_scores", "aux", "sector", "watchlist", "new_announcements", "chipk_mode"])
def test_same_date_dependency_change_rejects_old_plan(case, monkeypatch, mutation):
    generate_and_publish(case)
    if mutation == "same_scores_new_certificate":
        case["score"](-.1)
    elif mutation == "different_scores":
        case["score"](-.2)
    elif mutation == "aux":
        case["aux"].write_text("Ticker,EPS\n1111,11\n", encoding="utf-8")
    elif mutation == "sector":
        case["sector"].write_text('{"fixture":{"tier":"weak"}}', encoding="utf-8")
    elif mutation == "watchlist":
        (case["logs"] / "entry_watchlist.json").write_text("[]", encoding="utf-8")
    elif mutation == "new_announcements":
        news = case["root"] / "新聞資料"
        news.mkdir()
        (news / "announcements_20260904.csv").write_text("Ticker,Date,Title\n1111,2026-09-04,new\n", encoding="utf-8")
    else:
        monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")
    with pytest.raises(proof.EntryArtifactLineageError):
        proof.load_entry_artifact(case["plan"])


def test_failed_new_generation_preserves_old_files_but_cannot_claim_current(case, monkeypatch):
    generate_and_publish(case)
    manifest = Path(str(case["plan"]) + ".manifest.json")
    old = {path: path.read_bytes() for path in (case["plan"], manifest)}
    case["score"](-.2)
    def fail(*a, **k):
        raise RuntimeError("fixture generation failed")
    monkeypatch.setattr(gen, "generate", fail)
    with pytest.raises(RuntimeError, match="generation failed"):
        generate_and_publish(case)
    assert old == {path: path.read_bytes() for path in old}
    with pytest.raises(proof.EntryArtifactLineageError):
        proof.load_entry_artifact(case["plan"])


def test_new_preferred_prediction_invalidates_old_fallback_plan(case):
    case["selected"].unlink()
    Path(str(case["selected"]) + ".manifest.json").unlink()
    case["score"](prefix="predictions_", slot="production")
    generate_and_publish(case)
    assert proof.load_entry_artifact(case["plan"])["model_source"] == "predictions_2026-09-04.csv"
    case["score"]()
    with pytest.raises(proof.EntryArtifactLineageError, match="Selected prediction artifact changed"):
        proof.load_entry_artifact(case["plan"])


def test_dependency_changes_during_generation_never_bind_proof(case, monkeypatch):
    original = gen.generate
    def changed(*a, **k):
        result = original(*a, **k)
        case["sector"].write_text("{}", encoding="utf-8")
        return result
    monkeypatch.setattr(gen, "generate", changed)
    with pytest.raises(proof.EntryArtifactLineageError, match="dependency changed"):
        generate_and_publish(case)
    assert not case["plan"].exists()


def test_watchlist_feedback_is_once_and_preserves_before_after_evidence(case, monkeypatch):
    watchlist = case["logs"] / "entry_watchlist.json"
    watchlist.write_text(json.dumps([{"ticker": "2222", "zone_low": 89, "zone_high": 92,
        "stop": 80, "reviewed_at": "2020-01-01"}]), encoding="utf-8")
    original = gen.generate
    calls = []
    def counted(*a, **k):
        calls.append(k.get("persist_watchlist"))
        return original(*a, **k)
    monkeypatch.setattr(gen, "generate", counted)
    result, _ = generate_and_publish(case, persist_watchlist=True)
    assert calls == [True]
    assert json.loads(watchlist.read_text(encoding="utf-8")) == []
    evidence = result[proof.INTERNAL_RESULT]
    assert len(evidence["authorized_outputs"]) == 2
    assert {Path(row["path"]).name for row in evidence["authorized_outputs"]} == {"entry_watchlist.json", "entry_watchlist_archive.json"}
    assert proof.load_entry_artifact(case["plan"])["rows"]


@pytest.mark.parametrize("damage", ["json", "manifest", "historical", "changed_result", "changed_payload", "changed_proof"])
def test_modified_or_historical_plan_cannot_publish_as_current(case, monkeypatch, damage):
    result, payload = generate_and_publish(case)
    if damage == "changed_result":
        result["rows"][0]["pred20"] = 99
        with pytest.raises(proof.EntryArtifactLineageError, match="result was changed"):
            gen.to_entry_json(result, "2026-09-07")
        return
    if damage == "changed_payload":
        payload["rows"][0]["kind"] = "go"
        with pytest.raises(proof.EntryArtifactLineageError, match="payload changed"):
            gen._atomic_json(case["plan"], payload)
        return
    if damage == "changed_proof":
        payload[proof.PROOF]["run_id"] = "forged"
        with pytest.raises(proof.EntryArtifactLineageError, match="compact proof changed"):
            gen._atomic_json(case["plan"], payload)
        return
    if damage == "json":
        case["plan"].write_bytes(case["plan"].read_bytes() + b" ")
    elif damage == "manifest":
        Path(str(case["plan"]) + ".manifest.json").unlink()
    else:
        monkeypatch.setattr(proof, "_live_dates", lambda: ("2026-09-07", "2026-09-08"))
    with pytest.raises(proof.EntryArtifactLineageError):
        proof.load_entry_artifact(case["plan"])


def test_payload_cannot_claim_different_same_date_model_source(case):
    result, payload = generate_and_publish(case)
    body = {key: value for key, value in payload.items() if key not in (proof.PROOF, proof.INTERNAL_PAYLOAD)}
    body["model_source"] = "predictions_2026-09-04.csv"
    with pytest.raises(proof.EntryArtifactLineageError, match="model source"):
        proof.bind_payload(result, body)


def test_formal_auditor_uses_real_entry_proof_and_rejects_same_date_stale_plan(case):
    from scripts.audit_formal_data_consistency import audit_entry_artifacts
    generate_and_publish(case)
    payload = proof.load_entry_artifact(case["plan"])
    html = case["root"] / "dashboard.html"
    html.write_text('<script id="data" type="application/json">' + json.dumps({
        "meta": {"trade_date": "2026-09-07"}, "rows": payload["rows"]}) + '</script>', encoding="utf-8")
    sources = {"latest": pd.DataFrame({"Ticker": ["1111"], "Date": ["2026-09-04"]})}
    result = audit_entry_artifacts(case["plan"], html, sources, "2026-09-04", "2026-09-07")
    assert result["checks"]["entry_plan_lineage"] is True
    assert result["entry_manifest_receipt"]["sha256"]
    case["score"](-.2)
    with pytest.raises(proof.EntryArtifactLineageError):
        audit_entry_artifacts(case["plan"], html, sources, "2026-09-04", "2026-09-07")


@pytest.mark.parametrize("clock,as_of,trade", [
    ("2026-09-04T17:59:59", "2026-09-03", "2026-09-04"),
    ("2026-09-04T18:00:00", "2026-09-04", "2026-09-07"),
    ("2026-09-05T08:00:00", "2026-09-04", "2026-09-07"),
    ("2026-09-06T23:00:00", "2026-09-04", "2026-09-07"),
    ("2026-09-07T08:00:00", "2026-09-04", "2026-09-07"),
    ("2026-09-07T17:59:59", "2026-09-04", "2026-09-07"),
    ("2026-09-07T18:00:00", "2026-09-07", "2026-09-08"),
])
def test_live_dates_cli_and_capture_defaults_match(monkeypatch, clock, as_of, trade):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(clock)
    monkeypatch.setattr(proof, "datetime", Clock)
    monkeypatch.setattr(gen, "datetime", Clock)
    assert proof._live_dates() == (as_of, trade)
    calls = []
    monkeypatch.setattr(gen, "generate_certified", lambda *a, **k: calls.append(k) or {"as_of_close": None})
    monkeypatch.setattr("sys.argv", ["generate_entry_candidates.py", "--dry"])
    assert gen.main() == 2  # Stops before output; only date resolution is under test.
    assert calls == [{"trade_date": trade, "persist_watchlist": False}]
    selected_dates = []
    monkeypatch.setattr(gen, "_latest_predictions_path", lambda day: selected_dates.append(day.isoformat()))
    with pytest.raises(proof.EntryArtifactLineageError, match="No current selected prediction"):
        proof.capture_generation(None, None)
    assert selected_dates == [as_of]


@pytest.mark.parametrize("clock", ["2026-09-04T18:00:00", "2026-09-06T08:00:00", "2026-09-07T08:00:00"])
def test_omitted_dates_reach_one_real_generation_with_resolved_dates(case, monkeypatch, clock):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(clock)
    monkeypatch.setattr(proof, "datetime", Clock)
    monkeypatch.setattr(gen, "datetime", Clock)
    monkeypatch.setattr(proof, "_live_dates", REAL_LIVE_DATES)
    original = gen.generate
    calls = []
    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(gen, "generate", counted)
    result = gen.generate_certified(include_live=False)
    assert len(calls) == 1
    assert calls[0] == (("2026-09-04",), {"trade_date": "2026-09-07", "persist_watchlist": False, "include_live": False})
    assert result["as_of_date"] == "2026-09-04" and result["trade_date"] == "2026-09-07"
    assert result[proof.INTERNAL_RESULT]["trade_date"] == "2026-09-07"
