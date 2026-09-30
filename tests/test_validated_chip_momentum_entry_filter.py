from __future__ import annotations

import pandas as pd

from ml.predict import (
    VALIDATED_CHIP_MOMENTUM_GATE_VERSION,
    apply_group_cap,
    apply_sector_cap,
    apply_validated_chip_momentum_entry_filter,
)

BUY = "\u5efa\u8b70\u8cb7\u9032"


def _row(
    ticker: str,
    value: float,
    *,
    open_price: float = 20.0,
    volume: float = 500_000.0,
    amount: float = 50_000_000.0,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "date": "2026-06-10",
        "close": open_price,
        "Open": open_price,
        "Volume": volume,
        "AMOUNT_MA_20": amount,
        "inst_total_20d_norm": value,
        "foreign_cumsum_20d_norm": value,
        "trust_cumsum_20d_norm": value,
        "inst_buy_ratio_20d": value,
        "return_20d": value,
        "leaderboard_score": value,
        "pred_return_20d": value,
        "recommendation": BUY,
        "sector": "Tech",
        "group_code": "OTHER",
        "group_name": "Other",
        "inst_net_10d": 0.0,
    }


def test_validated_chip_momentum_filter_keeps_liquid_top_n(monkeypatch):
    monkeypatch.setenv("VALIDATED_CHIP_MOMENTUM_TOP_N", "2")
    monkeypatch.delenv("VALIDATED_CHIP_MOMENTUM_GATE_ENABLED", raising=False)
    frame = pd.DataFrame(
        [
            _row("1001", 0.99, volume=100_000.0),
            _row("1002", 0.90),
            _row("1003", 0.80),
            _row("1004", 0.70),
        ]
    )

    out = apply_validated_chip_momentum_entry_filter(frame)

    assert out["ticker"].tolist() == ["1002", "1003"]
    assert out["validated_chip_momentum_gate_version"].eq(VALIDATED_CHIP_MOMENTUM_GATE_VERSION).all()
    assert out["validated_chip_momentum_rank"].tolist() == [1.0, 2.0]
    assert out["validated_chip_momentum_liquidity_pass"].eq(True).all()
    assert out["validated_chip_momentum_gate_pass"].eq(True).all()


def test_validated_chip_momentum_filter_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VALIDATED_CHIP_MOMENTUM_GATE_ENABLED", "0")
    monkeypatch.setenv("VALIDATED_CHIP_MOMENTUM_TOP_N", "1")
    frame = pd.DataFrame([_row("1001", 0.90), _row("1002", 0.80)])

    out = apply_validated_chip_momentum_entry_filter(frame)

    assert out["ticker"].tolist() == ["1001", "1002"]
    assert out["validated_chip_momentum_gate_enabled"].eq(False).all()
    assert out["validated_chip_momentum_gate_pass"].tolist() == [True, False]


def test_validated_chip_momentum_filter_fails_open_with_missing_legacy_columns(monkeypatch):
    monkeypatch.delenv("VALIDATED_CHIP_MOMENTUM_GATE_ENABLED", raising=False)
    frame = pd.DataFrame(
        [
            {"ticker": "1001", "leaderboard_score": 0.90},
            {"ticker": "1002", "leaderboard_score": 0.80},
        ]
    )

    out = apply_validated_chip_momentum_entry_filter(frame)

    assert out["ticker"].tolist() == ["1001", "1002"]
    assert out["validated_chip_momentum_gate_error"].str.startswith("missing_columns:").all()


def test_sector_and_group_caps_do_not_reintroduce_filtered_names(monkeypatch):
    monkeypatch.setenv("VALIDATED_CHIP_MOMENTUM_TOP_N", "2")
    monkeypatch.delenv("VALIDATED_CHIP_MOMENTUM_GATE_ENABLED", raising=False)
    frame = pd.DataFrame(
        [
            _row("1001", 0.99, amount=1_000_000.0),
            _row("1002", 0.90),
            _row("1003", 0.80),
            _row("1004", 0.70),
        ]
    )

    sector_seed = apply_sector_cap(frame, top_n=30, cap_ratio=1.0)
    out = apply_group_cap(frame, top_n=30, initial_df=sector_seed, sector_cap_ratio=1.0)

    assert sector_seed["ticker"].tolist() == ["1002", "1003"]
    assert out["ticker"].tolist() == ["1002", "1003"]


def test_validated_chip_momentum_filter_handles_duplicate_feature_columns(monkeypatch):
    monkeypatch.setenv("VALIDATED_CHIP_MOMENTUM_TOP_N", "1")
    monkeypatch.delenv("VALIDATED_CHIP_MOMENTUM_GATE_ENABLED", raising=False)
    frame = pd.DataFrame([_row("1001", 0.50), _row("1002", 0.50)])
    duplicate = pd.DataFrame({"inst_buy_ratio_20d": [0.10, 0.99]})
    frame = pd.concat([frame, duplicate], axis=1)

    out = apply_validated_chip_momentum_entry_filter(frame)

    assert out["ticker"].tolist() == ["1002"]
    assert out["validated_chip_momentum_gate_error"].eq("").all()
    assert out["validated_chip_momentum_gate_pass"].eq(True).all()
