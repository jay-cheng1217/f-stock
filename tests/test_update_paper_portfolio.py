import pandas as pd

from scripts.update_paper_portfolio import _build_leaderboard


def test_build_leaderboard_filters_etfs_before_sector_cap_selection():
    pred_df = pd.DataFrame(
        [
            {
                "ticker": "0050",
                "date": "2026-04-30",
                "close": 180.0,
                "pred_return_20d": 0.90,
                "recommendation": "建議買進",
                "sector": "ETF",
            },
            {
                "ticker": "00981A",
                "date": "2026-04-30",
                "close": 10.0,
                "pred_return_20d": 0.80,
                "recommendation": "建議買進",
                "sector": "ETF",
            },
            {
                "ticker": "2330",
                "date": "2026-04-30",
                "close": 900.0,
                "pred_return_20d": 0.30,
                "recommendation": "建議買進",
                "sector": "半導體業",
            },
            {
                "ticker": "2317",
                "date": "2026-04-30",
                "close": 160.0,
                "pred_return_20d": 0.20,
                "recommendation": "建議買進",
                "sector": "其他電子業",
            },
            {
                "ticker": "1305",
                "date": "2026-04-30",
                "close": 20.0,
                "pred_return_20d": 0.10,
                "recommendation": "建議買進",
                "sector": "塑膠工業",
            },
        ]
    )

    leaderboard = _build_leaderboard(pred_df, top_n=3)

    assert leaderboard["ticker"].tolist() == ["2330", "2317", "1305"]
    assert leaderboard["selection_rank"].tolist() == [1, 2, 3]


def test_build_leaderboard_filters_etfs_from_legacy_sorted_selection():
    pred_df = pd.DataFrame(
        [
            {"ticker": "00679B", "date": "2026-04-30", "close": 30.0, "up_prob": 0.90},
            {"ticker": "00635U", "date": "2026-04-30", "close": 25.0, "up_prob": 0.80},
            {"ticker": "2330", "date": "2026-04-30", "close": 900.0, "up_prob": 0.30},
            {"ticker": "2317", "date": "2026-04-30", "close": 160.0, "up_prob": 0.20},
        ]
    )

    leaderboard = _build_leaderboard(pred_df, top_n=2)

    assert leaderboard["ticker"].tolist() == ["2330", "2317"]
    assert leaderboard["selection_rank"].tolist() == [1, 2]
