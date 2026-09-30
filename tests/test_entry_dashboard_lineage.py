"""Temporary real certificates exercise the renderer's read-only contract."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from ml import prediction_provenance as predictions
from ml import snapshot_lineage as snapshots
from scripts import entry_artifact_lineage as plans
from scripts import entry_dashboard as dashboard


@pytest.fixture
def render_inputs(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml/models"
    model_dir.mkdir(parents=True)
    daily_dir = tmp_path / "日K資料"
    daily_dir.mkdir()
    daily = daily_dir / "2330.csv"
    daily.write_text("Date,Open,High,Low,Close,Volume\n2026-09-03,99,101,98,100,1000000\n"
                     "2026-09-04,100,103,99,102,2000000\n", encoding="utf-8")
    sector = tmp_path / "ml/data/sector_mapping.csv"
    sector.parent.mkdir()
    sector.write_text("Ticker,Name,Sector\n2330,台積電,半導體\n", encoding="utf-8")
    auxiliary = tmp_path / "sector_strength.json"
    auxiliary.write_text("{}", encoding="utf-8")
    meta, model = model_dir / "fixture_meta.json", model_dir / "fixture_model.txt"
    model.write_text("fixture model A", encoding="utf-8")
    meta.write_text(json.dumps({"model_file": str(model)}), encoding="utf-8")
    monkeypatch.setattr(snapshots, "canonical_source_patterns", lambda: [str(daily), str(sector)])
    monkeypatch.setattr(predictions, "_prediction_patterns", lambda paths: [str(meta), str(model)])
    monkeypatch.setattr(dashboard, "BASE_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "DAILY_DIR", daily_dir)
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "existing.html")
    for name, value in {"load_indices": [], "load_tdcc": ({}, None),
                        "load_whale_deltas": ({}, [], None), "load_news_html": "",
                        "load_arena": {}, "load_holdings": [], "load_daytrade": {"rows": [], "asof": None}}.items():
        monkeypatch.setattr(dashboard, name, lambda *args, _value=value, **kwargs: _value)
    snapshot = pd.DataFrame({"ticker": ["2330"], "Date": ["2026-09-04"],
                             "RSI_14": [58.123456789012345], "operating_margin_latest": [.2]})
    snapshots.save_snapshot(snapshot, model_dir / "snapshot_cache.pkl",
                            snapshots.source_state(include_hashes=True))
    frame = pd.DataFrame({"ticker": ["2330"], "date": ["2026-09-04"], "close": [102.],
                          "pred_return_20d": [.12345678901234567], "up_prob": [.7],
                          "down_prob": [.2], "recommendation": ["建議買進"], "signal": ["buy"]})
    paths = {"base": {"meta_path": str(meta), "model_path": str(model)}}
    csv_paths = {}
    def publish(slot):
        path = model_dir / f"{'dataA_' if slot == 'dataA' else ''}predictions_2026-09-04.csv"
        run = predictions.bind_snapshot(predictions.capture_run(slot, model_paths=paths), snapshot)
        predictions.publish_prediction_csv(frame, path, run)
        csv_paths[slot] = path
    publish("dataA")
    publish("production")
    generator = SimpleNamespace(BASE_DIR=tmp_path, MODEL_DIR=model_dir,
                                _chipk_desktop_enabled=lambda: False,
                                _latest_predictions_path=lambda date: csv_paths["dataA"])
    monkeypatch.setattr(plans, "_generator", lambda: generator)
    monkeypatch.setattr(plans, "_extra_patterns", lambda: [str(auxiliary)])
    monkeypatch.setattr(plans, "_live_dates", lambda: ("2026-09-04", "2026-09-07"))
    row = {"stock": "2330 台積電", "lane": "main", "kind": "go", "sector": "半導體",
           "source_date": "2026-09-04", "model_source_date": "2026-09-04", "data_status": "OK",
           "zone": "100-105", "stop": "95", "reason": "fixture evidence"}
    run = plans.capture_generation("2026-09-04", "2026-09-07")
    result = plans.bind_generation(run, {"as_of_date": "2026-09-04", "trade_date": "2026-09-07",
                                        "as_of_close": str(csv_paths["dataA"]), "rows": [row]})
    payload = plans.bind_payload(result, {"as_of_date": "2026-09-04", "trade_date": "2026-09-07",
                                         "model_source_date": "2026-09-04", "model_source": csv_paths["dataA"].name,
                                         "data_status": "OK", "rows": [row]})
    plan = tmp_path / "logs/entry_list_20260907.json"
    plans.publish_entry_artifact(plan, payload)
    dashboard.OUT.write_text("KEEP EXISTING HTML", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["entry_dashboard.py", "--date", "20260907"])
    return SimpleNamespace(root=tmp_path, daily=daily, model=model, plan=plan, frame=frame,
                           snapshot=snapshot, csv=csv_paths, publish=publish)


def change(path, *, retain_stat=False):
    before = path.stat()
    blob = path.read_bytes()
    path.write_bytes(blob.replace(b"102", b"103") if b"102" in blob else blob + b" ")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns if retain_stat else before.st_mtime_ns + 1000000))


def test_certified_reader_preserves_original_pandas_parser_and_complete_html(render_inputs, monkeypatch):
    data = render_inputs
    pd.testing.assert_frame_equal(dashboard._certified_snapshot(), pd.read_pickle(data.root / "ml/models/snapshot_cache.pkl"))
    for slot in ("dataA", "production"):
        options = {"low_memory": False} if slot == "production" else {}
        pd.testing.assert_frame_equal(dashboard._certified_predictions(data.csv[slot], slot, **options),
                                      pd.read_csv(data.csv[slot], **options), check_exact=True)
    class FixedDateTime:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 9, 6, 23, 30)
    monkeypatch.setattr(dashboard, "datetime", FixedDateTime)
    # Keep date parsing available while fixing the generated display timestamp.
    from datetime import datetime
    FixedDateTime.strptime = staticmethod(datetime.strptime)
    certified = dashboard.build(data.plan)
    monkeypatch.setattr(dashboard, "_certified_snapshot", lambda **kwargs: pd.read_pickle(data.root / "ml/models/snapshot_cache.pkl"))
    monkeypatch.setattr(dashboard, "_certified_predictions", lambda path, slot, **kwargs:
                        pd.read_csv(path, **{k: v for k, v in kwargs.items() if k != "inputs"}))
    monkeypatch.setattr(plans, "load_entry_artifact", lambda path: json.loads(path.read_text(encoding="utf-8")))
    assert dashboard.build(data.plan) == certified
    assert "2330 台積電" in certified and '"trade_date": "2026-09-07"' in certified


@pytest.mark.parametrize("kind", ["snapshot", "dataA", "production"])
def test_required_readers_reject_missing_certificates(render_inputs, kind):
    data = render_inputs
    path = data.root / "ml/models/snapshot_cache.pkl" if kind == "snapshot" else data.csv[kind]
    Path(str(path) + ".manifest.json").unlink()
    with pytest.raises((dashboard.DashboardLineageError, plans.EntryArtifactLineageError)):
        dashboard.main()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


@pytest.mark.parametrize("reader", ["snapshot", "dataA", "production"])
def test_same_date_source_correction_rejects_each_direct_reader(render_inputs, reader):
    change(render_inputs.daily)
    with pytest.raises(dashboard.DashboardLineageError, match="stale"):
        if reader == "snapshot":
            dashboard._certified_snapshot()
        else:
            dashboard._certified_predictions(render_inputs.csv[reader], reader)


def test_old_plan_cannot_use_new_successful_same_day_prediction(render_inputs):
    render_inputs.publish("dataA")  # Same numeric scores, new actual run certificate.
    with pytest.raises(plans.EntryArtifactLineageError, match="changed"):
        dashboard.main()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


@pytest.mark.parametrize("during", ["source", "model", "production_same_stat", "new_source", "presentation"])
def test_mid_render_changes_cannot_overwrite_existing_html(render_inputs, monkeypatch, during):
    data = render_inputs
    def mutate():
        if during == "source":
            change(data.daily)
        elif during == "model":
            change(data.model)
        elif during == "production_same_stat":
            change(data.csv["production"], retain_stat=True)
        elif during == "new_source":
            (data.root / "日K資料/2317.csv").write_text("Date,Close\n2026-09-04,100\n", encoding="utf-8")
        else:
            (data.root / "ml/reports").mkdir(parents=True)
            (data.root / "ml/reports/industry_news_latest.md").write_text("new presentation input", encoding="utf-8")
        return {"rows": [], "asof": None}
    monkeypatch.setattr(dashboard, "load_daytrade", mutate)
    with pytest.raises((ValueError, RuntimeError), match="changed|rejected"):
        dashboard.main()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


def test_historical_date_cannot_be_rendered_with_current_raw_sources(render_inputs, monkeypatch):
    monkeypatch.setattr(plans, "_live_dates", lambda: ("2026-09-07", "2026-09-08"))
    with pytest.raises(plans.EntryArtifactLineageError, match="Historical|noncurrent"):
        dashboard.main()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


def test_legacy_plan_is_not_recertified_by_renderer(render_inputs):
    Path(str(render_inputs.plan) + ".manifest.json").unlink()
    with pytest.raises(dashboard.DashboardLineageError, match="missing"):
        dashboard.main()
    assert not Path(str(render_inputs.plan) + ".manifest.json").exists()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


def test_corrupt_snapshot_is_not_loaded_even_with_unchanged_dates(render_inputs):
    path = render_inputs.root / "ml/models/snapshot_cache.pkl"
    path.write_bytes(b"corrupted snapshot")
    with pytest.raises(dashboard.DashboardLineageError, match="invalid"):
        dashboard._certified_snapshot()
    with pytest.raises(plans.EntryArtifactLineageError):
        dashboard.main()
    assert dashboard.OUT.read_text(encoding="utf-8") == "KEEP EXISTING HTML"


def test_older_production_csv_is_not_selected_as_a_current_plan_supplement(render_inputs):
    from datetime import date
    path = render_inputs.csv["production"]
    previous = path.with_name("predictions_2026-09-03.csv")
    path.replace(previous)
    Path(str(path) + ".manifest.json").replace(Path(str(previous) + ".manifest.json"))
    # The score certificate remains valid; filename/plan date alignment must too.
    assert predictions.load_prediction_csv(previous) is not None
    with pytest.raises(dashboard.DashboardLineageError, match="date differs"):
        dashboard.load_model_tags(not_after=date(2026, 9, 4))


def test_certified_production_fallback_uses_exact_plan_source_when_dataa_absent(render_inputs):
    from datetime import date
    data = render_inputs
    payload = plans.load_entry_artifact(data.plan)
    data.csv["dataA"].unlink()
    Path(str(data.csv["dataA"]) + ".manifest.json").unlink()
    plans._generator()._latest_predictions_path = lambda as_of: data.csv["production"]
    run = plans.capture_generation("2026-09-04", "2026-09-07")
    result = plans.bind_generation(run, {"as_of_date": "2026-09-04", "trade_date": "2026-09-07",
        "as_of_close": str(data.csv["production"]), "rows": payload["rows"]})
    body = {key: value for key, value in payload.items() if key != plans.PROOF}
    body["model_source"] = data.csv["production"].name
    plans.publish_entry_artifact(data.plan, plans.bind_payload(result, body))
    assert plans.load_entry_artifact(data.plan)["model_source"] == "predictions_2026-09-04.csv"
    html = dashboard.build(data.plan)
    assert '"file": "predictions_2026-09-04.csv"' in html
    stocks, dates = dashboard.load_universe({"2330"}, model_source=body["model_source"],
                                            model_source_date=date(2026, 9, 4))
    assert stocks["2330"]["p"] == round(float(data.frame["pred_return_20d"].iloc[0]), 4)
    assert dates["data_a"] == "2026-09-04"  # Existing generic 20D display key.


def test_plan_selected_prediction_slot_must_match_certificate(render_inputs):
    from datetime import date
    data = render_inputs
    # Valid DataA pair placed at the production filename remains the wrong slot.
    data.csv["production"].write_bytes(data.csv["dataA"].read_bytes())
    Path(str(data.csv["production"]) + ".manifest.json").write_bytes(
        Path(str(data.csv["dataA"]) + ".manifest.json").read_bytes())
    assert predictions.load_prediction_csv(data.csv["production"], expected_model_slot="dataA") is not None
    with pytest.raises(dashboard.DashboardLineageError, match="production prediction certificate"):
        dashboard.load_universe({"2330"}, model_source=data.csv["production"].name,
                                model_source_date=date(2026, 9, 4))
