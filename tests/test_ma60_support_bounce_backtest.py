from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.ma60_support_bounce_backtest import (
    annotate_ma60_support_bounce,
    prepare_stock_frame,
    select_signals,
    trade_summary,
)


def _write_sample(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2026-06-08", periods=8, freq="B"),
            "Open": [116.0, 112.5, 107.5, 110.0, 110.0, 113.0, 110.5, 114.0],
            "High": [118.0, 115.0, 108.5, 110.5, 114.5, 113.5, 113.0, 114.5],
            "Low": [114.0, 108.5, 102.0, 107.0, 107.5, 110.5, 109.5, 113.0],
            "Close": [114.5, 108.5, 106.0, 107.5, 112.0, 110.5, 113.0, 113.5],
            "Volume": [6_000_000, 4_200_000, 3_300_000, 1_500_000, 3_500_000, 1_900_000, 1_600_000, 2_000_000],
            "Foreign_BuySell": [-100_000, -260_000, 220_000, -130_000, -250_000, -210_000, -180_000, 150_000],
            "Trust_BuySell": [0] * 8,
            "Dealer_BuySell": [10_000, -170_000, -45_000, 2_000, 35_000, -32_000, -3_000, 4_000],
            "MA_5": [124.0, 119.0, 114.3, 111.4, 109.7, 108.9, 109.8, 112.4],
            "MA_20": [115.36, 115.43, 115.46, 115.68, 116.23, 116.77, 117.50, 118.27],
            "MA_60": [108.16, 108.29, 108.43, 108.57, 108.78, 108.99, 109.21, 109.73],
            "RSI_14": [48.45, 43.34, 41.38, 43.04, 47.83, 46.43, 49.10, 49.69],
            "MACDh_12_26_9": [-0.14, -1.14, -1.90, -2.21, -2.03, -1.93, -1.63, -1.11],
            "K": [47.7, 19.2, 9.7, 9.3, 19.1, 24.6, 33.1, 57.9],
            "D": [48.5, 32.3, 19.6, 12.7, 12.7, 17.7, 25.6, 44.5],
            "VOL_MA_20": [7_100_000, 7_250_000, 7_310_000, 7_270_000, 7_330_000, 7_350_000, 7_330_000, 7_150_000],
        }
    )
    frame.to_csv(path, index=False)


def test_ma60_support_bounce_catches_3013_style_entry(tmp_path: Path) -> None:
    path = tmp_path / "3013.csv"
    _write_sample(path)

    prepared = annotate_ma60_support_bounce(prepare_stock_frame(path))
    signal = prepared.loc[prepared["Date"].eq(pd.Timestamp("2026-06-11"))].iloc[0]
    selected = select_signals(prepared, max_per_day=10)
    trade = selected[selected["Date"].eq(pd.Timestamp("2026-06-11"))].iloc[0]

    assert bool(signal["ma60_bounce_setup"]) is True
    assert bool(signal["ma60_bounce_filled"]) is True
    assert trade["entry_date"] == pd.Timestamp("2026-06-12")
    assert round(float(trade["ma60_bounce_entry_price"]), 2) == 108.57
    assert float(trade["gross_return_1d"]) > 0


def test_ma60_support_bounce_uses_signal_day_ma60_for_next_session(tmp_path: Path) -> None:
    path = tmp_path / "3013.csv"
    _write_sample(path)

    prepared = annotate_ma60_support_bounce(prepare_stock_frame(path))
    signal = prepared.loc[prepared["Date"].eq(pd.Timestamp("2026-06-12"))].iloc[0]
    selected = select_signals(prepared, max_per_day=10)

    assert bool(signal["ma60_bounce_setup"]) is True
    assert bool(signal["ma60_bounce_filled"]) is False
    assert selected[selected["Date"].eq(pd.Timestamp("2026-06-12"))].empty


def test_trade_summary_reports_target_and_stop_rates(tmp_path: Path) -> None:
    path = tmp_path / "3013.csv"
    _write_sample(path)

    selected = select_signals(annotate_ma60_support_bounce(prepare_stock_frame(path)), max_per_day=10)
    summary = trade_summary(selected, hold=1, friction=0.004)

    assert summary["trades"] >= 2
    assert summary["tp2_hit_rate"] is not None
    assert summary["stop2_hit_rate"] is not None
