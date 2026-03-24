"""EPS（每股盈餘）特徵工程模組

從季報 EPS 資料衍生盈餘特徵，
並透過 point-in-time join 對齊至日K資料時間軸。

注意: MOPS 的 EPS 資料為年度累計值:
  Q1 = 1-3月 EPS
  Q2 = 1-6月 累計 EPS
  Q3 = 1-9月 累計 EPS
  Q4 = 全年 累計 EPS
需要差分取得單季 EPS。

季報公布時程 (保守估計):
  Q1 -> 5/15,  Q2 -> 8/14,  Q3 -> 11/14,  Q4 -> 隔年 3/31
"""

import os
import pandas as pd
import numpy as np


# 季報公布日 (保守 lag)
_QUARTER_AVAILABLE = {
    1: (0, 5, 15),   # Q1 -> 同年 5/15
    2: (0, 8, 14),   # Q2 -> 同年 8/14
    3: (0, 11, 14),  # Q3 -> 同年 11/14
    4: (1, 3, 31),   # Q4 -> 隔年 3/31
}

# EPS 資料快取 (key: financial_dir, value: dict of (year, season) -> DataFrame)
_EPS_CACHE: dict = {}


def _get_available_date(year: int, season: int) -> pd.Timestamp:
    year_offset, month, day = _QUARTER_AVAILABLE[season]
    return pd.Timestamp(year=year + year_offset, month=month, day=day)


def _load_eps_cache(financial_dir: str) -> dict:
    """載入所有 EPS 檔案到快取（只載入一次）"""
    if financial_dir in _EPS_CACHE:
        return _EPS_CACHE[financial_dir]

    cache = {}
    for fname in os.listdir(financial_dir):
        if not fname.startswith("eps_") or not fname.endswith(".csv"):
            continue
        try:
            name = fname.replace("eps_", "").replace(".csv", "")
            year = int(name[:4])
            season = int(name[5])
        except (ValueError, IndexError):
            continue
        fpath = os.path.join(financial_dir, fname)
        try:
            df = pd.read_csv(fpath, dtype={"Ticker": str})
            cache[(year, season)] = df
        except Exception:
            continue

    _EPS_CACHE[financial_dir] = cache
    return cache


def compute_eps_features(
    daily_df: pd.DataFrame,
    ticker: str,
    financial_dir: str,
) -> pd.DataFrame:
    """從季報 EPS 資料計算盈餘特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    financial_dir : str
        季報財務目錄路徑，需包含 eps_{year}Q{season}.csv 檔案。

    Returns
    -------
    pd.DataFrame
        daily_df + EPS 特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    # 使用快取載入 EPS 資料
    eps_cache = _load_eps_cache(financial_dir)

    records = []
    for (year, season), qdf in eps_cache.items():
        row = qdf[qdf["Ticker"] == str(ticker)]
        if row.empty:
            continue
        row = row.iloc[0]

        eps_cumul = pd.to_numeric(row.get("EPS_Basic", np.nan), errors="coerce")
        if pd.notna(eps_cumul) and abs(eps_cumul) > 500:
            continue

        records.append({
            "year": year,
            "season": season,
            "available_date": _get_available_date(year, season),
            "eps_cumul": eps_cumul,
        })

    if not records:
        for col in EPS_FEATURE_COLS:
            out[col] = np.nan
        return out

    eps_df = pd.DataFrame(records)
    eps_df = eps_df.sort_values(["year", "season"]).reset_index(drop=True)

    # --- 從累計 EPS 推算單季 EPS ---
    # Q1: 單季 = 累計值, Q2-Q4: 單季 = 本季累計 - 上季累計
    single_eps = []
    for _, row in eps_df.iterrows():
        y, s, cumul = int(row["year"]), int(row["season"]), row["eps_cumul"]
        if s == 1:
            single_eps.append(cumul)
        else:
            prev = eps_df[(eps_df["year"] == y) & (eps_df["season"] == s - 1)]
            if not prev.empty and pd.notna(prev.iloc[0]["eps_cumul"]):
                single_eps.append(cumul - prev.iloc[0]["eps_cumul"])
            else:
                single_eps.append(np.nan)
    eps_df["eps_single_q"] = np.array(single_eps, dtype=np.float32)

    # 重新排序按 available_date
    eps_df = eps_df.sort_values("available_date").reset_index(drop=True)

    # --- 單季 EPS (eps_basic) ---
    eps_df["eps_basic"] = eps_df["eps_single_q"].astype(np.float32)

    # --- 近四季 EPS 合計 (TTM) ---
    eps_df["eps_ttm"] = (
        eps_df["eps_single_q"].rolling(4, min_periods=4).sum().astype(np.float32)
    )

    # --- EPS YoY: 與去年同季比較 ---
    eps_df["eps_yoy"] = np.nan
    for i in range(len(eps_df)):
        curr = eps_df.iloc[i]
        prev = eps_df[
            (eps_df["year"] == curr["year"] - 1) &
            (eps_df["season"] == curr["season"])
        ]
        if not prev.empty:
            prev_eps = prev.iloc[0]["eps_single_q"]
            curr_eps = curr["eps_single_q"]
            if pd.notna(prev_eps) and pd.notna(curr_eps) and abs(prev_eps) > 0.001:
                eps_df.loc[eps_df.index[i], "eps_yoy"] = (
                    (curr_eps - prev_eps) / abs(prev_eps)
                )
    eps_df["eps_yoy"] = eps_df["eps_yoy"].astype(np.float32)

    # --- EPS QoQ: 與上一季比較 ---
    prev_q = eps_df["eps_single_q"].shift(1)
    eps_df["eps_qoq"] = np.where(
        prev_q.abs() > 0.001,
        (eps_df["eps_single_q"] - prev_q) / prev_q.abs(),
        np.nan,
    ).astype(np.float32)

    # --- EPS 動能: 近兩季 EPS 趨勢 ---
    eps_df["eps_momentum"] = (
        eps_df["eps_single_q"] - eps_df["eps_single_q"].shift(2)
    ).astype(np.float32)

    # point-in-time merge
    feature_cols = [
        "available_date", "eps_basic", "eps_ttm",
        "eps_yoy", "eps_qoq", "eps_momentum",
    ]
    eps_features = eps_df[feature_cols].copy()

    out = out.sort_values("Date").reset_index(drop=True)
    eps_features = eps_features.sort_values("available_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        eps_features,
        left_on="Date",
        right_on="available_date",
        direction="backward",
    )

    out.drop(columns=["available_date"], inplace=True, errors="ignore")
    return out


# 本模組產出的特徵欄位名稱
EPS_FEATURE_COLS = [
    "eps_basic",
    "eps_ttm",
    "eps_yoy",
    "eps_qoq",
    "eps_momentum",
]
