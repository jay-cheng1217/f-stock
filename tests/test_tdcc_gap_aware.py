import numpy as np
import pandas as pd

from ml.features import tdcc as tdcc_module


def _write_summary(tmp_path, dates, whale, retail=None, holders=None):
    count = len(dates)
    frame = pd.DataFrame(
        {
            "Date": pd.DatetimeIndex(dates).strftime("%Y%m%d"),
            "Ticker": ["2330"] * count,
            "Retail_Pct": retail if retail is not None else np.linspace(60, 50, count),
            "Whale_Pct": whale,
            "Total_Holders": holders if holders is not None else np.arange(1000, 1000 + count),
        }
    )
    path = tmp_path / "tdcc_summary.csv"
    frame.to_csv(path, index=False)
    tdcc_module._TDCC_CACHE.clear()
    return path, frame


def _legacy_contiguous_features(frame):
    tk = frame.copy().reset_index(drop=True)
    tk["retail_pct"] = tk["Retail_Pct"].astype(np.float32)
    tk["whale_pct"] = tk["Whale_Pct"].astype(np.float32)
    tk["whale_pct_chg"] = tk["whale_pct"].diff().astype(np.float32)
    tk["retail_pct_chg"] = tk["retail_pct"].diff().astype(np.float32)
    tk["holders_chg_pct"] = (
        tk["Total_Holders"].astype(np.float64).pct_change().astype(np.float32)
    )
    tk["whale_retail_ratio"] = np.where(
        tk["retail_pct"] > 0.01,
        tk["whale_pct"] / tk["retail_pct"],
        np.nan,
    ).astype(np.float32)
    tk["whale_trend_4w"] = (
        tk["whale_pct"]
        .rolling(4, min_periods=2)
        .apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=False)
        .astype(np.float32)
    )
    chg = tk["whale_pct_chg"].fillna(0)
    streak = pd.Series(0, index=tk.index, dtype=np.int8)
    for index in range(1, len(streak)):
        streak.iloc[index] = streak.iloc[index - 1] + 1 if chg.iloc[index] > 0 else 0
    tk["whale_acc_weeks"] = streak.astype(np.float32)
    tk["whale_trend_8w"] = (
        tk["whale_pct"]
        .rolling(8, min_periods=4)
        .apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=False)
        .astype(np.float32)
    )
    tk["whale_acc_momentum"] = tk["whale_trend_4w"].diff().astype(np.float32)
    tk["whale_pct_rank_12w"] = (
        tk["whale_pct"].rolling(12, min_periods=4).rank(pct=True).astype(np.float32)
    )
    tk["whale_retail_diverge"] = (
        tk["whale_pct_chg"].fillna(0) - tk["retail_pct_chg"].fillna(0)
    ).astype(np.float32)
    tk["retail_capitulation"] = (
        tk["retail_pct"].rolling(12, min_periods=4).rank(pct=True).astype(np.float32)
    )
    return tk


def test_contiguous_week_features_are_exactly_unchanged(tmp_path):
    dates = pd.date_range("2026-01-02", periods=14, freq="W-FRI")
    path, source = _write_summary(
        tmp_path,
        dates,
        whale=np.array([40, 41, 42, 43, 42, 44, 45, 46, 47, 46, 48, 49, 50, 51]),
    )
    result = tdcc_module.compute_tdcc_features(
        pd.DataFrame({"Date": dates}), "2330", str(path)
    )
    legacy = _legacy_contiguous_features(source)

    pd.testing.assert_frame_equal(
        result[tdcc_module.TDCC_FEATURE_COLS],
        legacy[tdcc_module.TDCC_FEATURE_COLS],
        check_exact=True,
    )


def test_gap_week_and_first_observation_after_gap_do_not_bridge_changes(tmp_path):
    full_dates = pd.date_range("2026-01-02", periods=5, freq="W-FRI")
    observed_dates = full_dates.delete(2)
    path, _ = _write_summary(
        tmp_path,
        observed_dates,
        whale=[40.0, 41.0, 45.0, 46.0],
        retail=[60.0, 59.0, 55.0, 54.0],
        holders=[1000, 1010, 1100, 1110],
    )
    result = tdcc_module.compute_tdcc_features(
        pd.DataFrame({"Date": full_dates}), "2330", str(path)
    )

    assert pd.isna(result.loc[2, "whale_pct"])
    assert pd.isna(result.loc[2, "whale_pct_chg"])
    assert pd.isna(result.loc[3, "whale_pct_chg"])
    assert pd.isna(result.loc[3, "holders_chg_pct"])
    assert pd.isna(result.loc[3, "whale_retail_diverge"])
    assert result.loc[2, "whale_acc_weeks"] == 0.0
    assert result.loc[3, "whale_acc_weeks"] == 0.0
    assert result.loc[4, "whale_pct_chg"] == 1.0
    assert result.loc[4, "whale_acc_weeks"] == 1.0


def test_observed_thursday_keeps_actual_effective_date():
    rows = pd.DataFrame(
        {
            "tdcc_date": [pd.Timestamp("2026-07-03"), pd.Timestamp("2026-07-09")],
            "Whale_Pct": [40.0, 41.0],
        }
    )

    reindexed = tdcc_module._reindex_complete_tdcc_weeks(rows)

    assert reindexed["tdcc_date"].tolist() == [
        pd.Timestamp("2026-07-03"),
        pd.Timestamp("2026-07-09"),
    ]
    assert reindexed["_tdcc_observed"].tolist() == [True, True]
