import pandas as pd

from ml.dataset import _list_daily_tickers as list_v2_tickers
from ml.dataset import diagnose_stock_eligibility
from ml.dataset_t1 import _list_daily_tickers as list_t1_tickers
from ml.universe import (
    ETF_TICKERS,
    classify_ticker,
    filter_out_etfs_df,
    filter_stock_universe,
    get_etf_metadata,
    is_etf_ticker,
    is_retired_ticker,
    load_retired_tickers,
)


def test_etf_universe_is_loaded_from_data_file():
    assert len(ETF_TICKERS) >= 150
    assert {"0050", "0056", "00878", "00981A", "00679B", "00635U", "00669R"}.issubset(
        ETF_TICKERS
    )
    assert get_etf_metadata("00981A")["market"] == "TWSE"
    assert get_etf_metadata("00679B")["market"] == "TPEx"


def test_classify_ticker_covers_taiwan_etf_suffix_classes():
    assert classify_ticker("2330") == "stock"
    assert classify_ticker("0050") == "etf_passive"
    assert classify_ticker("00981A") == "etf_active"
    assert classify_ticker("00679B") == "etf_bond"
    assert classify_ticker("00635U") == "etf_futures"
    assert classify_ticker("00631L") == "etf_leveraged"
    assert classify_ticker("00669R") == "etf_inverse"


def test_suffix_fallback_blocks_new_letter_suffix_etfs_before_refresh():
    assert is_etf_ticker("00999A")
    assert classify_ticker("00999A") == "etf_active"
    assert classify_ticker("00999B") == "etf_bond"


def test_filter_stock_universe_removes_etfs_without_dropping_stocks():
    assert filter_stock_universe(["2330", "0050", "00631L", "3426", "6806", "6588", "00981A"]) == [
        "2330",
        "6588",
    ]


def test_retired_tickers_are_excluded_from_stock_universe():
    retired = load_retired_tickers()

    assert {"3426", "3454", "4945", "4987", "6747", "6806"}.issubset(retired)
    assert is_retired_ticker("3426")
    assert is_retired_ticker("6806")


def test_filter_out_etfs_df_removes_prediction_rows():
    df = pd.DataFrame(
        {"ticker": ["2330", "0050", "006208", "00981A", "6588"], "score": [1, 2, 3, 4, 5]}
    )

    out = filter_out_etfs_df(df)

    assert out["ticker"].tolist() == ["2330", "6588"]


def test_v2_and_t1_daily_universes_exclude_etfs():
    v2_tickers = set(list_v2_tickers())
    t1_tickers = set(list_t1_tickers())

    assert not (v2_tickers & ETF_TICKERS)
    assert not (t1_tickers & ETF_TICKERS)


def test_etf_diagnosis_is_explicitly_unsupported_for_v2_alpha():
    info = diagnose_stock_eligibility("00981A")

    assert info["eligible"] is False
    assert info["reason_code"] == "etf_v2_alpha_model_unsupported"


def test_retired_ticker_diagnosis_is_explicit():
    info = diagnose_stock_eligibility("3426")

    assert info["eligible"] is False
    assert info["reason_code"] == "retired_or_delisted"
