"""Adversarial data-contract regressions. All external inputs and writes isolated."""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
import pytest

from scripts import generate_entry_candidates as entry
from scripts import shadow_dataA_tracker as shadow
from scripts import smart_update_auto as pipeline


ASOF = date(2026, 9, 4)
TRADE = "2026-09-07"


def _technical(*, rere=True):
    return {"signal_date": ASOF.isoformat(), "close": 90.5, "v20": -.005, "v60": .08,
            "rsi": 55., "vol_ratio": .9, "macd_delta_rel": .001, "ma20": 91.,
            "elow": 89., "ehigh": 92., "foreign20": 10., "vol_today_lots": 800.,
            "wash_from_hi10": .105 if rere else .01, "vr_today": .9,
            "foreign_prev5": -10., "foreign_today": 2., "trust_today": 0.,
            "day_ret": 0., "prev_below_ma20": True}


def _configure_entry(tmp_path, monkeypatch, predictions, margins, *, rere=True):
    monkeypatch.setattr(entry, "BASE_DIR", tmp_path)
    monkeypatch.setattr(entry, "_load_predictions", lambda *_: predictions.copy())
    monkeypatch.setattr(entry, "_load_chipk", lambda *_: pd.DataFrame())
    monkeypatch.setattr(entry, "load_disposition_gate", lambda *a, **k: {
        "complete": True, "blocked_tickers": [], "reasons": []})
    monkeypatch.setattr(entry, "_sector_map", lambda: {tk: "fixture" for tk in predictions["ticker"]})
    monkeypatch.setattr(entry, "_snapshot_fields", lambda *_: ({tk: ASOF.isoformat() for tk in predictions["ticker"]}, margins))
    monkeypatch.setattr(entry, "_technical", lambda *a, **k: _technical(rere=rere))
    monkeypatch.setattr(entry, "_twii_above_ma60", lambda *_: True)
    monkeypatch.setattr(entry, "_latest_predictions_path", lambda *_: "dataA_predictions_2026-09-04.csv")
    monkeypatch.setattr(entry, "_chipk_desktop_enabled", lambda: False)


def test_missing_main_margin_cannot_indirectly_veto_valid_rere(tmp_path, monkeypatch):
    pred = pd.DataFrame({"ticker": ["1111"], "source_date": [ASOF.isoformat()],
                         "pred_return_20d": [.1], "recommendation": ["建議買進"]})
    _configure_entry(tmp_path, monkeypatch, pred, {})
    result = entry.generate(ASOF.isoformat(), trade_date=TRADE, include_live=False)
    rows = [r for r in result["rows"] if r["ticker"] == "1111"]
    assert len(rows) == 1
    assert rows[0]["lane"] == "rere"
    assert rows[0]["kind"] == "small"
    assert rows[0]["data_status"] == "OK"


def test_unknown_main_candidate_cannot_use_valid_candidate_slot(tmp_path, monkeypatch):
    tickers = [str(1000 + n) for n in range(13)]
    pred = pd.DataFrame({"ticker": tickers, "source_date": ASOF.isoformat(),
                         "pred_return_20d": [1.] + [.1] * 12, "recommendation": "建議買進"})
    _configure_entry(tmp_path, monkeypatch, pred, {tk: 3. for tk in tickers[1:]}, rere=False)
    result = entry.generate(ASOF.isoformat(), trade_date=TRADE, include_live=False)
    eligible = [r for r in result["rows"] if r["kind"] in ("go", "small")]
    assert {r["ticker"] for r in eligible} == set(tickers[1:])


def test_legacy_prediction_cannot_borrow_current_snapshot_date(tmp_path, monkeypatch):
    path = tmp_path / "dataA_predictions_2026-09-04.csv"
    pd.DataFrame({"ticker": ["1111"], "pred_return_20d": [.1]}).to_csv(path, index=False)
    monkeypatch.setattr(entry, "_latest_predictions_path", lambda *_: str(path))
    monkeypatch.setattr(entry, "_snapshot_fields", lambda *_: ({"1111": ASOF.isoformat()}, {"1111": 3.}))
    with pytest.raises(ValueError, match="prediction lineage rejected"):
        entry._load_predictions(ASOF)


class Booster:
    def __init__(self, scores=None):
        self.scores = scores

    def predict(self, frame):
        return np.zeros(len(frame)) if self.scores is None else np.array(self.scores)


def test_shadow_missing_model_column_fails_but_valid_missing_value_is_allowed():
    frame = pd.DataFrame({"ticker": ["1111"], "Date": [ASOF.isoformat()]})
    with pytest.raises(ValueError, match="missing.*feature"):
        shadow.score_snapshot(frame, {"feature_columns": ["alpha"]}, Booster())
    frame["alpha"] = np.nan
    result = shadow.score_snapshot(frame, {"feature_columns": ["alpha"]}, Booster())
    assert len(result) == 1


def test_shadow_duplicate_ticker_is_checked_after_normalization():
    frame = pd.DataFrame({"ticker": [1, "0001"], "Date": [ASOF.isoformat()] * 2, "alpha": [1, 2]})
    with pytest.raises(ValueError, match="ambiguous"):
        shadow.score_snapshot(frame, {"feature_columns": ["alpha"]}, Booster())


@pytest.mark.parametrize("scores", [[np.nan], [np.inf], []])
def test_shadow_invalid_score_vector_cannot_publish(scores):
    frame = pd.DataFrame({"ticker": ["1111"], "Date": [ASOF.isoformat()], "alpha": [1.]})
    with pytest.raises(ValueError, match="incomplete|nonfinite"):
        shadow.score_snapshot(frame, {"feature_columns": ["alpha"]}, Booster(scores))


def _configure_pipeline(monkeypatch, *, failed_step=None, gaps=None, audit_gaps=None):
    from scripts import fundamentals_completeness_audit as audit
    calls = []
    def run_step(name, callback):
        # Deliberately never execute callbacks: no web process, DB or external write.
        calls.append(name)
        return name != failed_step
    monkeypatch.setattr(pipeline, "run_step", run_step)
    monkeypatch.setattr(pipeline, "_load_special_status_report", lambda: {})
    monkeypatch.setattr(pipeline, "_special_status_problem", lambda *_: False)
    monkeypatch.setattr(pipeline, "_check_data_freshness", lambda *a, **k: gaps or [])
    monkeypatch.setattr(pipeline, "_send_alert_email", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_send_special_status_email", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_chipk_desktop_enabled", lambda: False)
    monkeypatch.setattr(audit, "run_audit", lambda *a, **k: audit_gaps or [])
    # Evening certified candidates need the Champion snapshot as of the same
    # close; model the normal case here and the stale case in its own test.
    monkeypatch.setattr(pipeline, "_expected_twse_data_date", lambda *a, **k: date(2026, 9, 7))
    monkeypatch.setattr(pipeline, "_canonical_snapshot_asof", lambda: date(2026, 9, 7))
    monkeypatch.setitem(pipeline.RUN_CONTEXT, "entry_evening_warning", None)
    logger = logging.getLogger("test_strategy_pipeline_integrity")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    monkeypatch.setattr(pipeline, "LOGGER", logger)
    return calls


def test_phase1_failed_official_bar_check_blocks_ingest_and_candidates(monkeypatch):
    calls = _configure_pipeline(monkeypatch, failed_step="Verify daily bars vs official (TWSE+TPEX)")
    pipeline._run_phase1_tw_data()
    assert "Ingest refreshed data into DuckDB" not in calls
    assert "Shadow dataA model tracker" not in calls
    assert "Entry candidates + rere lane tracker" not in calls


def test_phase1_critical_freshness_gap_blocks_model_and_candidates(monkeypatch):
    calls = _configure_pipeline(monkeypatch, gaps=["日K當日來源缺失"])
    pipeline._run_phase1_tw_data()
    assert "Shadow dataA model tracker" not in calls
    assert "Entry candidates + rere lane tracker" not in calls


def test_phase1_incomplete_fundamentals_block_model_and_candidates(monkeypatch):
    calls = _configure_pipeline(monkeypatch, audit_gaps=["financial coverage 20%"])
    pipeline._run_phase1_tw_data()
    assert "Shadow dataA model tracker" not in calls
    assert "Entry candidates + rere lane tracker" not in calls


def test_phase1_success_orders_ingest_then_shadow_then_candidates(monkeypatch):
    calls = _configure_pipeline(monkeypatch)
    pipeline._run_phase1_tw_data()
    assert calls.index("Ingest refreshed data into DuckDB") < calls.index("Shadow dataA model tracker")
    assert calls.index("Shadow dataA model tracker") < calls.index("Entry candidates + rere lane tracker")


def test_phase1_stale_canonical_snapshot_degrades_candidates_instead_of_failing(monkeypatch):
    calls = _configure_pipeline(monkeypatch)
    monkeypatch.setattr(pipeline, "_canonical_snapshot_asof", lambda: date(2026, 9, 4))
    results = pipeline._run_phase1_tw_data()
    assert "Shadow dataA model tracker" in calls
    assert "Entry candidates + rere lane tracker" not in calls
    assert ("Entry candidates + rere lane tracker", True) in results
    assert "Update agent arena" in calls
    assert any(w.startswith("entry_evening_skipped") for w in pipeline._runtime_degraded_steps())


def test_step_explicit_failure_is_not_done():
    assert pipeline.run_step("failure-fixture", lambda: False) is False
    assert pipeline.run_step("none-is-success", lambda: None) is True


def test_official_verification_exception_is_not_success(monkeypatch):
    def fail(*a, **kw):
        raise TimeoutError("fixture")
    monkeypatch.setattr(pipeline.subprocess, "run", fail)
    assert pipeline.exec_verify_daily_bars() is False
