from __future__ import annotations

import pandas as pd

from scripts.market_regime_caution_audit import (
    CautionVariant,
    REASON_MARKET_CAUTION,
    pre_market_pool,
    simulate_variant_for_date,
)


def _toy_rank20(caution: bool = True) -> pd.DataFrame:
    rows = []
    for rank in range(1, 13):
        reason_set: set[str] = set()
        if caution and rank > 10:
            reason_set = {REASON_MARKET_CAUTION}
        rows.append(
            {
                "ticker": f"T{rank:02d}",
                "rank_20d": rank,
                "sector": "半導體業" if rank % 2 else "通信網路業",
                "reason_set": reason_set,
                "market_regime_state": "CAUTION" if caution else "OPEN",
                "market_regime_action": "LIMIT_20D_TOP10_AND_BLOCK_T1" if caution else "ALLOW_NEW_POSITIONS",
            }
        )
    return pd.DataFrame(rows)


def test_top10_production_keeps_only_first_ten_on_caution_day() -> None:
    selected = simulate_variant_for_date(_toy_rank20(), CautionVariant("top10", 10, "test"))

    assert len(selected) == 10
    assert selected["rank_20d"].max() == 10


def test_top15_rescues_market_caution_rows() -> None:
    selected = simulate_variant_for_date(_toy_rank20(), CautionVariant("top15", 15, "test"))

    assert len(selected) == 12
    assert selected["rescued_from_market_caution"].sum() == 2


def test_non_market_blocks_are_not_reintroduced() -> None:
    df = _toy_rank20()
    df.loc[df["rank_20d"].eq(5), "reason_set"] = [{"OVERHEAT_RISK"}]

    pool = pre_market_pool(df)
    selected = simulate_variant_for_date(df, CautionVariant("top30", None, "test"))

    assert "T05" not in pool["ticker"].tolist()
    assert "T05" not in selected["ticker"].tolist()


def test_allow_day_ignores_rank_limit() -> None:
    selected = simulate_variant_for_date(_toy_rank20(caution=False), CautionVariant("top10", 10, "test"))

    assert len(selected) == 12
