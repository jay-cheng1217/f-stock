import numpy as np
import pandas as pd
import pytest

from scripts.strategy_scoreboard import STRATEGIES, _signals_for_frame, trades_for_frame, nonoverlapping, run
from scripts.generate_entry_candidates import _pattern_ok


def frame(n=180):
    close = pd.Series(np.linspace(100, 125, n))
    return pd.DataFrame({"Date": pd.bdate_range("2025-01-01", periods=n), "Close": close,
        "Volume": 1_000_000., "Foreign_BuySell": 100_000., "MA_20": close / 1.04,
        "MA_60": close / 1.1, "VOL_MA_20": 1_000_000., "RSI_14": 60., "MACDh_12_26_9": .5})


def signals_with_entry(d, index):
    sig = {name: pd.Series(False, index=d.index) for name in STRATEGIES}
    sig["main_pattern"].iloc[index] = True
    sig["close"] = d.Close
    return sig


def test_future_liquidity_cannot_remove_past_signals():
    a = frame(240)
    b = a.copy()
    b.loc[180:, "Volume"] = 1
    sa, sb = _signals_for_frame(a), _signals_for_frame(b)
    for name in STRATEGIES:
        pd.testing.assert_series_equal(sa[name].iloc[:180], sb[name].iloc[:180])
    assert sa["main_pattern"].iloc[100:180].any()
    assert not sb["main_pattern"].iloc[-1]


def test_main_pattern_uses_current_exact_stored_fields_and_predicate():
    d = frame()
    d.loc[80, "RSI_14"] = 90
    d.loc[90, "MACDh_12_26_9"] = -5
    sig = _signals_for_frame(d)
    for i in range(59, len(d)):
        r = d.iloc[i]
        technical = {"v20": r.Close / r.MA_20 - 1, "v60": r.Close / r.MA_60 - 1,
                     "vol_ratio": r.Volume / r.VOL_MA_20, "rsi": r.RSI_14,
                     "macd_delta_rel": (r.MACDh_12_26_9 - d.MACDh_12_26_9.iloc[i - 3]) / r.Close}
        assert bool(sig["main_pattern"].iloc[i]) == _pattern_ok(technical)


def test_20_day_maturity_does_not_require_60_day_outcome():
    d = frame(85)
    calendar = pd.DataFrame(columns=["stock_id", "date", "factor"])
    trades = trades_for_frame(d, "2330", signals_with_entry(d, 60), calendar)
    assert trades.h.tolist() == [20]
    assert trades.exit_date.iloc[0] == d.Date.iloc[81].strftime("%Y-%m-%d")


def test_economic_factor_return_removes_mechanical_exdiv_loss():
    d = frame(85)
    d["Close"] = 100.
    d.loc[70:, "Close"] = 90.
    calendar = pd.DataFrame({"stock_id": ["2330"], "date": [d.Date.iloc[70]], "factor": [.9]})
    trade = trades_for_frame(d, "2330", signals_with_entry(d, 60), calendar, friction=.004).iloc[0]
    assert trade.gross == pytest.approx(0)
    assert trade.r == pytest.approx(-.004)


def test_event_on_entry_date_is_excluded_from_holding_return():
    d = frame(85)
    d["Close"] = 100.
    d.loc[61:, "Close"] = 90.
    calendar = pd.DataFrame({"stock_id": ["2330"], "date": [d.Date.iloc[61]], "factor": [.9]})
    trade = trades_for_frame(d, "2330", signals_with_entry(d, 60), calendar).iloc[0]
    assert trade.gross == pytest.approx(0)


def test_nonoverlap_removes_repeated_entries_before_prior_exit():
    d = frame(140)
    sig = signals_with_entry(d, 60)
    sig["main_pattern"].iloc[61:95] = True
    trades = trades_for_frame(d, "2330", sig, pd.DataFrame(columns=["stock_id", "date", "factor"]))
    selected = nonoverlapping(trades)
    for _, group in selected.groupby(["s", "ticker", "h"]):
        ordered = group.sort_values("d")
        assert (ordered.d.iloc[1:].to_numpy() > ordered.exit_date.iloc[:-1].to_numpy()).all()


def test_missing_foreign_data_is_unknown_and_not_required_for_main_pattern():
    d = frame().drop(columns="Foreign_BuySell")
    sig = _signals_for_frame(d)
    assert sig["main_pattern"].any()
    assert not sig["ma20_combo"].any() and not sig["rere_lane"].any()


def test_report_data_end_uses_actual_bars_and_horizon_specific_cutoffs(tmp_path, monkeypatch):
    import scripts.strategy_scoreboard as board
    d = frame(140)
    d.to_csv(tmp_path / "2330.csv", index=False)
    calendar = pd.DataFrame({"stock_id": ["9999"], "date": [pd.Timestamp("2020-01-01")], "factor": [.9]})
    monkeypatch.setattr(board, "load_action_calendar", lambda: calendar)
    payload = run(daily_dir=tmp_path)
    assert payload["data_end"] == d.Date.max().strftime("%Y-%m-%d")
    assert payload["board"]["main_pattern"]["20d"]["latest_mature_entry_date"] > payload["board"]["main_pattern"]["60d"]["latest_mature_entry_date"]
    assert payload["board"]["main_pattern"]["20d"]["full"]["n"] > payload["board"]["main_pattern"]["60d"]["full"]["n"]
