"""產業類股特徵工程模組

計算同產業相對表現、產業動能等跨股票特徵。
需要先產生 sector_mapping.csv（由 scripts/fetch_sector_mapping.py 生成）。
"""

import os
import pandas as pd
import numpy as np

from ml.config import BASE_DIR

SECTOR_MAPPING_PATH = os.path.join(BASE_DIR, "ml", "data", "sector_mapping.csv")


def load_sector_mapping() -> pd.DataFrame | None:
    """載入股票→產業對應表"""
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return None
    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    return df[["Ticker", "Sector"]].drop_duplicates(subset=["Ticker"])


def compute_sector_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """計算產業類股特徵（需在全體股票合併後呼叫）

    特徵說明:
    - sector_return_rank: 個股當日報酬在同產業中的百分位排名
    - sector_avg_return_5d: 同產業平均 5 日報酬
    - sector_avg_return_20d: 同產業平均 20 日報酬
    - sector_relative_return_5d: 個股 5 日報酬 - 同產業平均
    - sector_relative_return_20d: 個股 20 日報酬 - 同產業平均
    - sector_momentum_5d: 產業整體 5 日動能 (同產業漲幅中位數)
    - sector_momentum_20d: 產業整體 20 日動能
    - sector_breadth: 產業廣度 (同產業中上漲股票比例)
    - sector_id: 產業編碼 (label encoded)
    """
    mapping = load_sector_mapping()
    if mapping is None:
        # 無對應表時填 NaN
        for col in SECTOR_FEATURE_COLS:
            dataset[col] = np.nan
        return dataset

    dataset = dataset.merge(mapping, left_on="ticker", right_on="Ticker", how="left")
    if "Ticker" in dataset.columns and "ticker" in dataset.columns:
        dataset.drop(columns=["Ticker"], inplace=True)

    # 需要先計算個股報酬 (若尚未存在)
    if "return_5d" not in dataset.columns:
        dataset["_ret_5d"] = dataset.groupby("ticker")["Close"].transform(
            lambda x: x.pct_change(5)
        )
    else:
        dataset["_ret_5d"] = dataset["return_5d"]

    if "return_20d" not in dataset.columns:
        dataset["_ret_20d"] = dataset.groupby("ticker")["Close"].transform(
            lambda x: x.pct_change(20)
        )
    else:
        dataset["_ret_20d"] = dataset["return_20d"]

    # 日報酬
    if "return_1d" not in dataset.columns:
        dataset["_ret_1d"] = dataset.groupby("ticker")["Close"].transform(
            lambda x: x.pct_change(1)
        )
    else:
        dataset["_ret_1d"] = dataset["return_1d"]

    # --- 同產業截面統計 ---
    # 個股當日報酬在同產業的排名
    dataset["sector_return_rank"] = (
        dataset.groupby(["Date", "Sector"])["_ret_1d"]
        .rank(pct=True)
        .astype(np.float32)
    )

    # 同產業平均報酬
    for w, col_name in [(5, "sector_avg_return_5d"), (20, "sector_avg_return_20d")]:
        ret_col = f"_ret_{w}d"
        sector_avg = dataset.groupby(["Date", "Sector"])[ret_col].transform("mean")
        dataset[col_name] = sector_avg.astype(np.float32)

    # 個股相對產業報酬
    dataset["sector_relative_return_5d"] = (
        dataset["_ret_5d"] - dataset["sector_avg_return_5d"]
    ).astype(np.float32)
    dataset["sector_relative_return_20d"] = (
        dataset["_ret_20d"] - dataset["sector_avg_return_20d"]
    ).astype(np.float32)

    # 產業動能 (中位數)
    for w, col_name in [(5, "sector_momentum_5d"), (20, "sector_momentum_20d")]:
        ret_col = f"_ret_{w}d"
        sector_median = dataset.groupby(["Date", "Sector"])[ret_col].transform("median")
        dataset[col_name] = sector_median.astype(np.float32)

    # 產業廣度 (上漲股比例)
    dataset["_up"] = (dataset["_ret_1d"] > 0).astype(float)
    dataset["sector_breadth"] = (
        dataset.groupby(["Date", "Sector"])["_up"]
        .transform("mean")
        .astype(np.float32)
    )

    # 產業編碼
    dataset["sector_id"] = (
        dataset["Sector"].astype("category").cat.codes.astype(np.float32)
    )
    # 無對應的設 NaN
    dataset.loc[dataset["Sector"].isna(), "sector_id"] = np.nan

    # 清除暫時欄位
    drop_cols = ["_ret_1d", "_ret_5d", "_ret_20d", "_up", "Sector"]
    dataset.drop(columns=[c for c in drop_cols if c in dataset.columns], inplace=True)

    return dataset


# 本模組產出的特徵欄位名稱
SECTOR_FEATURE_COLS = [
    "sector_return_rank",
    "sector_avg_return_5d",
    "sector_avg_return_20d",
    "sector_relative_return_5d",
    "sector_relative_return_20d",
    "sector_momentum_5d",
    "sector_momentum_20d",
    "sector_breadth",
    "sector_id",
]
