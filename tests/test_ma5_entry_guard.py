from __future__ import annotations

import pandas as pd

from ml.thresholds import ENTRY_MA5_MIN_PCT
from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    SIGNAL_TYPE_NONE,
    TRADABILITY_REASON_ENTRY_MA5_WEAK,
    TRADABILITY_REASON_MISSING_ENTRY_MA5_CONTEXT,
    TRADABILITY_STATUS_OPEN,
    _apply_ma5_entry_guard,
)


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "1111",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 1,
                "price_vs_ma5_20d": ENTRY_MA5_MIN_PCT,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "2222",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 2,
                "price_vs_ma5_20d": ENTRY_MA5_MIN_PCT - 0.001,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "3333",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 3,
                "price_vs_ma5_20d": None,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
        ]
    )


def test_ma5_entry_guard_blocks_weak_or_missing_20d_context() -> None:
    out = _apply_ma5_entry_guard(_base_df())
    by_ticker = {row["ticker"]: row for row in out.to_dict("records")}

    assert by_ticker["1111"]["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert not bool(by_ticker["1111"]["tradability_blocked"])

    assert by_ticker["2222"]["signal_type"] == SIGNAL_TYPE_NONE
    assert bool(by_ticker["2222"]["tradability_blocked"])
    assert TRADABILITY_REASON_ENTRY_MA5_WEAK in by_ticker["2222"]["tradability_reason"]

    assert by_ticker["3333"]["signal_type"] == SIGNAL_TYPE_NONE
    assert bool(by_ticker["3333"]["tradability_blocked"])
    assert TRADABILITY_REASON_MISSING_ENTRY_MA5_CONTEXT in by_ticker["3333"]["tradability_reason"]
