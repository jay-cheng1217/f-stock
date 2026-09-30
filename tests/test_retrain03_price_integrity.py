import numpy as np
import pandas as pd
import pytest

from ml.corporate_actions import (
    action_adjusted_ohlc,
    backward_adjustment_multiplier,
    load_action_calendar,
    load_price_continuity_calendar,
    normalize_action_calendar,
)
from ml.features.technical import (
    CLASSIC_INDICATOR_COLUMNS,
    compute_classic_technical_indicators,
    compute_technical_features,
)
from scripts.official_daily_prices import (
    OfficialDailyPriceError,
    normalize_official_ticker,
    parse_tpex_history,
    parse_twse_history,
    validate_official_daily_coverage,
)
from scripts.repair_official_daily_ohlc import reconcile_ticker_frame
from scripts.update_corporate_action_factors import (
    OUTPUT_COLUMNS,
    fetch_range,
    parse_tpex_reduction,
    parse_twse_etf_split,
    parse_twse_exdiv,
)
import scripts.update_corporate_action_factors as action_factor_updater
import scripts.official_daily_prices as official_prices


def _calendar(ticker: str, date: pd.Timestamp, factor: float) -> pd.DataFrame:
    return pd.DataFrame({"stock_id": [ticker], "date": [date], "factor": [factor]})


def test_official_history_parsers_return_complete_ohlcv():
    twse = {
        "stat": "OK",
        "tables": [{"data": [[
            "0050", "Taiwan 50", "252,639,825", "1", "12,002,805,591",
            "47.50", "47.72", "47.14", "47.57",
        ]]}],
    }
    tpex = {
        "stat": "ok",
        "tables": [{"data": [[
            "8093", "Enermax", "14.90", "+0.25", "14.65", "15.20", "14.65",
            "25,000", "0", "10",
        ]]}],
    }

    twse_result = parse_twse_history(twse, "2025-06-18").iloc[0]
    tpex_result = parse_tpex_history(tpex, "2025-10-16").iloc[0]
    assert twse_result[["Open", "High", "Low", "Close", "Volume"]].tolist() == [
        47.5, 47.72, 47.14, 47.57, 252_639_825.0,
    ]
    assert tpex_result[["Open", "High", "Low", "Close", "Volume"]].tolist() == [
        14.65, 15.2, 14.65, 14.9, 25_000.0,
    ]


def test_official_symbol_validation_includes_etfs_and_leveraged_suffixes():
    assert normalize_official_ticker("2330") == "2330"
    assert normalize_official_ticker("006208") == "006208"
    assert normalize_official_ticker("00631L") == "00631L"
    assert normalize_official_ticker("00400A") == "00400A"
    assert normalize_official_ticker("not-a-symbol") is None


def test_official_daily_coverage_rejects_partial_market_payloads():
    partial = pd.DataFrame(
        {
            "Ticker": ["2330", "8093"],
            "Date": pd.to_datetime(["2026-07-16", "2026-07-16"]),
            "Market": ["TWSE", "TPEX"],
        }
    )
    with pytest.raises(OfficialDailyPriceError, match="coverage incomplete"):
        validate_official_daily_coverage(partial, "2026-07-16")


def test_partial_official_payload_is_not_persisted_to_cache(monkeypatch, tmp_path):
    twse = {
        "stat": "OK",
        "tables": [{"data": [[
            "2330", "TSMC", "1,000", "1", "1,000,000",
            "1000", "1010", "995", "1005",
        ]]}],
    }
    tpex = {
        "stat": "ok",
        "tables": [{"data": [[
            "8093", "Enermax", "14.90", "+0.25", "14.65", "15.20", "14.65",
            "25,000", "0", "10",
        ]]}],
    }

    def fake_download(url, **_kwargs):
        return twse if "MI_INDEX" in url else tpex

    monkeypatch.setattr(official_prices, "_download_json", fake_download)
    monkeypatch.setattr(
        official_prices,
        "MIN_OFFICIAL_MARKET_ROWS",
        {"TWSE": 2, "TPEX": 2},
    )

    with pytest.raises(OfficialDailyPriceError, match="coverage incomplete"):
        official_prices.fetch_official_daily_prices(
            "2026-07-16",
            cache_dir=tmp_path,
        )

    assert not list(tmp_path.rglob("*.json.gz"))


def test_direct_official_factor_calendar_is_accepted():
    result = normalize_action_calendar(
        pd.DataFrame(
            {"stock_id": ["0050"], "date": ["2025-06-18"], "factor": [0.25]}
        )
    )
    assert result.to_dict("records") == [
        {"stock_id": "0050", "date": pd.Timestamp("2025-06-18"), "factor": 0.25}
    ]


def test_twse_exdiv_parser_preserves_separate_label_and_price_basis():
    payload = {
        "data": [[
            "113年07月02日", "2429", "銘旺科", "38.90", "29.15",
            "9.743493", "權", "42.75", "26.25", "38.90", "38.90",
        ]]
    }
    result = parse_twse_exdiv(payload)[0]
    assert result["label_factor"] == 29.15 / 38.90
    assert result["price_factor"] == 1.0


def test_special_action_parsers_use_resume_and_opening_basis():
    reduction_payload = {
        "tables": [{"data": [[
            "1150112", "8093", "保銳", "13.00", "21.67", "23.80",
            "19.55", "21.65", "0.00", "彌補虧損", "details",
        ]]}]
    }
    split_payload = {
        "data": [[
            "114/06/18", "0050", "元大台灣50", "分割", "188.65",
            "47.16", "56.59", "37.73", "47.16",
        ]]
    }
    reduction = parse_tpex_reduction(reduction_payload)[0]
    split = parse_twse_etf_split(split_payload)[0]
    assert reduction["label_factor"] == 21.67 / 13.0
    assert reduction["price_factor"] == 21.65 / 13.0
    assert split["label_factor"] == 47.16 / 188.65
    assert split["price_factor"] == 47.16 / 188.65


def test_empty_official_action_window_is_valid(monkeypatch):
    monkeypatch.setattr(
        action_factor_updater,
        "fetch_period",
        lambda start, end: ([], {"twse_exdiv": 0, "tpex_exdiv": 0}),
    )
    frame, counts = fetch_range(
        pd.Timestamp("2026-01-01").date(),
        pd.Timestamp("2026-01-31").date(),
    )
    assert frame.empty
    assert frame.columns.tolist() == OUTPUT_COLUMNS
    assert counts == {"twse_exdiv": 0, "tpex_exdiv": 0}


def test_label_and_price_calendars_use_different_official_columns(tmp_path):
    legacy = tmp_path / "legacy.csv"
    official = tmp_path / "official.csv"
    pd.DataFrame(
        {
            "stock_id": ["2429"],
            "date": ["2024-07-02"],
            "before_price": [38.9],
            "after_price": [29.15],
        }
    ).to_csv(legacy, index=False)
    pd.DataFrame(
        {
            "stock_id": ["2429"],
            "date": ["2024-07-02"],
            "label_factor": [29.15 / 38.9],
            "price_factor": [1.0],
        }
    ).to_csv(official, index=False)

    label = load_action_calendar(str(legacy), str(official))
    price = load_price_continuity_calendar(str(official))
    assert label.iloc[0]["factor"] == 29.15 / 38.9
    assert price.iloc[0]["factor"] == 1.0
    assert len(label) == 1


def test_backward_adjustment_excludes_event_day_and_smooths_split():
    dates = pd.Series(pd.date_range("2025-06-16", periods=4, freq="B"))
    calendar = _calendar("0050", dates.iloc[2], 0.25)
    multiplier = backward_adjustment_multiplier(dates, "0050", calendar)
    assert multiplier.tolist() == [0.25, 0.25, 1.0, 1.0]

    raw = pd.DataFrame(
        {
            "Date": dates,
            "Open": [188.0, 190.0, 47.5, 48.0],
            "High": [190.0, 192.0, 48.0, 48.5],
            "Low": [187.0, 189.0, 47.0, 47.5],
            "Close": [190.0, 190.0, 47.5, 48.0],
        }
    )
    adjusted, _ = action_adjusted_ohlc(raw, "0050", calendar)
    assert adjusted["Close"].tolist() == [47.5, 47.5, 47.5, 48.0]


def test_no_action_technical_features_are_exactly_unchanged():
    count = 90
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2025-01-02", periods=count, freq="B"),
            "Open": np.linspace(50, 70, count),
            "High": np.linspace(51, 71, count),
            "Low": np.linspace(49, 69, count),
            "Close": np.linspace(50.5, 70.5, count),
            "Volume": np.arange(count) * 1000 + 100_000,
        }
    )
    legacy = compute_technical_features(frame)
    candidate = compute_technical_features(
        frame,
        ticker="1234",
        action_calendar=pd.DataFrame(columns=["stock_id", "date", "factor"]),
    )
    pd.testing.assert_frame_equal(legacy, candidate, check_exact=True)


def test_classic_indicator_helper_matches_full_feature_catalog():
    count = 90
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2025-01-02", periods=count, freq="B"),
            "Open": np.linspace(50, 70, count),
            "High": np.linspace(51, 71, count),
            "Low": np.linspace(49, 69, count),
            "Close": np.linspace(50.5, 70.5, count),
            "Volume": np.arange(count) * 1000 + 100_000,
        }
    )
    full = compute_technical_features(frame, ticker="1234")
    classic = compute_classic_technical_indicators(frame, ticker="1234")
    pd.testing.assert_frame_equal(
        full[CLASSIC_INDICATOR_COLUMNS],
        classic[CLASSIC_INDICATOR_COLUMNS],
        check_exact=True,
    )


def test_split_keeps_normalized_ma_rsi_and_atr_continuous():
    count = 100
    event_index = 60
    dates = pd.date_range("2025-03-03", periods=count, freq="B")
    adjusted_close = 45 + np.arange(count) * 0.08 + np.sin(np.arange(count) / 3) * 0.5
    raw_close = adjusted_close.copy()
    raw_close[:event_index] /= 0.25
    raw = pd.DataFrame(
        {
            "Date": dates,
            "Open": raw_close * 0.998,
            "High": raw_close * 1.01,
            "Low": raw_close * 0.99,
            "Close": raw_close,
            "Volume": np.full(count, 1_000_000),
        }
    )
    calendar = _calendar("0050", dates[event_index], 0.25)
    result = compute_technical_features(raw, ticker="0050", action_calendar=calendar)

    window = result.iloc[event_index - 1:event_index + 1]
    assert window["return_1d"].abs().max() < 0.03
    assert window["price_vs_ma20"].diff().abs().iloc[-1] < 0.02
    assert window["rsi_14"].diff().abs().iloc[-1] < 15
    assert window["atr_pct"].diff().abs().iloc[-1] < 0.01


def test_scheduled_action_on_market_closure_adjusts_next_trading_day():
    dates = pd.to_datetime(["2024-07-22", "2024-07-23", "2024-07-26", "2024-07-29"])
    raw = pd.DataFrame(
        {
            "Date": dates,
            "Open": [1485.0, 1445.0, 751.0, 864.0],
            "High": [1490.0, 1480.0, 786.0, 864.0],
            "Low": [1410.0, 1435.0, 731.0, 811.0],
            "Close": [1410.0, 1465.0, 786.0, 830.0],
            "Volume": [1_474_000, 1_860_000, 4_842_000, 9_168_000],
        }
    )
    calendar = _calendar("3293", pd.Timestamp("2024-07-24"), 715.0 / 1465.0)
    result = compute_technical_features(raw, ticker="3293", action_calendar=calendar)
    expected = 786.0 / 715.0 - 1.0
    assert result.loc[2, "return_1d"] == pytest.approx(expected, abs=1e-7)


def test_long_suspension_resets_rolling_and_lag_features():
    first_dates = pd.date_range("2024-01-02", periods=40, freq="B")
    second_dates = pd.date_range("2024-06-03", periods=40, freq="B")
    dates = first_dates.append(second_dates)
    close = np.r_[np.linspace(10, 12, 40), np.linspace(20, 22, 40)]
    frame = pd.DataFrame(
        {
            "Date": dates,
            "Open": close,
            "High": close + 0.2,
            "Low": close - 0.2,
            "Close": close,
            "Volume": np.full(80, 100_000),
        }
    )

    full = compute_technical_features(frame, ticker="3073")
    classic = compute_classic_technical_indicators(frame, ticker="3073")
    assert pd.isna(full.loc[40, "return_1d"])
    assert pd.isna(full.loc[40, "MA_20"])
    assert pd.isna(classic.loc[40, "MA_20"])
    assert full.loc[59, "MA_20"] == pytest.approx(frame.loc[40:59, "Close"].mean())


def test_reconcile_overwrites_all_price_fields_and_preserves_other_cells():
    local = pd.DataFrame(
        {
            "Date": ["2025-10-15", "2025-10-16", "2025-10-17"],
            "Open": [14.6, 24.4, 24.8],
            "High": [14.8, 25.3, 25.0],
            "Low": [14.5, 24.4, 24.5],
            "Close": [14.65, 24.83, 24.7],
            "Volume": [10_000, 15_000, 20_000],
            "Foreign_BuySell": [1.0, 2.0, 3.0],
        }
    )
    official = pd.DataFrame(
        {
            "Date": ["2025-10-15", "2025-10-16"],
            "Open": [14.6, 14.65],
            "High": [14.8, 15.2],
            "Low": [14.5, 14.65],
            "Close": [14.65, 14.9],
            "Volume": [10_000, 25_000],
        }
    )
    repaired, stats = reconcile_ticker_frame(
        local,
        official,
        ticker="8093",
        start=pd.Timestamp("2025-10-15"),
        end=pd.Timestamp("2025-10-17"),
    )
    assert repaired["Date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-10-15", "2025-10-16"
    ]
    assert repaired.loc[1, ["Open", "High", "Low", "Close", "Volume"]].tolist() == [
        14.65, 15.2, 14.65, 14.9, 25_000.0,
    ]
    assert repaired["Foreign_BuySell"].tolist() == [1.0, 2.0]
    assert stats["changed_rows"] == 1
    assert stats["unchanged_rows"] == 1
    assert stats["removed_rows"] == 1
    assert stats["added_rows"] == 0


def test_reconcile_adds_missing_official_rows_without_mutating_existing_metadata():
    local = pd.DataFrame(
        {
            "Date": ["2025-07-31", "2025-08-04"],
            "Open": [100.0, 102.0],
            "High": [101.0, 103.0],
            "Low": [99.0, 101.0],
            "Close": [100.5, 102.5],
            "Volume": [10_000, 12_000],
            "Foreign_BuySell": [7.0, 9.0],
        }
    )
    official = pd.DataFrame(
        {
            "Date": ["2025-07-31", "2025-08-01", "2025-08-04"],
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [10_000, 11_000, 12_000],
        }
    )

    repaired, stats = reconcile_ticker_frame(
        local,
        official,
        ticker="2330",
        start=pd.Timestamp("2025-07-31"),
        end=pd.Timestamp("2025-08-04"),
    )

    assert repaired["Date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-07-31", "2025-08-01", "2025-08-04",
    ]
    assert repaired.loc[1, ["Open", "High", "Low", "Close", "Volume"]].tolist() == [
        101.0, 102.0, 100.0, 101.5, 11_000.0,
    ]
    assert repaired["Foreign_BuySell"].iloc[[0, 2]].tolist() == [7.0, 9.0]
    assert pd.isna(repaired.loc[1, "Foreign_BuySell"])
    assert stats["added_rows"] == 1
    assert stats["added_dates_sample"] == ["2025-08-01"]
