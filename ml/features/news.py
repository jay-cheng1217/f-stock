"""消息面特徵工程模組 — 基於 MOPS 重大訊息公告

從公司重大訊息公告記錄衍生真正的消息面特徵，
並透過 point-in-time join 對齊至日K資料時間軸。

特徵邏輯:
- 近 30 日公告次數: 公司訊息活躍度
- 近 7 日公告次數: 短期訊息密度
- 公告量異常比: 近 30 日 vs 過去 90 日平均的偏離
- 近 30 日是否有公告: 二元指標
"""

import os
import glob
import pandas as pd
import numpy as np


# 快取
_NEWS_CACHE: dict = {}


def _load_news_cache(news_dir: str) -> pd.DataFrame:
    """載入所有重大訊息並快取"""
    if news_dir in _NEWS_CACHE:
        return _NEWS_CACHE[news_dir]

    files = sorted(glob.glob(os.path.join(news_dir, "announcements_*.csv")))
    if not files:
        _NEWS_CACHE[news_dir] = pd.DataFrame()
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, dtype={"Ticker": str, "Date": str}, encoding="utf-8-sig")
            if not df.empty and "Date" in df.columns:
                frames.append(df[["Ticker", "Date"]].dropna())
        except Exception:
            continue

    if not frames:
        _NEWS_CACHE[news_dir] = pd.DataFrame()
        return pd.DataFrame()

    all_news = pd.concat(frames, ignore_index=True)
    all_news["Date"] = pd.to_datetime(all_news["Date"], errors="coerce")
    all_news = all_news.dropna(subset=["Date"])
    _NEWS_CACHE[news_dir] = all_news
    return all_news


def compute_news_features(
    daily_df: pd.DataFrame,
    ticker: str,
    news_dir: str,
) -> pd.DataFrame:
    """從重大訊息公告計算消息面特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    news_dir : str
        新聞資料目錄路徑 (含 announcements_YYYYMM.csv)。

    Returns
    -------
    pd.DataFrame
        daily_df + 消息面特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    all_news = _load_news_cache(news_dir)
    if all_news.empty:
        for col in NEWS_FEATURE_COLS:
            out[col] = np.nan
        return out

    # 篩選該股票的公告
    tk_news = all_news[all_news["Ticker"] == str(ticker)].copy()
    if tk_news.empty:
        for col in NEWS_FEATURE_COLS:
            out[col] = np.nan
        return out

    # 計算每日公告數
    daily_counts = tk_news.groupby("Date").size().reset_index(name="count")
    daily_counts = daily_counts.sort_values("Date").reset_index(drop=True)

    # 建立完整日期範圍的公告計數
    out = out.sort_values("Date").reset_index(drop=True)
    date_range = pd.DataFrame({"Date": out["Date"].unique()})
    date_range = date_range.merge(daily_counts, on="Date", how="left")
    date_range["count"] = date_range["count"].fillna(0)

    # 滾動計算
    date_range = date_range.sort_values("Date").reset_index(drop=True)
    date_range["ann_count_7d"] = (
        date_range["count"].rolling(7, min_periods=1).sum().astype(np.float32)
    )
    date_range["ann_count_30d"] = (
        date_range["count"].rolling(30, min_periods=1).sum().astype(np.float32)
    )

    # 公告量異常比: 近30日 vs 過去90日均值
    avg_90d = date_range["count"].rolling(90, min_periods=30).mean()
    date_range["ann_surprise"] = np.where(
        avg_90d > 0.01,
        (date_range["ann_count_30d"] / 30 - avg_90d) / avg_90d,
        0.0,
    ).astype(np.float32)

    # 是否有公告
    date_range["has_ann_30d"] = (date_range["ann_count_30d"] > 0).astype(np.float32)

    # 合併回 daily_df
    feature_cols = ["Date", "ann_count_7d", "ann_count_30d", "ann_surprise", "has_ann_30d"]
    out = out.merge(date_range[feature_cols], on="Date", how="left")

    return out


# 本模組產出的特徵欄位名稱
NEWS_FEATURE_COLS = [
    "ann_count_7d",
    "ann_count_30d",
    "ann_surprise",
    "has_ann_30d",
]
