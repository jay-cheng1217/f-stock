import numpy as np
import pandas as pd
import pytest

from ml import dataset as dataset_module
from ml.target import compute_target
from scripts.update_taiex_total_return_index import _parse_roc_date


def _prices(periods: int = 26, close: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": np.full(periods, close),
            "Close": np.full(periods, close),
        }
    )


def _benchmark(dates: pd.Series, *, total_growth: float = 0.0) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "Date": dates,
            "twii_close": np.full(count, 100.0),
            "twii_total_return_close": np.linspace(100.0, 100.0 * (1 + total_growth), count),
        }
    )


def _event(ticker: str, event_date: pd.Timestamp, before: float, after: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_id": [ticker],
            "date": [event_date],
            "before_price": [before],
            "after_price": [after],
        }
    )


def test_no_event_labels_are_bitwise_unchanged_when_benchmark_basis_matches():
    prices = _prices()
    benchmark = _benchmark(prices["Date"])

    legacy = compute_target(prices, benchmark)
    adjusted = compute_target(
        prices,
        benchmark,
        ticker="2330",
        exdiv_df=pd.DataFrame(
            columns=["stock_id", "date", "before_price", "after_price"]
        ),
    )

    columns = [
        "forward_return",
        "forward_return_20d",
        "trade_return_20d",
        "excess_return",
        "excess_return_20d",
        "trade_excess_return_20d",
        "target",
    ]
    pd.testing.assert_frame_equal(legacy[columns], adjusted[columns], check_exact=True)


def test_event_on_t_plus_one_is_in_close_window_but_not_trade_window():
    prices = _prices()
    event = _event("1234", prices.loc[1, "Date"], 100.0, 90.0)
    result = compute_target(prices, ticker="1234", exdiv_df=event)

    assert result.loc[0, "forward_return_20d"] == pytest_approx(1 / 0.9 - 1)
    assert result.loc[0, "trade_return_20d"] == pytest_approx(0.0)


def test_event_on_t_plus_twenty_is_in_both_windows():
    prices = _prices()
    event = _event("1234", prices.loc[20, "Date"], 100.0, 90.0)
    result = compute_target(prices, ticker="1234", exdiv_df=event)

    expected = 1 / 0.9 - 1
    assert result.loc[0, "forward_return_20d"] == pytest_approx(expected)
    assert result.loc[0, "trade_return_20d"] == pytest_approx(expected)


def test_event_on_t_is_excluded_from_both_windows():
    prices = _prices()
    event = _event("1234", prices.loc[0, "Date"], 100.0, 90.0)
    result = compute_target(prices, ticker="1234", exdiv_df=event)

    assert result.loc[0, "forward_return_20d"] == pytest_approx(0.0)
    assert result.loc[0, "trade_return_20d"] == pytest_approx(0.0)


def test_large_stock_dividend_uses_ratio_not_cash_addition():
    prices = _prices(close=1465.0)
    prices.loc[20:, ["Open", "Close"]] = 800.0
    event = _event("3293", prices.loc[20, "Date"], 1465.0, 715.0)
    result = compute_target(prices, ticker="3293", exdiv_df=event)

    ratio_return = 800.0 / (715.0 / 1465.0) / 1465.0 - 1
    additive_return = (800.0 + 750.0) / 1465.0 - 1
    assert result.loc[0, "forward_return_20d"] == pytest_approx(ratio_return)
    assert abs(ratio_return - additive_return) > 0.05


def test_excess_return_uses_total_return_benchmark_when_available():
    prices = _prices()
    benchmark = _benchmark(prices["Date"], total_growth=0.05)
    event = _event("1234", prices.loc[1, "Date"], 100.0, 90.0)
    result = compute_target(prices, benchmark, ticker="1234", exdiv_df=event)

    benchmark_5d = benchmark.loc[5, "twii_total_return_close"] / 100.0 - 1
    expected = (1 / 0.9 - 1) - benchmark_5d
    assert result.loc[0, "excess_return"] == pytest_approx(expected)


def test_missing_total_return_index_keeps_excess_on_symmetric_raw_basis():
    prices = _prices()
    benchmark = _benchmark(prices["Date"]).drop(columns="twii_total_return_close")
    event = _event("1234", prices.loc[1, "Date"], 100.0, 90.0)
    result = compute_target(prices, benchmark, ticker="1234", exdiv_df=event)

    assert result.loc[0, "forward_return"] == pytest_approx(1 / 0.9 - 1)
    assert result.loc[0, "excess_return"] == pytest_approx(0.0)


def test_forward_targets_do_not_bridge_long_trading_suspension():
    prices = _prices(periods=45)
    prices.loc[10:, "Date"] = prices.loc[10:, "Date"] + pd.Timedelta(days=100)
    result = compute_target(prices)

    assert result.loc[0, "forward_return"] == pytest_approx(0.0)
    assert pd.isna(result.loc[5, "forward_return"])
    assert pd.isna(result.loc[0, "forward_return_20d"])
    assert pd.isna(result.loc[9, "trade_return_20d"])
    assert result.loc[10, "forward_return_20d"] == pytest_approx(0.0)


def test_roc_date_parser_matches_twse_mfi94u_contract():
    assert _parse_roc_date("113/07/24") == pd.Timestamp("2024-07-24")


def test_training_twii_loader_fails_closed_without_total_return_index(
    monkeypatch, tmp_path
):
    pd.DataFrame(
        {"Date": ["2026-01-02", "2026-01-05"], "Close": [100.0, 101.0]}
    ).to_csv(tmp_path / "index_TWII.csv", index=False)
    monkeypatch.setattr(dataset_module, "INDEX_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="total-return index is required"):
        dataset_module._load_twii(require_total_return=True)


def test_training_twii_loader_fails_closed_on_missing_total_return_date(
    monkeypatch, tmp_path
):
    pd.DataFrame(
        {"Date": ["2026-01-02", "2026-01-05"], "Close": [100.0, 101.0]}
    ).to_csv(tmp_path / "index_TWII.csv", index=False)
    pd.DataFrame(
        {"Date": ["2026-01-02"], "Close": [200.0]}
    ).to_csv(tmp_path / "index_TWII_total_return.csv", index=False)
    monkeypatch.setattr(dataset_module, "INDEX_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="1 missing trading dates"):
        dataset_module._load_twii(require_total_return=True)


def pytest_approx(value: float):
    return pytest.approx(value, abs=1e-9)
