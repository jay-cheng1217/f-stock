from __future__ import annotations

from pathlib import Path

from scripts.entry_dashboard import (
    HTML,
    _entry_model_metadata,
    _latest_dated_file,
    _source_freshness,
)


def test_latest_dated_file_ignores_aliases_backups_and_invalid_dates(tmp_path: Path) -> None:
    for name in (
        "entry_list_20260701.json",
        "entry_list_20260703.json",
        "entry_list_latest.json",
        "entry_list_99999999.json",
        "entry_list_20260704.json.bak",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    result = _latest_dated_file(
        tmp_path, prefix="entry_list_", suffix=".json", date_format="%Y%m%d"
    )

    assert result is not None
    path, source_date = result
    assert path.name == "entry_list_20260703.json"
    assert source_date.isoformat() == "2026-07-03"


def test_source_freshness_distinguishes_daily_and_weekly_cadence() -> None:
    result = _source_freshness("2026/07/17", [
        ("snapshot", "四面向", "2026-07-15", "daily"),
        ("model", "正式標籤", "2026-07-16", "daily"),
        ("tdcc", "集保週報", "2026-07-09", "weekly"),
        ("whale", "大戶雷達", "2026-07-03", "weekly"),
    ])
    by_key = {row["key"]: row for row in result}

    assert by_key["snapshot"]["status"] == "stale"
    assert by_key["snapshot"]["expected"] == "2026-07-16"
    assert by_key["model"]["status"] == "fresh"
    assert by_key["tdcc"]["status"] == "fresh"
    assert by_key["tdcc"]["expected"] == "2026-07-09"
    assert by_key["whale"]["status"] == "stale"


def test_dashboard_renders_source_freshness_banner() -> None:
    assert "資料基準" in HTML
    assert "freshline" in HTML


def test_latest_dated_file_respects_plan_cutoff(tmp_path: Path) -> None:
    for name in ("predictions_2026-07-15.csv", "predictions_2026-07-16.csv"):
        (tmp_path / name).write_text("date\n", encoding="utf-8")

    cutoff = _entry_model_metadata({"model_source_date": "2026-07-15"})[1]
    result = _latest_dated_file(
        tmp_path,
        prefix="predictions_",
        suffix=".csv",
        date_format="%Y-%m-%d",
        not_after=cutoff,
    )

    assert result is not None
    assert result[0].name == "predictions_2026-07-15.csv"


def test_entry_model_metadata_reads_explicit_fields_before_intro() -> None:
    source, source_date = _entry_model_metadata({
        "model_source": "dataA_predictions_2026-07-16.csv",
        "model_source_date": "2026-07-16",
        "intro": "source dataA_predictions_2026-07-15.csv",
    })

    assert source == "dataA_predictions_2026-07-16.csv"
    assert source_date is not None
    assert source_date.isoformat() == "2026-07-16"
