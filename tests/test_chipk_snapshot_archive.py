from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ml.chipk import CHIPK_DATE_RELATIVE, CHIPK_MAIN_FORCE_FILENAME, CHIPK_REQUIRED_COLUMNS
from scripts.archive_chipk_snapshot import archive_latest_chipk_snapshot


def _write_chipk_snapshot(appviewer_dir, *, asof: str = "20260618") -> None:
    chart_dir = appviewer_dir / "profile" / "UBSKChart"
    (chart_dir / CHIPK_DATE_RELATIVE.parent).mkdir(parents=True)
    (chart_dir / CHIPK_DATE_RELATIVE).write_text(asof, encoding="cp950")
    rows = [
        "^".join(CHIPK_REQUIRED_COLUMNS),
        "^".join(["2330", "台積電", "1000.00", "30059", "1", "2", "3"]),
        "^".join(["3550", "全球傳動", "33.90", "4661", "1", "1", "2"]),
    ]
    (chart_dir / CHIPK_MAIN_FORCE_FILENAME).write_text("\n".join(rows), encoding="cp950")


def test_archive_latest_chipk_snapshot_writes_archive_and_latest(tmp_path, monkeypatch):
    appviewer_dir = tmp_path / "AppViewer"
    archive_dir = tmp_path / "archive"
    latest_dir = tmp_path / "latest"
    _write_chipk_snapshot(appviewer_dir)
    monkeypatch.setenv("CMONEY_APPVIEWER_DIR", str(appviewer_dir))

    payload = archive_latest_chipk_snapshot(archive_dir=archive_dir, latest_dir=latest_dir)

    assert payload["archived"] is True
    assert payload["status"] == "ok"
    assert payload["asof_date"] == "2026-06-18"
    assert payload["row_count"] == 2
    assert payload["source_path"] == str(Path("...") / "UBSKChart" / CHIPK_MAIN_FORCE_FILENAME)

    archive_csv = archive_dir / "chipk_main_force_2026-06-18.csv"
    archive_json = archive_dir / "chipk_main_force_2026-06-18.json"
    latest_csv = latest_dir / "chipk_main_force_latest.csv"
    latest_json = latest_dir / "chipk_main_force_latest.json"
    assert archive_csv.exists()
    assert archive_json.exists()
    assert latest_csv.exists()
    assert latest_json.exists()

    archived = pd.read_csv(archive_csv, dtype={"ticker": str})
    assert archived["ticker"].tolist() == ["2330", "3550"]
    assert json.loads(latest_json.read_text(encoding="utf-8"))["archived"] is True


def test_archive_latest_chipk_snapshot_missing_source_is_nonfatal(tmp_path, monkeypatch):
    monkeypatch.setenv("CMONEY_APPVIEWER_DIR", str(tmp_path / "missing_AppViewer"))

    payload = archive_latest_chipk_snapshot(archive_dir=tmp_path / "archive", latest_dir=tmp_path / "latest")

    assert payload["archived"] is False
    assert payload["status"] == "missing"
    assert not (tmp_path / "archive").exists()
    assert not (tmp_path / "latest").exists()
