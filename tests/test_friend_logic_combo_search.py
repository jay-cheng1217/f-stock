from __future__ import annotations

import pandas as pd

from scripts.friend_logic_combo_search import (
    build_overlay_masks,
    enrich_combo_features,
    extreme_trades_for_combo,
    search_combinations,
    select_combo,
    summarize_combo,
)


def _combo_frame() -> pd.DataFrame:
    dates = pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"])
    return pd.DataFrame(
        {
            "Date": dates,
            "ticker": ["1111", "2222", "3333", "4444"],
            "stock_name": ["Alpha", "Beta", "Gamma", "Delta"],
            "sector": ["Electronics", "Steel", "Finance", "Electronics"],
            "friend_pullback_limit": [True, True, False, True],
            "friend_base": [True, True, True, True],
            "friend_loose": [True, True, True, True],
            "friend_strict": [False, True, False, False],
            "friend_logic_score": [0.7, 0.4, 0.2, 0.1],
            "entry_date": dates + pd.Timedelta(days=1),
            "entry_open": [10.0, 20.0, 30.0, 40.0],
            "pullback_entry_price": [9.5, 19.5, None, 39.5],
            "gross_return_60d": [0.10, -0.05, 0.08, 0.03],
            "pullback_gross_return_60d": [0.15, -0.02, None, 0.04],
            "exit_date_60d": dates + pd.Timedelta(days=60),
            "exit_close_60d": [11.0, 19.0, 32.4, 41.2],
            "Close": [10.0, 20.0, 30.0, 40.0],
            "Volume": [1_000_000, 1_000_000, 1_000_000, 1_000_000],
            "VOL_MA_20": [1_250_000, 2_500_000, 1_000_000, 2_000_000],
            "MA_5": [9.8, 20.5, 29.0, 39.0],
            "MA_20": [11.0, 21.0, 30.0, 41.0],
            "MA_60": [9.9, 18.0, 28.0, 39.0],
            "Foreign_BuySell": [500.0, -100.0, 200.0, 50.0],
            "Trust_BuySell": [0.0, 0.0, 0.0, 0.0],
            "Dealer_BuySell": [10.0, -5.0, 0.0, 0.0],
            "inst_total": [510.0, -105.0, 200.0, 50.0],
            "MACDh_12_26_9": [-0.2, -1.2, 0.1, -0.5],
            "macd_improving": [True, True, False, True],
            "macd_hist_delta_3d": [0.4, 0.2, -0.1, 0.3],
            "RSI_14": [55.0, 65.0, 45.0, 58.0],
            "K": [60.0, 40.0, 50.0, 55.0],
            "D": [50.0, 45.0, 45.0, 50.0],
            "Margin_Balance": [100.0, 95.0, 94.0, 93.0],
            "return_1d": [0.01, 0.03, -0.01, 0.015],
        }
    )


def test_select_combo_uses_pullback_execution_and_overlays() -> None:
    data = enrich_combo_features(_combo_frame())
    masks = build_overlay_masks(data)

    selected = select_combo(
        data,
        base_rule="friend_pullback_limit",
        overlays=("flow_inst_positive", "macd_hist_gt_neg1", "volume_confirmed"),
        overlay_masks=masks,
        max_signals_per_day=10,
    )
    summary = summarize_combo(selected, hold=60, friction=0.004)

    assert selected["ticker"].tolist() == ["1111", "4444"]
    assert selected["stock_name"].tolist() == ["Alpha", "Delta"]
    assert selected["sector"].tolist() == ["Electronics", "Electronics"]
    assert selected["entry_open"].tolist() == [9.5, 39.5]
    assert round(float(summary["avg_net_return"]), 3) == 0.091
    assert summary["trades"] == 2

    extremes = extreme_trades_for_combo(selected, hold=60, friction=0.004, top_n=1)
    assert extremes["top"][0]["stock_name"] == "Alpha"
    assert extremes["top"][0]["sector"] == "Electronics"


def test_search_combinations_includes_ranked_local_overlays() -> None:
    data = enrich_combo_features(_combo_frame())
    results, selected_by_combo = search_combinations(
        data,
        max_overlays=1,
        max_signals_per_day=10,
        hold=60,
        friction=0.004,
    )

    combo_id = "friend_pullback_limit+flow_inst_positive"
    combo = results[results["combo_id"].eq(combo_id)].iloc[0]

    assert combo_id in selected_by_combo
    assert int(combo["trades"]) == 2
    assert combo["avg_net_return"] > 0
