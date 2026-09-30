from __future__ import annotations

import pandas as pd
import pytest

from scripts import backtest_rere_lane_baseline as bt


def _frame(periods=140):
    return pd.DataFrame({
        "Date": pd.bdate_range("2026-01-01", periods=periods).strftime("%Y-%m-%d"),
        "Close": 100., "Volume": 1_000_000., "MA_20": 100., "MA_60": 85.,
        "VOL_MA_20": 1_000_000., "Foreign_BuySell": -1000., "Trust_BuySell": 0.,
    })


def test_canonical_wash_uses_high_over_close_and_mean_volume():
    frame = _frame()
    frame.loc[60, "Close"] = 90.5
    frame.loc[60, "MA_20"] = 91.
    frame.loc[60, "Foreign_BuySell"] = 1000.
    frame.loc[60, "Volume"] = 400_000.
    masks = bt.signal_masks(frame)
    assert not masks[("legacy", "shakeout")].iloc[60]
    assert masks[("aligned", "shakeout")].iloc[60]
    # Wash-only repair cannot accidentally bypass the old daily volume condition.
    assert not masks[("wash_only", "shakeout")].iloc[60]


def test_ignition_requires_trust_and_preserves_subtype():
    frame = _frame()
    frame.loc[59, "Close"] = 88.
    frame.loc[60, "Close"] = 90.5
    frame.loc[60, "MA_20"] = 90.
    frame.loc[60, "Volume"] = 2_000_000.
    frame.loc[60, "Foreign_BuySell"] = 1000.
    assert not bt.signal_masks(frame)[("aligned", "ignition")].iloc[60]
    frame.loc[60, "Trust_BuySell"] = 1000.
    masks = bt.signal_masks(frame)
    assert masks[("aligned", "ignition")].iloc[60]
    assert not masks[("aligned", "shakeout")].iloc[60]


def test_immature_early_stop_is_not_a_complete_cohort_observation():
    frame = _frame(100)
    frame.loc[62, "Close"] = 60.
    assert bt.evaluate_trade(frame, 60, [], require_mature=True) is None
    marked = bt.evaluate_trade(frame, 60, [], require_mature=False)
    assert marked["status"] == "stopped_out"
    assert not marked["complete_cohort"]


def test_baseline_expiry_excludes_later_crash_and_includes_expiry_stop():
    frame = _frame()
    frame.loc[122, "Close"] = 60.
    result = bt.evaluate_trade(frame, 60, [])
    assert result["status"] == "matured"
    assert result["gross_return"] == 0
    frame.loc[121, "Close"] = 60.
    result = bt.evaluate_trade(frame, 60, [])
    assert result["status"] == "stopped_out"
    assert result["days_held"] == 60


def test_entry_day_dividend_excluded_post_entry_dividend_included():
    frame = _frame()
    frame.loc[62:, "Close"] = 90.
    events = [(frame.loc[61, "Date"], 8.), (frame.loc[62, "Date"], 10.)]
    result = bt.evaluate_trade(frame, 60, events)
    assert result["gross_return"] == 0
    assert result["status"] == "matured"
    assert bt.evaluate_trade(frame, 60, events, adjusted=False)["gross_return"] == pytest.approx(-.1)


def test_cost_sensitivity_is_monotone_and_no_daytrade_discount():
    assert bt.cost_adjusted_return(0., 0) == pytest.approx(.995575 / 1.001425 - 1)
    assert bt.cost_adjusted_return(.1, 0) > bt.cost_adjusted_return(.1, .001) > bt.cost_adjusted_return(.1, .0025)
