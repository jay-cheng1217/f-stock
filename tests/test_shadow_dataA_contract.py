import json
import numpy as np
import pandas as pd
import pytest

from scripts import shadow_dataA_tracker as tracker


class Booster:
    def predict(self, frame):
        return frame["f"].to_numpy() * .01


def test_score_snapshot_preserves_dates_and_scores():
    frame = pd.DataFrame({"ticker": ["1234", "2330"], "Date": ["2026-09-02", "2026-09-04"], "f": [1., 2.]})
    out = tracker.score_snapshot(frame, {"feature_columns": ["f"]}, Booster())
    assert out["source_date"].tolist() == ["2026-09-02", "2026-09-04"]
    assert out["pred_return_20d"].tolist() == pytest.approx([.01, .02])
    assert out["operating_margin_latest"].isna().all()


def test_snapshot_without_date_fails():
    with pytest.raises(ValueError):
        tracker.score_snapshot(pd.DataFrame({"ticker": ["2330"], "f": [1]}), {}, Booster())


def test_record_repairs_score_file_without_duplicate_or_stale_champion(monkeypatch, tmp_path):
    import lightgbm
    from ml import snapshot_lineage as lineage, prediction_provenance as provenance
    models = tmp_path / "ml/models"
    models.mkdir(parents=True)
    meta = models / "meta.json"
    meta.write_text(json.dumps({"model_file": "model.txt", "feature_columns": ["f"]}))
    (models / "model.txt").write_text("fixture weights")
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [])
    monkeypatch.setattr(provenance, "_prediction_patterns", lambda paths: [p for spec in paths.values() for p in spec.values()])
    snapshot = models / "snapshot.pkl"
    lineage.save_snapshot(pd.DataFrame({"ticker": ["2330", "1234"],
        "Date": ["2026-09-04", "2026-09-02"], "f": [1., 2.]}), snapshot,
        lineage.source_state(include_hashes=True))
    old = models / "dataA_predictions_2026-09-03.csv"
    old.write_text("old scores")
    (models / "predictions_2026-09-03.csv").write_text("ticker,pred_return_20d\n2330,.3\n")
    ledger = tmp_path / "ledger.csv"
    pd.DataFrame([{"date": "2026-09-04", "side": "shadow", "rank": 1, "ticker": "2330",
                   "score": .01, "ret20_pct": np.nan}]).to_csv(ledger, index=False)
    monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tracker, "SHADOW_META", meta)
    monkeypatch.setattr(tracker, "SNAPSHOT_PKL", snapshot)
    monkeypatch.setattr(tracker, "LEDGER", ledger)
    monkeypatch.setattr(lightgbm, "Booster", lambda **kw: Booster())
    tracker.record(expected_asof="2026-09-04")
    tracker.record(expected_asof="2026-09-04")
    assert (models / "dataA_predictions_2026-09-04.csv").exists()
    assert b"\r\r\n" not in (models / "dataA_predictions_2026-09-04.csv").read_bytes()
    assert old.read_text() == "old scores"
    assert len(pd.read_csv(ledger)) == 1
    with pytest.raises(ValueError, match="required"):
        tracker.record(expected_asof="2026-09-07")
