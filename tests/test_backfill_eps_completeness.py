from datetime import date

import pandas as pd
import pytest

from scripts import backfill_eps


def _write_rows(path, count):
    pd.DataFrame(
        {
            "Ticker": [f"{index:04d}" for index in range(count)],
            "EPS_Basic": [1.0] * count,
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")


def test_latest_quarter_waits_for_filing_deadline_and_grace():
    assert backfill_eps.get_latest_available_quarter(date(2026, 4, 2)) == (2025, 3)
    assert backfill_eps.get_latest_available_quarter(date(2026, 4, 3)) == (2025, 4)
    assert backfill_eps.get_latest_available_quarter(date(2026, 5, 17)) == (2025, 4)
    assert backfill_eps.get_latest_available_quarter(date(2026, 5, 18)) == (2026, 1)


def test_pending_selection_preserves_existing_partial_file(tmp_path):
    quarters = [(2025, 4), (2026, 1)]
    complete = tmp_path / "eps_2025Q4.csv"
    partial = tmp_path / "eps_2026Q1.csv"
    _write_rows(complete, 100)
    _write_rows(partial, 50)

    pending = backfill_eps.select_pending_quarters(quarters, str(tmp_path))

    assert pending == [(2026, 1)]
    assert partial.exists()
    assert backfill_eps._row_count(partial) == 50


def test_partial_market_response_is_rejected():
    twse = pd.DataFrame({"Ticker": ["2330"], "Market": ["TWSE"]})

    with pytest.raises(ValueError, match="OTC"):
        backfill_eps.combine_complete_markets({"TWSE": twse}, reference_rows=1)


def test_incomplete_replacement_is_rejected():
    twse = pd.DataFrame({"Ticker": [f"1{i:03d}" for i in range(30)]})
    otc = pd.DataFrame({"Ticker": [f"6{i:03d}" for i in range(30)]})

    with pytest.raises(ValueError, match="疑似殘缺"):
        backfill_eps.combine_complete_markets(
            {"TWSE": twse, "OTC": otc},
            reference_rows=100,
        )
