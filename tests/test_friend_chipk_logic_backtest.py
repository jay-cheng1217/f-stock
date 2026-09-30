from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.friend_chipk_logic_backtest import (
    BacktestConfig,
    _extreme_trades,
    _select_signals,
    _trade_summary,
    annotate_friend_rules,
    prepare_stock_frame,
)


def test_friend_base_rule_detects_foreign_turn_buy_near_support(tmp_path: Path) -> None:
    path = tmp_path / "3013.csv"
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2026-06-10", periods=8, freq="B"),
            "Open": [100, 99, 98, 97, 96, 98, 101, 103],
            "High": [101, 100, 99, 98, 99, 102, 104, 105],
            "Low": [99, 98, 97, 96, 95, 97, 100, 102],
            "Close": [100, 99, 98, 97, 98, 100, 103, 104],
            "Volume": [300_000] * 8,
            "Foreign_BuySell": [-100, -120, -90, -80, -70, 150, 50, 30],
            "Trust_BuySell": [0] * 8,
            "Dealer_BuySell": [-10, -10, -10, -10, -10, 20, 10, 5],
            "MA_5": [100, 99, 98, 97, 97, 99, 100, 101],
            "MA_20": [105] * 8,
            "MA_60": [98] * 8,
            "MACDh_12_26_9": [-2.0, -1.8, -1.7, -1.6, -1.5, -1.1, -0.8, -0.4],
            "VOL_MA_20": [300_000] * 8,
        }
    )
    frame.to_csv(path, index=False)

    prepared = annotate_friend_rules(prepare_stock_frame(path))
    signal = prepared.loc[prepared["Date"].eq(pd.Timestamp("2026-06-17"))].iloc[0]

    assert bool(signal["friend_base"]) is True
    assert bool(signal["friend_strict"]) is True
    assert signal["entry_open"] == 101


def test_signal_selection_caps_per_day_and_trade_summary() -> None:
    data = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2026-06-17")] * 3,
            "ticker": ["1111", "2222", "3333"],
            "friend_base": [True, True, True],
            "friend_logic_score": [0.1, 0.3, 0.2],
            "entry_open": [10.0, 10.0, 10.0],
            "gross_return_5d": [0.01, 0.03, -0.02],
        }
    )

    selected = _select_signals(data, "friend_base", max_per_day=2)
    summary = _trade_summary(selected, hold=5, friction=0.004)

    assert selected["ticker"].tolist() == ["2222", "3333"]
    assert summary["trades"] == 2
    assert summary["win_rate"] == 0.5


def test_pullback_limit_rule_uses_support_fill_price(tmp_path: Path) -> None:
    path = tmp_path / "3013.csv"
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2026-06-08", periods=8, freq="B"),
            "Open": [111, 114, 112, 111, 110, 112, 115, 116],
            "High": [113, 116, 114, 113, 112, 116, 117, 118],
            "Low": [110, 113, 110, 110, 109, 111, 108.5, 115],
            "Close": [112, 114.5, 108.5, 111, 112, 115, 116, 117],
            "Volume": [300_000] * 8,
            "Foreign_BuySell": [-100, -120, -90, -80, -70, 150, 50, 30],
            "Trust_BuySell": [0] * 8,
            "Dealer_BuySell": [-10, -10, -10, -10, -10, 20, 10, 5],
            "MA_5": [111, 112, 111, 111, 111, 112, 113, 114],
            "MA_20": [118] * 8,
            "MA_60": [108] * 8,
            "MACDh_12_26_9": [-1.0, -0.8, -1.1, -0.9, -0.7, -0.5, -0.3, -0.1],
            "VOL_MA_20": [300_000] * 8,
        }
    )
    frame.to_csv(path, index=False)

    prepared = annotate_friend_rules(prepare_stock_frame(path))
    signal = prepared.loc[prepared["Date"].eq(pd.Timestamp("2026-06-15"))].iloc[0]
    selected = _select_signals(prepared, "friend_pullback_limit", max_per_day=10)

    assert bool(signal["friend_pullback_setup"]) is True
    assert bool(signal["friend_pullback_limit"]) is True
    trade = selected[selected["Date"].eq(pd.Timestamp("2026-06-15"))].iloc[0]
    assert trade["entry_open"] == trade["pullback_limit_price"]
    assert round(float(trade["entry_open"]), 2) == 108.54


def test_extreme_trades_orders_60d_top_and_bottom() -> None:
    trades = pd.DataFrame(
        {
            "ticker": ["1111", "2222", "3333"],
            "Date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "entry_date": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]),
            "entry_open": [10.0, 10.0, 10.0],
            "Close": [9.8, 10.2, 10.1],
            "exit_date_60d": pd.to_datetime(["2026-03-01", "2026-03-02", "2026-03-03"]),
            "exit_close_60d": [12.0, 8.0, 11.0],
            "gross_return_60d": [0.20, -0.20, 0.10],
            "Foreign_BuySell": [100.0, -100.0, 50.0],
            "Dealer_BuySell": [0.0, 0.0, 0.0],
            "inst_total": [100.0, -100.0, 50.0],
            "MACDh_12_26_9": [0.1, -0.1, 0.0],
            "friend_logic_score": [0.5, -0.5, 0.2],
        }
    )

    result = _extreme_trades(trades, hold=60, friction=0.004, top_n=2)

    assert [row["ticker"] for row in result["top"]] == ["1111", "3333"]
    assert [row["ticker"] for row in result["bottom"]] == ["2222", "3333"]
