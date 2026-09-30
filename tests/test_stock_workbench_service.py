import pandas as pd

import backend.services.stock_workbench_service as workbench_service
from backend.services.stock_workbench_service import (
    _calculate_performance,
    _extract_feature_importance_rows,
    _load_unified_signal,
    _serialize_prediction_row,
    _safe_ohlc,
)


def test_calculate_performance_reports_drawdown_and_total_return():
    frame = pd.DataFrame({"Close": [100.0, 110.0, 99.0, 120.0]})

    result = _calculate_performance(frame)

    assert result["window_days"] == 4
    assert result["total_return_pct"] == 20.0
    assert result["max_drawdown_pct"] == -10.0
    assert result["annual_volatility_pct"] is not None
    assert result["sharpe"] is not None


def test_extract_feature_importance_rows_normalizes_top_features():
    frame = pd.DataFrame(
        {
            "feature": ["volatility_20d", "twii_return_20d", "vix_level"],
            "importance": [50.0, 30.0, 20.0],
        }
    )

    rows = _extract_feature_importance_rows(frame, limit=2)

    assert rows == [
        {"feature": "volatility_20d", "importance": 50.0, "share_pct": 62.5},
        {"feature": "twii_return_20d", "importance": 30.0, "share_pct": 37.5},
    ]


def test_safe_ohlc_replaces_nan_with_none():
    frame = pd.DataFrame(
        {
            "Open": [10.0],
            "Close": [11.0],
            "Low": [float("nan")],
            "High": [12.0],
        }
    )

    assert _safe_ohlc(frame) == [[10.0, 11.0, None, 12.0]]


def test_serialize_prediction_row_preserves_raw_and_adds_pct_fields():
    row = pd.Series(
        {
            "pred_return_20d": 0.032043,
            "prob_edge": -0.18136,
            "gap_pct": 0.048,
            "beta_60": 1.103323,
            "recommendation": "觀察",
        }
    )

    result = _serialize_prediction_row(row)

    assert result["pred_return_20d"] == 0.032043
    assert result["pred_return_20d_pct"] == 3.2
    assert result["prob_edge"] == -0.18136
    assert result["prob_edge_pct"] == -18.14
    assert result["gap_pct_pct"] == 4.8
    assert result["beta_60"] == 1.103323


def test_load_unified_signal_rebuilds_stale_feed(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    stale_path = report_dir / "unified_signals_latest.json"
    stale_path.write_text(
        """
        {
          "source_signal_path": "F:/stock/ml/models/unified_signals_2026-05-28.csv",
          "selected": [{"ticker": "1111", "bucket_value": "stale"}]
        }
        """,
        encoding="utf-8",
    )
    latest_path = tmp_path / "unified_signals_2026-05-29.csv"
    latest_path.write_text("ticker\n2222\n", encoding="utf-8")

    monkeypatch.setattr(workbench_service, "REPORT_DIR", report_dir)

    import backend.services.warroom_service as warroom_service

    monkeypatch.setattr(warroom_service, "_latest_csv_path", lambda prefix: str(latest_path))
    monkeypatch.setattr(
        warroom_service,
        "build_unified_signals_latest_feed",
        lambda: {
            "source_signal_path": str(latest_path),
            "selected": [{"ticker": "2222", "bucket_value": "fresh"}],
        },
    )

    result, source = _load_unified_signal("2222")

    assert result["bucket_value"] == "fresh"
    assert result["bucket"] == "selected"
    assert source == str(latest_path)


def test_load_unified_signal_reports_cached_source_signal_path(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    source_signal_path = tmp_path / "unified_signals_2026-05-29.csv"
    source_signal_path.write_text("ticker\n2222\n", encoding="utf-8")
    (report_dir / "unified_signals_latest.json").write_text(
        f"""
        {{
          "source_signal_path": "{str(source_signal_path).replace(chr(92), chr(92) + chr(92))}",
          "selected": [{{"ticker": "2222", "bucket_value": "fresh"}}]
        }}
        """,
        encoding="utf-8",
    )

    monkeypatch.setattr(workbench_service, "REPORT_DIR", report_dir)

    import backend.services.warroom_service as warroom_service

    monkeypatch.setattr(warroom_service, "_latest_csv_path", lambda prefix: str(source_signal_path))

    result, source = _load_unified_signal("2222")

    assert result["bucket_value"] == "fresh"
    assert source == str(source_signal_path)


def test_load_unified_signal_rebuilds_corrupt_feed(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "unified_signals_latest.json").write_text("{not json", encoding="utf-8")
    latest_path = tmp_path / "unified_signals_2026-05-29.csv"
    latest_path.write_text("ticker\n2360\n", encoding="utf-8")

    monkeypatch.setattr(workbench_service, "REPORT_DIR", report_dir)

    import backend.services.warroom_service as warroom_service

    monkeypatch.setattr(warroom_service, "_latest_csv_path", lambda prefix: str(latest_path))
    monkeypatch.setattr(
        warroom_service,
        "build_unified_signals_latest_feed",
        lambda: {
            "source_signal_path": str(latest_path),
            "selected": [{"ticker": "2360", "bucket_value": "rebuilt"}],
        },
    )

    result, source = _load_unified_signal("2360")

    assert result["bucket_value"] == "rebuilt"
    assert result["bucket"] == "selected"
    assert source == str(latest_path)
