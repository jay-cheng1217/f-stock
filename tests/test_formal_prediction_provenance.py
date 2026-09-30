"""The output auditor requires each product's provenance and own dates."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import audit_formal_data_consistency as audit


@pytest.fixture
def output_case(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_BASE_DIR", str(tmp_path))
    frame = pd.DataFrame({"ticker": ["2330", "5371"],
                          "Date": ["2026-09-04", "2026-09-02"],
                          "Open": [100.0, 40.0], "High": [102.0, 42.0],
                          "Low": [99.0, 39.0], "Close": [101.0, 41.0],
                          "Volume": [1000000, 500000]})
    daily = tmp_path / "日K資料"
    daily.mkdir()
    for row in frame.to_dict("records"):
        ticker = row.pop("ticker")
        pd.DataFrame([row]).to_csv(daily / f"{ticker}.csv", index=False)
    snapshot_path = tmp_path / "snapshot.pkl"
    frame.to_pickle(snapshot_path)
    Path(str(snapshot_path) + ".manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("ml.snapshot_lineage.load_snapshot", lambda path: frame.copy())
    prediction_path = tmp_path / "predictions_2026-09-04.csv"
    prediction = frame.rename(columns={"Date": "date"})
    prediction.to_csv(prediction_path, index=False)
    dataa = tmp_path / "dataA_predictions_2026-09-04.csv"
    frame[["ticker", "Date"]].rename(columns={"Date": "source_date"}).to_csv(dataa, index=False)
    Path(str(dataa) + ".manifest.json").write_text("{}", encoding="utf-8")
    row = {"stock": "2330 台積電", "priority": "1", "source_date": "2026-09-04",
           "model_source_date": "2026-09-04", "data_status": "OK"}
    plan = {"trade_date": "2026-09-07", "as_of_date": "2026-09-04",
            "model_source_date": "2026-09-04", "model_source": dataa.name, "rows": [row]}
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps(plan), encoding="utf-8")
    Path(str(entry) + ".manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("scripts.entry_artifact_lineage.load_entry_artifact", lambda path: json.loads(path.read_text(encoding="utf-8")))
    html = tmp_path / "entry.html"
    html.write_text('<script id="data" type="application/json">' +
                    json.dumps({"meta": {"trade_date": "2026-09-07"}, "rows": [row]}) +
                    '</script>', encoding="utf-8")
    args = SimpleNamespace(root=tmp_path, output=tmp_path / "output/audit.json",
                           stage="outputs", as_of="2026-09-04", trade_date="2026-09-07",
                           snapshot=snapshot_path, predictions=prediction_path,
                           dataa=dataa, entry=entry, html=html, db=tmp_path / "absent.duckdb")
    return args, prediction


def install_loader(monkeypatch, function):
    # API boundary only: no real model, production source or DB is read.
    monkeypatch.setitem(sys.modules, "ml.prediction_provenance",
                        SimpleNamespace(load_prediction_csv=function))


def test_missing_production_provenance_fails_outputs_and_cli(output_case, monkeypatch):
    args, _ = output_case
    calls = []
    def rejected(path, *, expected_model_slot, read_csv_kwargs):
        calls.append((path, expected_model_slot))
        return None
    install_loader(monkeypatch, rejected)
    monkeypatch.setattr(sys, "argv", ["audit", "--root", str(args.root), "--stage", "outputs",
        "--as-of", args.as_of, "--trade-date", args.trade_date, "--output", str(args.output),
        "--snapshot", str(args.snapshot), "--predictions", str(args.predictions),
        "--dataa", str(args.dataa), "--entry", str(args.entry), "--html", str(args.html)])
    assert audit.main() == 1
    report = json.loads(args.output.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["checks"]["production_prediction_lineage"] is False
    assert "lineage rejected or absent" in report["error"]
    assert calls == [(args.predictions, "production")]
    assert not args.db.exists()


def test_certified_frame_used_and_dataa_source_date_still_accepted(output_case, monkeypatch):
    args, prediction = output_case
    certificate = {"schema": 1, "model_slot": "production", "fixture": True}
    manifest = Path(str(args.predictions) + ".manifest.json")
    manifest.write_text(json.dumps(certificate), encoding="utf-8")
    calls = []
    def accepted(path, *, expected_model_slot, read_csv_kwargs):
        calls.append((path, expected_model_slot))
        assert read_csv_kwargs["float_precision"] == "round_trip"
        if expected_model_slot == "dataA":
            return prediction[["ticker", "date"]].rename(columns={"date": "source_date"}), {}
        return prediction.copy(), certificate
    install_loader(monkeypatch, accepted)
    real_read_csv = pd.read_csv
    def no_production_reparse(path, *positional, **keywords):
        if Path(path) == args.predictions:
            raise AssertionError("Auditor must use the helper's already-verified frame")
        return real_read_csv(path, *positional, **keywords)
    monkeypatch.setattr(audit.pd, "read_csv", no_production_reparse)
    before = {path: audit.file_receipt(path)["sha256"] for path in (args.predictions, args.dataa, manifest)}
    report = audit.run(args)
    assert report["status"] == "PASS"
    assert report["checks"]["production_prediction_lineage"] is True
    assert report["production_prediction_lineage"] == certificate
    assert report["production_prediction_manifest_receipt"]["sha256"] == before[manifest]
    assert report["checks"]["dataA_snapshot_identities"] is True
    assert calls == [(args.predictions, "production"), (args.dataa, "dataA")]
    assert report["checks"]["dataA_prediction_lineage"] is True
    assert not args.db.exists()
    assert before == {path: audit.file_receipt(path)["sha256"] for path in before}


def test_dataa_conflicting_dates_still_fail_after_production_certificate(output_case, monkeypatch):
    args, prediction = output_case
    Path(str(args.predictions) + ".manifest.json").write_text("{}", encoding="utf-8")
    install_loader(monkeypatch, lambda path, **kwargs: (pd.read_csv(path, dtype={"ticker": str}), {}))
    dataa = pd.read_csv(args.dataa, dtype={"ticker": str})
    dataa["date"] = "2026-09-04"
    dataa.to_csv(args.dataa, index=False)
    report = audit.run(args)
    assert report["status"] == "FAIL"
    assert report["checks"]["production_prediction_lineage"] is True
    assert "Date/date/source_date identities disagree" in report["error"]


def test_missing_dataa_certificate_is_not_an_empty_candidate_list(output_case, monkeypatch):
    args, prediction = output_case
    Path(str(args.predictions) + ".manifest.json").write_text("{}", encoding="utf-8")
    install_loader(monkeypatch, lambda path, **kwargs: (prediction.copy(), {}) if path == args.predictions else None)
    report = audit.run(args)
    assert report["status"] == "FAIL"
    assert report["checks"]["dataA_prediction_lineage"] is False
    assert "dataA prediction lineage rejected or absent" in report["error"]


@pytest.mark.parametrize("changed", ["csv", "manifest"])
def test_replacement_during_validation_is_rejected(output_case, monkeypatch, changed):
    args, prediction = output_case
    manifest = Path(str(args.predictions) + ".manifest.json")
    manifest.write_text("{}", encoding="utf-8")
    def replaced(path, **kwargs):
        target = args.predictions if changed == "csv" else manifest
        target.write_bytes(target.read_bytes() + b"\n")
        return prediction.copy(), {}
    install_loader(monkeypatch, replaced)
    report = audit.run(args)
    assert report["status"] == "FAIL"
    assert report["checks"]["production_prediction_lineage"] is False
    assert "changed during validation" in report["error"]
