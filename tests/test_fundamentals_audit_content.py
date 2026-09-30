from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from scripts import fundamentals_completeness_audit as audit


def quarter(path, **changes):
    data = {"Ticker": ["2330", "8046"], "Market": ["TWSE", "OTC"],
            "Year": [2026, 2026], "Season": [2, 2], "EPS_Basic": [2.0, 1.0]}
    data.update(changes)
    pd.DataFrame(data).to_csv(path, index=False)


@pytest.mark.parametrize("changes,reason", [
    ({"Market": ["TWSE", "TWSE"]}, "雙市場"),
    ({"Season": [1, 1]}, "期別"),
    ({"EPS_Basic": [1.0, float("nan")]}, "全缺"),
    ({"Ticker": ["2330", "2330"]}, "重複"),
])
def test_nonempty_quarter_still_rejects_partial_or_wrong_content(tmp_path, changes, reason):
    quarter(tmp_path / "eps_2026Q2.csv", **changes)
    assert reason in audit._check_quarterly("季EPS", str(tmp_path), "eps_", (2026, 2))


def test_valid_dual_market_quarter(tmp_path):
    quarter(tmp_path / "eps_2026Q2.csv")
    assert audit._check_quarterly("季EPS", str(tmp_path), "eps_", (2026, 2)) is None


def test_missing_historical_quarter_is_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(audit, "FIN_DIR", str(tmp_path))
    monkeypatch.setattr(audit, "BS_DIR", str(tmp_path))
    monkeypatch.setattr(audit, "_latest_due_quarter", lambda today: (2020, 2))
    monkeypatch.setattr(audit, "_check_monthly_revenue", lambda today: None)
    monkeypatch.setattr(audit, "_check_tdcc_freshness", lambda today: None)
    monkeypatch.setattr(audit, "_check_semantic_invariants", lambda: [])
    gaps = audit.run_audit(today=date(2020, 9, 6))
    assert any("季EPS 2020Q1 歷史缺檔" in gap for gap in gaps)
    assert any("季EPS 2020Q2 缺檔" in gap for gap in gaps)


def test_early_next_month_report_does_not_hide_due_revenue(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "REVENUE_DIR", str(tmp_path))
    pd.DataFrame({"Date": ["2026-06", "2026-07", "2026-08"],
                  "Monthly_Revenue": [100, 110, 120]}).to_csv(tmp_path / "revenue_2330.csv", index=False)
    assert audit._check_monthly_revenue(date(2026, 9, 6)) is None


def test_due_revenue_does_not_imply_prior_month_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "REVENUE_DIR", str(tmp_path))
    pd.DataFrame({"Date": ["2026-07"], "Monthly_Revenue": [100]}).to_csv(tmp_path / "revenue_2330.csv", index=False)
    assert "2026-06 全缺" in audit._check_monthly_revenue(date(2026, 9, 6))


def test_invalid_first_duplicate_revenue_row_does_not_hide_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "REVENUE_DIR", str(tmp_path))
    pd.DataFrame({"Date": ["2026-06", "2026-07", "2026-07"], "Monthly_Revenue": [100, float("nan"), 120]}).to_csv(tmp_path / "revenue_2330.csv", index=False, na_rep="nan")
    assert "期別重複" in audit._check_monthly_revenue(date(2026, 9, 6))


def test_friday_cannot_accept_two_week_old_tdcc(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "BASE_DIR", str(tmp_path))
    folder = tmp_path / "集保分散"
    folder.mkdir()
    (folder / "tdcc_20260821.csv").write_text("fixture", encoding="utf-8")
    assert "過期" in audit._check_tdcc_freshness(date(2026, 9, 4))


@pytest.mark.parametrize("whale", [0, float("nan"), 2.63])
def test_zero_or_nan_whale_cannot_bypass_semantic_invariant(monkeypatch, tmp_path, whale):
    monkeypatch.setattr(audit, "BASE_DIR", str(tmp_path))
    folder = tmp_path / "集保分散"
    folder.mkdir()
    pd.DataFrame({"Ticker": ["2330"], "Date": ["20260904"],
                  "Retail_Pct": [2.0], "Whale_Pct": [whale]}).to_csv(folder / "tdcc_summary.csv", index=False)
    assert any("2330" in gap for gap in audit._check_semantic_invariants())
