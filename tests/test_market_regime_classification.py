from __future__ import annotations

from ml.market_regime import (
    ACTION_BLOCK,
    ACTION_LIMIT,
    STATE_CAUTION,
    STATE_CLOSED,
    _classify_regime,
)


def test_narrow_breadth_with_positive_twii_trend_is_selective_caution() -> None:
    state, action, reasons = _classify_regime(
        twii_close=110.0,
        twii_ma20=105.0,
        twii_ma60=100.0,
        twii_ma20_slope_5d=0.02,
        breadth_ma20_ratio=0.386,
    )

    assert state == STATE_CAUTION
    assert action == ACTION_LIMIT
    assert "narrow_breadth_selective_open_35%_to_40%" in reasons


def test_deeply_weak_breadth_still_blocks_new_positions() -> None:
    state, action, reasons = _classify_regime(
        twii_close=110.0,
        twii_ma20=105.0,
        twii_ma60=100.0,
        twii_ma20_slope_5d=0.02,
        breadth_ma20_ratio=0.30,
    )

    assert state == STATE_CLOSED
    assert action == ACTION_BLOCK
    assert "breadth_ma20_below_40%" in reasons


def test_twii_below_ma60_still_blocks_even_with_selective_breadth() -> None:
    state, action, reasons = _classify_regime(
        twii_close=95.0,
        twii_ma20=90.0,
        twii_ma60=100.0,
        twii_ma20_slope_5d=0.02,
        breadth_ma20_ratio=0.386,
    )

    assert state == STATE_CLOSED
    assert action == ACTION_BLOCK
    assert "twii_below_ma60" in reasons
