"""產業聚合特徵工程模組 — 先行訊號偵測

目標：讓模型在「產業還沒全面起漲」時就看到先行訊號：
1. 產業資金流聚合 — 同產業法人買超合計，偵測主力佈局板塊
2. 產業營收動能 — 同產業營收 YoY 中位數，偵測景氣拐點
3. 領頭羊效應 — 產業內最強股漲幅，領頭羊帶動補漲
4. 產業 TDCC 籌碼集中 — 同產業大戶增持比例，靜默佈局

需要在全體股票合併後（cross-sectional）呼叫。
"""

import os
import pandas as pd
import numpy as np

from ml.config import BASE_DIR

SECTOR_MAPPING_PATH = os.path.join(BASE_DIR, "ml", "data", "sector_mapping.csv")
REVENUE_DIR = os.path.join(BASE_DIR, "月營收")


def _load_sector_map() -> pd.DataFrame | None:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return None
    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    return df[["Ticker", "Sector"]].drop_duplicates(subset=["Ticker"])


def compute_industry_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """計算產業聚合先行特徵（需在全體股票合併後呼叫）

    特徵:
    - industry_fund_flow_5d/10d/20d: 同產業法人買超合計 (標準化)
    - industry_foreign_pct: 同產業外資買超比例 (多少家被外資買)
    - industry_revenue_yoy_med: 同產業營收 YoY 中位數
    - industry_revenue_accel: 同產業營收加速度 (最新 - 3月前)
    - industry_leader_ret_5d/20d: 產業內最強股報酬 (領頭羊)
    - industry_whale_pct_avg: 同產業平均大戶增持幅度
    - industry_whale_acc_ratio: 同產業大戶增持家數占比
    """
    mapping = _load_sector_map()
    if mapping is None:
        for col in INDUSTRY_FEATURE_COLS:
            dataset[col] = np.nan
        return dataset

    # 合併產業
    if "_ind_sector" not in dataset.columns:
        dataset = dataset.merge(
            mapping.rename(columns={"Ticker": "_ind_tk", "Sector": "_ind_sector"}),
            left_on="ticker", right_on="_ind_tk", how="left",
        )
        dataset.drop(columns=["_ind_tk"], inplace=True, errors="ignore")

    has_sector = dataset["_ind_sector"].notna()

    # =========================================================
    # 1. 產業資金流 — 同產業法人買超合計
    # =========================================================
    # 用原始 Foreign_BuySell rolling sum（避免 percentile ranking 後失去量級資訊）
    # shift(1) 確保 T 日只用到 T-1 之前的法人資料（保守防 leakage）
    if "Foreign_BuySell" in dataset.columns:
        for w, prefix in [(5, "5d"), (10, "10d"), (20, "20d")]:
            roll_col = f"_fb_roll_{w}"
            dataset[roll_col] = (
                dataset.groupby("ticker")["Foreign_BuySell"]
                .transform(lambda x: x.rolling(w, min_periods=1).sum().shift(1))
            )
            dataset[f"industry_fund_flow_{prefix}"] = (
                dataset.groupby(["Date", "_ind_sector"])[roll_col]
                .transform("sum")
                .astype(np.float32)
            )
            dataset.drop(columns=[roll_col], inplace=True)
    else:
        for prefix in ["5d", "10d", "20d"]:
            dataset[f"industry_fund_flow_{prefix}"] = np.nan

    # 同產業外資買超比例（多少家近 5 日被外資淨買超）
    # shift(1) 同上
    if "Foreign_BuySell" in dataset.columns:
        dataset["_fb5"] = (
            dataset.groupby("ticker")["Foreign_BuySell"]
            .transform(lambda x: x.rolling(5, min_periods=1).sum().shift(1))
        )
        dataset["_fb_pos"] = (dataset["_fb5"] > 0).astype(float)
        dataset["industry_foreign_pct"] = (
            dataset.groupby(["Date", "_ind_sector"])["_fb_pos"]
            .transform("mean")
            .astype(np.float32)
        )
        dataset.drop(columns=["_fb5", "_fb_pos"], inplace=True)
    else:
        dataset["industry_foreign_pct"] = np.nan

    # =========================================================
    # 2. 產業營收動能 — 同產業營收 YoY 中位數 & 加速度
    # =========================================================
    if "revenue_yoy_latest" in dataset.columns:
        dataset["industry_revenue_yoy_med"] = (
            dataset.groupby(["Date", "_ind_sector"])["revenue_yoy_latest"]
            .transform("median")
            .astype(np.float32)
        )
    else:
        dataset["industry_revenue_yoy_med"] = np.nan

    if "revenue_yoy_momentum" in dataset.columns:
        dataset["industry_revenue_accel"] = (
            dataset.groupby(["Date", "_ind_sector"])["revenue_yoy_momentum"]
            .transform("median")
            .astype(np.float32)
        )
    else:
        dataset["industry_revenue_accel"] = np.nan

    # =========================================================
    # 3. 領頭羊效應 — 產業內最強股「截至昨日」報酬
    # =========================================================
    # 用 shift(1) 確保 T 日只看到 T-1 之前的報酬，避免 T 日 Close 資訊重疊
    for w in [5, 20]:
        ret_col = f"_leader_ret_{w}d_lagged"
        dataset[ret_col] = dataset.groupby("ticker")["Close"].transform(
            lambda x: x.pct_change(w).shift(1)
        )

        dataset[f"industry_leader_ret_{w}d"] = (
            dataset.groupby(["Date", "_ind_sector"])[ret_col]
            .transform("max")
            .astype(np.float32)
        )

        dataset.drop(columns=[ret_col], inplace=True)

    # =========================================================
    # 4. 產業 TDCC 籌碼集中 — 同產業大戶增持動態
    # =========================================================
    if "whale_pct_chg" in dataset.columns:
        # 同產業平均大戶持股變化
        dataset["industry_whale_pct_avg"] = (
            dataset.groupby(["Date", "_ind_sector"])["whale_pct_chg"]
            .transform("mean")
            .astype(np.float32)
        )
        # 同產業大戶增持家數占比
        dataset["_wacc"] = (dataset["whale_pct_chg"] > 0).astype(float)
        dataset["industry_whale_acc_ratio"] = (
            dataset.groupby(["Date", "_ind_sector"])["_wacc"]
            .transform("mean")
            .astype(np.float32)
        )
        dataset.drop(columns=["_wacc"], inplace=True)
    else:
        dataset["industry_whale_pct_avg"] = np.nan
        dataset["industry_whale_acc_ratio"] = np.nan

    # 清除暫時欄位
    dataset.drop(columns=["_ind_sector"], inplace=True, errors="ignore")

    return dataset


INDUSTRY_FEATURE_COLS = [
    # 產業資金流
    "industry_fund_flow_5d",
    "industry_fund_flow_10d",
    "industry_fund_flow_20d",
    "industry_foreign_pct",      # 同產業外資買超家數比例
    # 產業營收動能
    "industry_revenue_yoy_med",  # 同產業營收 YoY 中位數
    "industry_revenue_accel",    # 同產業營收加速度
    # 領頭羊效應
    "industry_leader_ret_5d",    # 產業最強股 5 日報酬
    "industry_leader_ret_20d",   # 產業最強股 20 日報酬
    # 產業 TDCC 籌碼集中
    "industry_whale_pct_avg",    # 同產業大戶增持平均
    "industry_whale_acc_ratio",  # 同產業大戶增持家數占比
]
