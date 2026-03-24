"""集保分散特徵工程模組

從 TDCC 集保股權分散表衍生籌碼集中度特徵，
並透過 point-in-time join 對齊至日K資料時間軸。

TDCC 每週五更新，資料包含:
- Retail_Pct: 散戶持股比例 (持股分級 1-12, < 400張)
- Whale_Pct: 大戶持股比例 (持股分級 12-15, >= 400張)
- Total_Holders: 總持有人數

特徵邏輯:
- 散戶/大戶持股比例: 直接反映籌碼集中度
- 大戶持股變化: 週間大戶增減持方向
- 散戶持股變化: 散戶進出場動態
- 持有人數變化率: 籌碼分散/集中趨勢
- 大戶散戶比: 大戶佔散戶比例，越高越集中
"""

import os
import pandas as pd
import numpy as np


def compute_tdcc_features(
    daily_df: pd.DataFrame,
    ticker: str,
    tdcc_summary_path: str,
) -> pd.DataFrame:
    """從集保分散摘要計算籌碼集中度特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    tdcc_summary_path : str
        集保摘要 CSV 路徑 (tdcc_summary.csv)。

    Returns
    -------
    pd.DataFrame
        daily_df + 集保特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    if not os.path.exists(tdcc_summary_path):
        for col in TDCC_FEATURE_COLS:
            out[col] = np.nan
        return out

    # 載入摘要 (使用模組層級快取)
    tdcc_all = _load_tdcc_cache(tdcc_summary_path)
    if tdcc_all.empty:
        for col in TDCC_FEATURE_COLS:
            out[col] = np.nan
        return out

    # 篩選該股票
    tk = tdcc_all[tdcc_all["Ticker"] == str(ticker)].copy()
    if tk.empty:
        for col in TDCC_FEATURE_COLS:
            out[col] = np.nan
        return out

    tk = tk.sort_values("tdcc_date").reset_index(drop=True)

    # --- 基礎欄位 ---
    tk["retail_pct"] = tk["Retail_Pct"].astype(np.float32)
    tk["whale_pct"] = tk["Whale_Pct"].astype(np.float32)

    # --- 大戶持股週變化 (pct point change) ---
    tk["whale_pct_chg"] = tk["whale_pct"].diff().astype(np.float32)

    # --- 散戶持股週變化 ---
    tk["retail_pct_chg"] = tk["retail_pct"].diff().astype(np.float32)

    # --- 持有人數變化率 ---
    holders = tk["Total_Holders"].astype(np.float64)
    tk["holders_chg_pct"] = holders.pct_change().astype(np.float32)

    # --- 大戶/散戶比 ---
    tk["whale_retail_ratio"] = np.where(
        tk["retail_pct"] > 0.01,
        tk["whale_pct"] / tk["retail_pct"],
        np.nan,
    ).astype(np.float32)

    # --- 大戶持股 4 週趨勢 (正=持續增持) ---
    tk["whale_trend_4w"] = (
        tk["whale_pct"].rolling(4, min_periods=2).apply(
            lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) >= 2 else np.nan,
            raw=False,
        ).astype(np.float32)
    )

    # Point-in-time merge
    feature_cols = [
        "tdcc_date", "retail_pct", "whale_pct",
        "whale_pct_chg", "retail_pct_chg",
        "holders_chg_pct", "whale_retail_ratio", "whale_trend_4w",
    ]
    tdcc_features = tk[feature_cols].copy()

    out = out.sort_values("Date").reset_index(drop=True)
    tdcc_features = tdcc_features.sort_values("tdcc_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        tdcc_features,
        left_on="Date",
        right_on="tdcc_date",
        direction="backward",
    )

    out.drop(columns=["tdcc_date"], inplace=True, errors="ignore")
    return out


# --- 快取 ---
_TDCC_CACHE: dict = {}


def _load_tdcc_cache(path: str) -> pd.DataFrame:
    """載入集保摘要並快取"""
    if path in _TDCC_CACHE:
        return _TDCC_CACHE[path]

    try:
        df = pd.read_csv(path, dtype={"Ticker": str, "Date": str})
        df["tdcc_date"] = pd.to_datetime(df["Date"], format="mixed")
        _TDCC_CACHE[path] = df
        return df
    except Exception:
        _TDCC_CACHE[path] = pd.DataFrame()
        return pd.DataFrame()


# 本模組產出的特徵欄位名稱
def _load_tdcc_cache_v2(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        _TDCC_CACHE[path] = {"fingerprint": None, "df": pd.DataFrame()}
        return pd.DataFrame()

    st = os.stat(path)
    fingerprint = (st.st_mtime_ns, st.st_size)
    cached = _TDCC_CACHE.get(path)
    if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
        return cached.get("df", pd.DataFrame())

    try:
        df = pd.read_csv(path, dtype={"Ticker": str, "Date": str})
        required_cols = {"Date", "Ticker", "Retail_Pct", "Whale_Pct", "Total_Holders"}
        if required_cols - set(df.columns):
            clean_df = pd.DataFrame()
        else:
            clean_df = df.copy()
            clean_df["Date"] = (
                clean_df["Date"]
                .astype(str)
                .str.replace("/", "", regex=False)
                .str.replace("-", "", regex=False)
                .str.strip()
            )
            clean_df["Ticker"] = clean_df["Ticker"].astype(str).str.strip()
            for col in ["Retail_Pct", "Whale_Pct", "Total_Holders"]:
                clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce")
            clean_df["tdcc_date"] = pd.to_datetime(
                clean_df["Date"],
                format="%Y%m%d",
                errors="coerce",
            )
            clean_df = clean_df.dropna(subset=["tdcc_date", "Ticker"])
            clean_df = clean_df[clean_df["Ticker"].astype(str).str.len() > 0]
            clean_df = clean_df.drop_duplicates(subset=["Date", "Ticker"], keep="last")
            clean_df = clean_df.sort_values(["Ticker", "tdcc_date"]).reset_index(drop=True)

        _TDCC_CACHE[path] = {"fingerprint": fingerprint, "df": clean_df}
        return clean_df
    except Exception:
        empty = pd.DataFrame()
        _TDCC_CACHE[path] = {"fingerprint": fingerprint, "df": empty}
        return empty


_load_tdcc_cache = _load_tdcc_cache_v2


TDCC_FEATURE_COLS = [
    "retail_pct",
    "whale_pct",
    "whale_pct_chg",
    "retail_pct_chg",
    "holders_chg_pct",
    "whale_retail_ratio",
    "whale_trend_4w",
]
