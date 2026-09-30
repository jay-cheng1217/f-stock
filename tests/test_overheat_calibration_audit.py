from __future__ import annotations

import pandas as pd

from scripts.overheat_risk_calibration_audit import (
    ThresholdVariant,
    REASON_MARKET_CAUTION,
    REASON_OVERHEAT,
    simulate_variant_for_date,
)


def _toy_rank20() -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "ticker": "A",
                "rank_20d": 1,
                "sector": "半導體業",
                "price_vs_ma60_used": 0.38,
                "reason_set": {REASON_OVERHEAT},
                "market_regime_state": "OPEN",
                "market_regime_action": "ALLOW_NEW_POSITIONS",
                "market_regime_twii_close": 110.0,
                "market_regime_twii_ma20": 105.0,
                "market_regime_twii_ma60": 100.0,
            },
            {
                "ticker": "B",
                "rank_20d": 2,
                "sector": "通信網路業",
                "price_vs_ma60_used": 0.42,
                "reason_set": {REASON_OVERHEAT},
                "market_regime_state": "OPEN",
                "market_regime_action": "ALLOW_NEW_POSITIONS",
                "market_regime_twii_close": 110.0,
                "market_regime_twii_ma20": 105.0,
                "market_regime_twii_ma60": 100.0,
            },
            {
                "ticker": "C",
                "rank_20d": 3,
                "sector": "電子零組件業",
                "price_vs_ma60_used": 0.20,
                "reason_set": {"HIGH_BETA_RISK"},
                "market_regime_state": "OPEN",
                "market_regime_action": "ALLOW_NEW_POSITIONS",
                "market_regime_twii_close": 110.0,
                "market_regime_twii_ma20": 105.0,
                "market_regime_twii_ma60": 100.0,
            },
        ]
    )
    return df


def test_fixed_threshold_rescues_only_overheat_rows_without_other_blocks() -> None:
    variant = ThresholdVariant("test_40", "price_vs_ma60_used", 0.40, "test")
    selected = simulate_variant_for_date(_toy_rank20(), variant)

    assert selected["ticker"].tolist() == ["A"]
    assert selected["rescued_from_overheat"].tolist() == [True]


def test_regime_adaptive_uses_trending_threshold() -> None:
    variant = ThresholdVariant("adaptive", "price_vs_ma60_used", None, "test")
    selected = simulate_variant_for_date(_toy_rank20(), variant)

    assert selected["ticker"].tolist() == ["A", "B"]
    assert selected["overheat_threshold"].iloc[0] == 0.50


def test_caution_keeps_top10_limit_even_when_threshold_passes() -> None:
    df = pd.concat([_toy_rank20()] * 4, ignore_index=True)
    df["rank_20d"] = range(1, len(df) + 1)
    df["ticker"] = [f"T{i:02d}" for i in df["rank_20d"]]
    df["price_vs_ma60_used"] = 0.10
    df["reason_set"] = df["rank_20d"].map(
        lambda rank: {REASON_MARKET_CAUTION} if rank > 10 else set()
    )
    df["market_regime_state"] = "CAUTION"
    df["market_regime_action"] = "LIMIT_20D_TOP10_AND_BLOCK_T1"

    variant = ThresholdVariant("adaptive", "price_vs_ma60_used", None, "test")
    selected = simulate_variant_for_date(df, variant)

    assert selected["rank_20d"].max() == 10
    assert len(selected) == 10
