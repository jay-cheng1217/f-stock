from pathlib import Path

import pandas as pd

from scripts.champion_audit import check_group_cap, check_ma5_entry_quality


def test_ma5_entry_quality_is_informational_for_below_ma5_entries() -> None:
    df = pd.DataFrame(
        {
            "ticker": ["2330", "2317", "1305"],
            "signal_type": ["BUY", "BUY", "BUY"],
            "price_vs_ma5": [0.02, -0.02, -0.06],
        }
    )

    result = check_ma5_entry_quality(df, Path("unified_signals_2026-05-09.csv"))

    assert result.passed
    assert result.severity == "INFO"
    assert result.data["blocking"] is False
    assert result.data["below_ma5_count"] == 2
    assert result.data["below_guard_count"] == 1
    assert result.data["deep_below_count"] == 1


def test_ma5_entry_quality_still_fails_when_unobservable() -> None:
    df = pd.DataFrame({"ticker": ["9999"], "signal_type": ["BUY"]})

    result = check_ma5_entry_quality(df, Path("no_date.csv"))

    assert not result.passed
    assert result.severity == "CHECK"


def test_group_cap_fails_when_capped_group_exceeds_limit() -> None:
    df = pd.DataFrame(
        {
            "ticker": [f"10{i:02d}" for i in range(1, 31)],
            "signal_type": ["20D_only"] * 30,
            "group_code": ["CCL_MATERIALS"] * 5 + ["OTHER"] * 25,
            "group_name": ["CCL"] * 5 + ["Other / uncapped"] * 25,
        }
    )

    result = check_group_cap(df)

    assert not result.passed
    assert result.data["violations"] == {"CCL_MATERIALS": 5}


def test_group_cap_ignores_other_group() -> None:
    df = pd.DataFrame(
        {
            "ticker": [f"10{i:02d}" for i in range(1, 31)],
            "signal_type": ["20D_only"] * 30,
            "group_code": ["OTHER"] * 30,
            "group_name": ["Other / uncapped"] * 30,
        }
    )

    result = check_group_cap(df)

    assert result.passed
