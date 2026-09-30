from __future__ import annotations

import pandas as pd

from scripts.min_effective_positions_audit import (
    VARIANT_BASELINE,
    VARIANT_KEEP_CASH,
    _selected_rows_for_variant,
    summarize_variants,
)


def _toy_signal(active_count: int) -> pd.DataFrame:
    rows = []
    for idx in range(active_count):
        rows.append(
            {
                "ticker": f"T{idx:02d}",
                "target_units": 2,
                "target_weight_ratio": 1.0 / active_count,
                "rank_20d": idx + 1,
                "sector": "半導體業" if idx % 2 == 0 else "通信網路業",
            }
        )
    return pd.DataFrame(rows)


def test_keep_cash_buffer_keeps_cash_when_active_is_below_minimum() -> None:
    selected = _selected_rows_for_variant(
        _toy_signal(active_count=5),
        VARIANT_KEEP_CASH,
        min_active=10,
        top_n=30,
    )

    assert len(selected) == 5
    assert selected["variant_weight"].sum() == 10 / 60
    assert selected["variant_weight"].max() == 2 / 60


def test_keep_cash_buffer_matches_baseline_when_active_is_sufficient() -> None:
    df = _toy_signal(active_count=12)
    baseline = _selected_rows_for_variant(df, VARIANT_BASELINE, min_active=10, top_n=30)
    keep_cash = _selected_rows_for_variant(df, VARIANT_KEEP_CASH, min_active=10, top_n=30)

    assert keep_cash["variant_weight"].tolist() == baseline["variant_weight"].tolist()
    assert keep_cash["variant_weight"].sum() == 1.0


def test_summarize_variants_includes_cash_and_risk_metrics() -> None:
    counterfactual = pd.DataFrame(
        [
            {
                "variant": VARIANT_BASELINE,
                "basket_return_pct": 0.02,
                "basket_alpha_vs_twii_pct": 0.01,
                "selected_count": 5,
                "gross_invested_weight": 1.0,
                "cash_weight": 0.0,
                "max_name_weight": 0.20,
                "max_sector_weight": 0.60,
                "active_count": 5,
                "added_by_relax_gate": 0,
            },
            {
                "variant": VARIANT_BASELINE,
                "basket_return_pct": -0.01,
                "basket_alpha_vs_twii_pct": -0.02,
                "selected_count": 5,
                "gross_invested_weight": 1.0,
                "cash_weight": 0.0,
                "max_name_weight": 0.20,
                "max_sector_weight": 0.60,
                "active_count": 5,
                "added_by_relax_gate": 0,
            },
        ]
    )

    summary = summarize_variants(counterfactual, min_active=10).iloc[0]

    assert summary["mean_cash_weight"] == 0.0
    assert summary["daily_volatility_pct"] > 0
    assert "sortino_vs_zero" in summary
