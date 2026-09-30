from datetime import date

import pandas as pd

import twstock


def _margin_frame(day, ticker_prefix, count=60):
    return pd.DataFrame(
        {
            "Date": [day] * count,
            "Ticker": [f"{ticker_prefix}{index:03d}" for index in range(count)],
            "Margin_Balance": [100] * count,
            "Short_Balance": [10] * count,
        }
    )


def test_margin_gap_detects_tpex_only_missing(tmp_path, monkeypatch):
    target = date(2026, 7, 16)
    monkeypatch.setattr(twstock, "iter_taiwan_trading_days", lambda start, end: [target])
    monkeypatch.setattr(
        twstock,
        "_margin_raw_file_valid",
        lambda path, market, day: market == "TWSE",
    )

    assert twstock._find_missing_margin_dates(tmp_path, target) == [target]


def test_detect_margin_last_date_returns_latest_file(tmp_path, monkeypatch):
    monkeypatch.setattr(twstock, "RAW_MARGIN_DIR", str(tmp_path))
    (tmp_path / "raw_margin_twse_20260715.csv").write_text("old", encoding="utf-8")
    (tmp_path / "raw_margin_twse_20260716.csv").write_text("new", encoding="utf-8")

    assert twstock._detect_margin_last_date() == date(2026, 7, 16)


def test_margin_merge_start_reopens_historical_correction():
    start = twstock._resolve_margin_merge_start("2026-07-16", "2026-06-24")

    assert start == "2026-06-24"


def test_clean_merge_skips_partial_day_and_preserves_existing(tmp_path, monkeypatch):
    cleaned = tmp_path / "cleaned.csv"
    status = tmp_path / "status.json"
    raw = tmp_path / "raw"
    raw.mkdir()
    old = _margin_frame("2026-07-16", "1")
    old.to_csv(cleaned, index=False, encoding="utf-8-sig")

    monkeypatch.setattr(twstock, "CLEANED_MARGIN_FILE", str(cleaned))
    monkeypatch.setattr(twstock, "MARGIN_MERGE_STATUS_FILE", str(status))
    monkeypatch.setattr(twstock, "RAW_MARGIN_DIR", str(raw))
    monkeypatch.setattr(
        twstock,
        "_clean_twse_margin",
        lambda path, day: _margin_frame(day, "1"),
    )
    monkeypatch.setattr(
        twstock,
        "_clean_tpex_margin",
        lambda path, day: pd.DataFrame(columns=twstock.TARGET_COLUMNS),
    )

    twstock._clean_and_merge_margin(date(2026, 7, 16), date(2026, 7, 16))

    result = pd.read_csv(cleaned, dtype={"Ticker": str})
    assert len(result) == len(old)
    assert not status.exists()


def test_clean_merge_marks_earliest_complete_date_dirty(tmp_path, monkeypatch):
    cleaned = tmp_path / "cleaned.csv"
    status = tmp_path / "status.json"
    raw = tmp_path / "raw"
    raw.mkdir()

    monkeypatch.setattr(twstock, "CLEANED_MARGIN_FILE", str(cleaned))
    monkeypatch.setattr(twstock, "MARGIN_MERGE_STATUS_FILE", str(status))
    monkeypatch.setattr(twstock, "RAW_MARGIN_DIR", str(raw))
    monkeypatch.setattr(
        twstock,
        "_clean_twse_margin",
        lambda path, day: _margin_frame(day, "1"),
    )
    monkeypatch.setattr(
        twstock,
        "_clean_tpex_margin",
        lambda path, day: _margin_frame(day, "6"),
    )

    twstock._clean_and_merge_margin(date(2026, 7, 15), date(2026, 7, 16))

    assert twstock._read_margin_merge_status()["pending_remerge_from"] == "2026-07-15"
    result = pd.read_csv(cleaned)
    assert len(result) == 240
