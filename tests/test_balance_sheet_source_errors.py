import pandas as pd
import pytest

from ml.features.balance_sheet import (
    BalanceSheetSourceError,
    _BS_CACHE,
    compute_balance_sheet_features,
)


def _daily_frame():
    return pd.DataFrame({"Date": ["2026-05-18"], "Close": [100.0]})


def _write_valid_bs(path):
    path.write_text(
        "Ticker,Total_Assets,Total_Liabilities,Total_Equity\n"
        "2330,1000,400,600\n",
        encoding="utf-8",
    )


def test_corrupt_balance_sheet_file_fails_loudly(tmp_path):
    _BS_CACHE.clear()
    bs_dir = tmp_path / "bs"
    bs_dir.mkdir()
    (bs_dir / "bs_2026Q1.csv").write_text(
        'Ticker,Total_Assets,Total_Liabilities,Total_Equity\n2330,"broken\n',
        encoding="utf-8",
    )

    with pytest.raises(BalanceSheetSourceError, match="balance_sheet source unreadable"):
        compute_balance_sheet_features(_daily_frame(), "2330", str(bs_dir))


def test_balance_sheet_schema_drift_fails_loudly(tmp_path):
    _BS_CACHE.clear()
    bs_dir = tmp_path / "bs"
    bs_dir.mkdir()
    (bs_dir / "bs_2026Q1.csv").write_text(
        "Ticker,Total_Assets\n2330,1000\n",
        encoding="utf-8",
    )

    with pytest.raises(BalanceSheetSourceError, match="missing"):
        compute_balance_sheet_features(_daily_frame(), "2330", str(bs_dir))


def test_corrupt_financial_file_is_not_silently_skipped(tmp_path):
    _BS_CACHE.clear()
    bs_dir = tmp_path / "bs"
    financial_dir = tmp_path / "financial"
    bs_dir.mkdir()
    financial_dir.mkdir()
    _write_valid_bs(bs_dir / "bs_2026Q1.csv")
    (financial_dir / "financial_2026Q1.csv").write_text(
        'Ticker,Revenue_M,Net_Margin_Pct\n2330,"broken\n',
        encoding="utf-8",
    )

    with pytest.raises(BalanceSheetSourceError, match="financial source unreadable"):
        compute_balance_sheet_features(
            _daily_frame(),
            "2330",
            str(bs_dir),
            str(financial_dir),
        )
