from datetime import date

import pandas as pd
import pytest

import twstock
from scripts import backfill_balance_sheet


def _write_rows(path, count):
    pd.DataFrame({"Ticker": [f"{index:04d}" for index in range(count)]}).to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )


@pytest.mark.parametrize(
    ("asof", "expected"),
    [
        (date(2026, 4, 2), (2025, 3)),
        (date(2026, 4, 3), (2025, 4)),
        (date(2026, 5, 17), (2025, 4)),
        (date(2026, 5, 18), (2026, 1)),
    ],
)
def test_financial_quarter_waits_for_filing_deadline(asof, expected):
    assert twstock._latest_available_financial_quarter(asof) == expected


def test_financial_pending_selection_preserves_partial_file(tmp_path):
    quarters = [(2025, 4), (2026, 1)]
    complete = tmp_path / "financial_2025Q4.csv"
    partial = tmp_path / "financial_2026Q1.csv"
    _write_rows(complete, 100)
    _write_rows(partial, 50)

    pending = twstock._select_pending_financial_quarters(quarters, str(tmp_path))

    assert pending == [(2026, 1)]
    assert partial.exists()
    assert twstock._financial_row_count(partial) == 50


def test_financial_partial_market_response_is_rejected():
    twse = pd.DataFrame({"Ticker": ["2330"]})

    with pytest.raises(ValueError, match="OTC"):
        twstock._combine_complete_financial_markets({"TWSE": twse}, reference_rows=1)


def test_financial_incomplete_replacement_is_rejected():
    twse = pd.DataFrame({"Ticker": [f"1{i:03d}" for i in range(30)]})
    otc = pd.DataFrame({"Ticker": [f"6{i:03d}" for i in range(30)]})

    with pytest.raises(ValueError, match="incomplete financial replacement"):
        twstock._combine_complete_financial_markets(
            {"TWSE": twse, "OTC": otc},
            reference_rows=100,
        )


def test_balance_sheet_quarter_waits_for_filing_deadline_and_grace():
    assert backfill_balance_sheet.get_latest_available_quarter(date(2026, 4, 2)) == (2025, 3)
    assert backfill_balance_sheet.get_latest_available_quarter(date(2026, 4, 3)) == (2025, 4)
    assert backfill_balance_sheet.get_latest_available_quarter(date(2026, 5, 17)) == (2025, 4)
    assert backfill_balance_sheet.get_latest_available_quarter(date(2026, 5, 18)) == (2026, 1)


def test_balance_sheet_pending_selection_preserves_partial_file(tmp_path):
    quarters = [(2025, 4), (2026, 1)]
    complete = tmp_path / "bs_2025Q4.csv"
    partial = tmp_path / "bs_2026Q1.csv"
    _write_rows(complete, 100)
    _write_rows(partial, 50)

    pending = backfill_balance_sheet.select_pending_quarters(quarters, str(tmp_path))

    assert pending == [(2026, 1)]
    assert partial.exists()
    assert backfill_balance_sheet._row_count(partial) == 50


def test_balance_sheet_partial_market_response_is_rejected():
    twse = pd.DataFrame({"Ticker": ["2330"]})

    with pytest.raises(ValueError, match="OTC"):
        backfill_balance_sheet.combine_complete_markets({"TWSE": twse}, reference_rows=1)


def test_balance_sheet_incomplete_replacement_is_rejected():
    twse = pd.DataFrame({"Ticker": [f"1{i:03d}" for i in range(30)]})
    otc = pd.DataFrame({"Ticker": [f"6{i:03d}" for i in range(30)]})

    with pytest.raises(ValueError, match="incomplete balance-sheet replacement"):
        backfill_balance_sheet.combine_complete_markets(
            {"TWSE": twse, "OTC": otc},
            reference_rows=100,
        )

