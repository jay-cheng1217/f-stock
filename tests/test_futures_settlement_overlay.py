from __future__ import annotations

from datetime import date

import pandas as pd

from ml.futures_settlement import (
    PHASE_PRE_SETTLEMENT,
    PHASE_SETTLEMENT,
    monthly_equity_index_settlement_day,
    nearest_monthly_settlement_window,
)
from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    TRADABILITY_REASON_FUTURES_SETTLEMENT_WINDOW,
    _apply_futures_settlement_overlay,
    _assign_target_fields,
)


def test_monthly_settlement_adjusts_for_2026_lunar_new_year_holiday() -> None:
    assert monthly_equity_index_settlement_day(2026, 2) == date(2026, 2, 23)
    assert monthly_equity_index_settlement_day(2026, 5) == date(2026, 5, 20)


def test_settlement_window_flags_prior_trade_day_and_settlement_day() -> None:
    prior = nearest_monthly_settlement_window("2026-05-19")
    settlement = nearest_monthly_settlement_window("2026-05-20")
    normal = nearest_monthly_settlement_window("2026-05-18")

    assert prior.active
    assert prior.phase == PHASE_PRE_SETTLEMENT
    assert prior.settlement_date == "2026-05-20"
    assert settlement.active
    assert settlement.phase == PHASE_SETTLEMENT
    assert not normal.active


def test_futures_settlement_overlay_halves_normal_and_quarters_high_beta() -> None:
    df = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "beta_60_20d": 0.8,
                "tradability_reason": "",
            },
            {
                "ticker": "2222",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "beta_60_20d": 1.5,
                "tradability_reason": "",
            },
        ]
    )

    out = _apply_futures_settlement_overlay(_assign_target_fields(df), prediction_date="2026-05-18")
    by_ticker = {row["ticker"]: row for row in out.to_dict("records")}

    assert bool(by_ticker["1111"]["futures_settlement_window"]) is True
    assert by_ticker["1111"]["futures_settlement_phase"] == PHASE_PRE_SETTLEMENT
    assert by_ticker["1111"]["target_weight_ratio"] == 0.25
    assert by_ticker["1111"]["futures_settlement_action"] == "half_size_no_chase"
    assert by_ticker["2222"]["target_weight_ratio"] == 0.125
    assert by_ticker["2222"]["futures_settlement_action"] == "quarter_size_high_beta_no_chase"
    assert TRADABILITY_REASON_FUTURES_SETTLEMENT_WINDOW in by_ticker["2222"]["tradability_reason"]


def test_futures_settlement_overlay_does_not_change_normal_day() -> None:
    df = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "beta_60_20d": 1.5,
                "tradability_reason": "",
            }
        ]
    )

    out = _apply_futures_settlement_overlay(_assign_target_fields(df), prediction_date="2026-05-15")
    row = out.iloc[0]

    assert bool(row["futures_settlement_window"]) is False
    assert row["target_weight_ratio"] == 1.0
    assert row["futures_settlement_action"] == ""
