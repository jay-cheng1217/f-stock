from __future__ import annotations

import pandas as pd

from ml.predict import apply_group_cap


def _row(ticker: str, score: float, sector: str, group_code: str) -> dict[str, object]:
    return {
        "ticker": ticker,
        "risk_adjusted_return": score,
        "recommendation": "建議買進",
        "sector": sector,
        "group_code": group_code,
        "group_name": group_code,
        "inst_net_10d": 0.0,
        "avg_20d_volume": 1_000_000.0,
    }


def test_group_cap_replaces_lowest_ranked_over_cap_name() -> None:
    full = pd.DataFrame(
        [
            _row("1001", 0.90, "電子零組件業", "CCL_MATERIALS"),
            _row("1002", 0.89, "電子零組件業", "CCL_MATERIALS"),
            _row("1003", 0.88, "電子零組件業", "CCL_MATERIALS"),
            _row("1004", 0.87, "電子零組件業", "CCL_MATERIALS"),
            _row("2001", 0.86, "半導體業", "SEMI_EQUIP_MATERIALS"),
            _row("2002", 0.85, "半導體業", "SEMI_EQUIP_MATERIALS"),
            _row("3001", 0.84, "通信網路業", "OPTICAL_COMM"),
        ]
    )
    initial = full.head(5).copy()

    out = apply_group_cap(full, top_n=5, cap_ratio=0.60, initial_df=initial, sector_cap_ratio=1.0)

    assert len(out) == 5
    assert (out["group_code"] == "CCL_MATERIALS").sum() == 3
    assert "1004" not in set(out["ticker"])
    replacement = out.loc[out["group_cap_applied"].astype(bool)].iloc[0]
    assert replacement["ticker"] in {"2002", "3001"}
    assert bool(replacement["group_cap_applied"]) is True


def test_group_cap_does_not_cap_other_group() -> None:
    full = pd.DataFrame(
        [
            _row("1001", 0.90, "其他業", "OTHER"),
            _row("1002", 0.89, "其他業", "OTHER"),
            _row("1003", 0.88, "其他業", "OTHER"),
            _row("1004", 0.87, "其他業", "OTHER"),
        ]
    )

    out = apply_group_cap(full, top_n=4, cap_ratio=0.25, initial_df=full, sector_cap_ratio=1.0)

    assert len(out) == 4
    assert set(out["ticker"]) == {"1001", "1002", "1003", "1004"}
