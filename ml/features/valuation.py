"""估值面特徵工程模組

從每日 PE/PB/殖利率資料衍生估值特徵，
並合併至個股日K資料。
"""
import os
import glob
import pandas as pd
import numpy as np

# 估值資料快取: {valuation_dir: DataFrame with all dates}
_VALUATION_CACHE: dict = {}


def compute_valuation_features(
    daily_df: pd.DataFrame,
    ticker: str,
    valuation_dir: str,
) -> pd.DataFrame:
    """從每日估值資料計算估值面特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    valuation_dir : str
        估值資料目錄路徑 (如 F:\\stock\\估值資料)。

    Returns
    -------
    pd.DataFrame
        daily_df + 估值特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    # 載入並快取所有估值檔案
    if valuation_dir not in _VALUATION_CACHE:
        val_files = sorted(glob.glob(os.path.join(valuation_dir, "valuation_*.csv")))
        if val_files:
            frames = []
            for fpath in val_files:
                try:
                    basename = os.path.basename(fpath)
                    date_str = basename.replace("valuation_", "").replace(".csv", "")
                    vdf = pd.read_csv(fpath, dtype={"Ticker": str})
                    vdf["val_date"] = pd.Timestamp(date_str)
                    frames.append(vdf)
                except Exception:
                    continue
            _VALUATION_CACHE[valuation_dir] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        else:
            _VALUATION_CACHE[valuation_dir] = pd.DataFrame()

    all_val = _VALUATION_CACHE[valuation_dir]
    if all_val.empty:
        for col in VALUATION_FEATURE_COLS:
            out[col] = np.nan
        return out

    # 篩選該股票
    ticker_val = all_val[all_val["Ticker"] == str(ticker)]
    records = []
    for _, row in ticker_val.iterrows():
        records.append({
            "val_date": row["val_date"],
            "pe_ratio": pd.to_numeric(row.get("PE_Ratio", np.nan), errors="coerce"),
            "pb_ratio": pd.to_numeric(row.get("PB_Ratio", np.nan), errors="coerce"),
            "dividend_yield": pd.to_numeric(row.get("Dividend_Yield", np.nan), errors="coerce"),
        })

    if not records:
        for col in VALUATION_FEATURE_COLS:
            out[col] = np.nan
        return out

    val_df = pd.DataFrame(records).sort_values("val_date").reset_index(drop=True)

    # 估值特徵
    val_df["pe_ratio"] = val_df["pe_ratio"].astype(np.float32)
    val_df["pb_ratio"] = val_df["pb_ratio"].astype(np.float32)
    val_df["dividend_yield"] = val_df["dividend_yield"].astype(np.float32)

    # PE 相對歷史位置 (percentile over 60 days)
    val_df["pe_percentile_60d"] = (
        val_df["pe_ratio"].rolling(60, min_periods=20)
        .apply(lambda x: (x.iloc[-1] <= x).mean() if len(x) > 0 else np.nan, raw=False)
        .astype(np.float32)
    )

    # PB 變化率
    val_df["pb_change_20d"] = val_df["pb_ratio"].pct_change(20, fill_method=None).astype(np.float32)

    # Point-in-time merge
    feature_cols = ["val_date", "pe_ratio", "pb_ratio", "dividend_yield",
                    "pe_percentile_60d", "pb_change_20d"]
    val_features = val_df[feature_cols].copy()

    out = out.sort_values("Date").reset_index(drop=True)
    val_features = val_features.sort_values("val_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        val_features,
        left_on="Date",
        right_on="val_date",
        direction="backward",
    )

    out.drop(columns=["val_date"], inplace=True, errors="ignore")
    return out


# 本模組產出的特徵欄位名稱
VALUATION_FEATURE_COLS = [
    "pe_ratio",
    "pb_ratio",
    "dividend_yield",
    "pe_percentile_60d",
    "pb_change_20d",
]
