from __future__ import annotations

import pandas as pd

from ml.thresholds import OVERHEAT_DEFAULT_THRESHOLD
from scripts.build_unified_signals import (
    SECTOR_OVERHEAT_THRESHOLDS_ENV,
    SIGNAL_TYPE_20D_ONLY,
    SIGNAL_TYPE_NONE,
    TRADABILITY_REASON_OVERHEAT_RISK,
    _apply_overheat_risk_gate,
    _load_sector_threshold_config,
    _sector_overheat_context,
)


def _gate_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["tradability_status"] = "OPEN"
    df["tradability_blocked"] = False
    df["tradability_reason"] = ""
    return df


def test_sector_overheat_context_maps_real_sector_names() -> None:
    sectors = pd.Series(
        [
            "塑膠工業",
            "化學工業",
            "鋼鐵工業",
            "航運業",
            "食品工業",
            "半導體業",
            "生技醫療業",
            "電子零組件業",
            "其他電子業",
            "未知族群",
        ]
    )

    out = _sector_overheat_context(sectors)

    assert out["overheat_threshold"].tolist() == [
        0.25,
        0.25,
        0.25,
        0.25,
        0.35,
        0.55,
        0.50,
        0.40,
        0.40,
        OVERHEAT_DEFAULT_THRESHOLD,
    ]


def test_high_risk_groups_do_not_loosen_above_default() -> None:
    config = _load_sector_threshold_config()
    groups = config["groups"]
    for name in ["petrochemical", "steel_metal", "shipping"]:
        assert groups[name]["threshold"] <= OVERHEAT_DEFAULT_THRESHOLD


def test_production_default_keeps_sector_thresholds_disabled(monkeypatch) -> None:
    monkeypatch.delenv(SECTOR_OVERHEAT_THRESHOLDS_ENV, raising=False)
    df = _gate_frame(
        [
            {
                "ticker": "1305",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "price_vs_ma60_20d": 0.35,
                "beta_60_20d": 0.8,
                "sector_20d": "塑膠工業",
            }
        ]
    )

    out = _apply_overheat_risk_gate(df)
    row = out.iloc[0]

    assert row["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert bool(row["tradability_blocked"]) is False
    assert row["overheat_threshold"] == OVERHEAT_DEFAULT_THRESHOLD
    assert row["overheat_threshold_group"] == "production_default"


def test_sector_conditioned_overheat_blocks_plastics_above_pm_30pct_target(monkeypatch) -> None:
    monkeypatch.setenv(SECTOR_OVERHEAT_THRESHOLDS_ENV, "1")
    df = _gate_frame(
        [
            {
                "ticker": "1305",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "price_vs_ma60_20d": 0.35,
                "beta_60_20d": 0.8,
                "sector_20d": "塑膠工業",
            }
        ]
    )

    out = _apply_overheat_risk_gate(df)
    row = out.iloc[0]

    assert row["signal_type"] == SIGNAL_TYPE_NONE
    assert bool(row["tradability_blocked"]) is True
    assert row["overheat_threshold"] <= 0.30
    assert TRADABILITY_REASON_OVERHEAT_RISK in row["tradability_reason"]


def test_sector_conditioned_overheat_keeps_semiconductor_until_55pct(monkeypatch) -> None:
    monkeypatch.setenv(SECTOR_OVERHEAT_THRESHOLDS_ENV, "1")
    df = _gate_frame(
        [
            {
                "ticker": "2330",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "price_vs_ma60_20d": 0.45,
                "beta_60_20d": 0.8,
                "sector_20d": "半導體業",
            }
        ]
    )

    out = _apply_overheat_risk_gate(df)
    row = out.iloc[0]

    assert row["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert bool(row["tradability_blocked"]) is False
    assert row["overheat_threshold"] == 0.55
    assert TRADABILITY_REASON_OVERHEAT_RISK not in row["tradability_reason"]


def test_unmapped_sector_falls_back_to_40pct(monkeypatch) -> None:
    monkeypatch.setenv(SECTOR_OVERHEAT_THRESHOLDS_ENV, "1")
    df = _gate_frame(
        [
            {
                "ticker": "9999",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "price_vs_ma60_20d": 0.45,
                "beta_60_20d": 0.8,
                "sector_20d": "未知族群",
            }
        ]
    )

    out = _apply_overheat_risk_gate(df)
    row = out.iloc[0]

    assert row["signal_type"] == SIGNAL_TYPE_NONE
    assert row["overheat_threshold"] == OVERHEAT_DEFAULT_THRESHOLD
    assert TRADABILITY_REASON_OVERHEAT_RISK in row["tradability_reason"]
