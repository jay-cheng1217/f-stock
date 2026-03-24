"""資產負債表特徵工程模組

從資產負債表資料衍生 ROE、ROA、負債比、流動比率等特徵，
並透過 point-in-time join 對齊至日K資料時間軸。

季報公布時程 (保守估計):
  Q1 → 5/15,  Q2 → 8/14,  Q3 → 11/14,  Q4 → 隔年 3/31
"""

import os
import pandas as pd
import numpy as np


# 季報公布日 (保守 lag)
_QUARTER_AVAILABLE = {
    1: (0, 5, 15),
    2: (0, 8, 14),
    3: (0, 11, 14),
    4: (1, 3, 31),
}

_BS_CACHE: dict = {}


def _get_available_date(year: int, season: int) -> pd.Timestamp:
    year_offset, month, day = _QUARTER_AVAILABLE[season]
    return pd.Timestamp(year=year + year_offset, month=month, day=day)


def _load_bs_cache(bs_dir: str) -> dict:
    """載入所有資產負債表檔案到快取"""
    if bs_dir in _BS_CACHE:
        return _BS_CACHE[bs_dir]

    cache = {}
    for fname in os.listdir(bs_dir):
        if not fname.startswith("bs_") or not fname.endswith(".csv"):
            continue
        try:
            name = fname.replace("bs_", "").replace(".csv", "")
            year = int(name[:4])
            season = int(name[5])
        except (ValueError, IndexError):
            continue
        fpath = os.path.join(bs_dir, fname)
        try:
            df = pd.read_csv(fpath, dtype={"Ticker": str})
            cache[(year, season)] = df
        except Exception:
            continue

    _BS_CACHE[bs_dir] = cache
    return cache


def compute_balance_sheet_features(
    daily_df: pd.DataFrame,
    ticker: str,
    bs_dir: str,
    financial_dir: str | None = None,
) -> pd.DataFrame:
    """從資產負債表計算財務健全度特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    bs_dir : str
        資產負債表目錄路徑 (如 F:\\stock\\資產負債)。
    financial_dir : str or None
        季報財務目錄（用於計算 ROE/ROA），可為 None。

    Returns
    -------
    pd.DataFrame
        daily_df + 資產負債表特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    bs_cache = _load_bs_cache(bs_dir)

    # 從季報財務取得淨利 (用於計算 ROE/ROA)
    net_income_map = {}
    if financial_dir and os.path.isdir(financial_dir):
        for fname in os.listdir(financial_dir):
            if not fname.startswith("financial_") or not fname.endswith(".csv"):
                continue
            try:
                name = fname.replace("financial_", "").replace(".csv", "")
                year = int(name[:4])
                season = int(name[5])
            except (ValueError, IndexError):
                continue
            fpath = os.path.join(financial_dir, fname)
            try:
                fdf = pd.read_csv(fpath, dtype={"Ticker": str})
                row = fdf[fdf["Ticker"] == str(ticker)]
                if not row.empty:
                    r = row.iloc[0]
                    ni = pd.to_numeric(r.get("Net_Income_M", np.nan), errors="coerce")
                    # 若無 Net_Income_M，從 Revenue_M * Net_Margin_Pct 推算
                    if pd.isna(ni):
                        rev = pd.to_numeric(r.get("Revenue_M", np.nan), errors="coerce")
                        npm = pd.to_numeric(r.get("Net_Margin_Pct", np.nan), errors="coerce")
                        if pd.notna(rev) and pd.notna(npm):
                            ni = rev * npm / 100.0
                    net_income_map[(year, season)] = ni
            except Exception:
                continue

    records = []
    for (year, season), qdf in bs_cache.items():
        row = qdf[qdf["Ticker"] == str(ticker)]
        if row.empty:
            continue
        row = row.iloc[0]

        total_assets = pd.to_numeric(row.get("Total_Assets", np.nan), errors="coerce")
        total_liab = pd.to_numeric(row.get("Total_Liabilities", np.nan), errors="coerce")
        total_equity = pd.to_numeric(row.get("Total_Equity", np.nan), errors="coerce")
        current_assets = pd.to_numeric(row.get("Current_Assets", np.nan), errors="coerce")
        current_liab = pd.to_numeric(row.get("Current_Liabilities", np.nan), errors="coerce")
        bvps = pd.to_numeric(row.get("Book_Value_Per_Share", np.nan), errors="coerce")
        debt_ratio = pd.to_numeric(row.get("Debt_Ratio", np.nan), errors="coerce")
        current_ratio = pd.to_numeric(row.get("Current_Ratio", np.nan), errors="coerce")

        # 如果 Debt_Ratio 未預算，手動算
        if pd.isna(debt_ratio) and pd.notna(total_liab) and pd.notna(total_assets) and total_assets > 0:
            debt_ratio = total_liab / total_assets * 100

        # 如果 Current_Ratio 未預算，手動算
        if pd.isna(current_ratio) and pd.notna(current_assets) and pd.notna(current_liab) and current_liab > 0:
            current_ratio = current_assets / current_liab * 100

        # ROE = 淨利 / 股東權益 * 100
        # 注意: net_income 單位為百萬, 資產負債表單位為千元
        # 換算: 百萬 * 1000 = 千元
        net_income = net_income_map.get((year, season), np.nan)
        roe = np.nan
        if pd.notna(net_income) and pd.notna(total_equity) and abs(total_equity) > 0.01:
            ni_thousands = net_income * 1000  # 百萬 → 千元
            roe = ni_thousands / total_equity * 100

        # ROA = 淨利 / 總資產 * 100
        roa = np.nan
        if pd.notna(net_income) and pd.notna(total_assets) and abs(total_assets) > 0.01:
            ni_thousands = net_income * 1000
            roa = ni_thousands / total_assets * 100

        records.append({
            "year": year,
            "season": season,
            "available_date": _get_available_date(year, season),
            "debt_ratio": debt_ratio,
            "current_ratio": current_ratio,
            "roe_annualized": roe,
            "roa_annualized": roa,
            "book_value_per_share": bvps,
            "equity_ratio": (total_equity / total_assets * 100) if (
                pd.notna(total_equity) and pd.notna(total_assets) and total_assets > 0
            ) else np.nan,
        })

    if not records:
        for col in BALANCE_SHEET_FEATURE_COLS:
            out[col] = np.nan
        return out

    bs_df = pd.DataFrame(records)
    bs_df = bs_df.sort_values(["year", "season"]).reset_index(drop=True)

    for col in ["debt_ratio", "current_ratio", "roe_annualized", "roa_annualized",
                 "book_value_per_share", "equity_ratio"]:
        bs_df[col] = pd.to_numeric(bs_df[col], errors="coerce").astype(np.float32)

    # --- 負債比 QoQ 變化 ---
    bs_df["debt_ratio_trend"] = bs_df["debt_ratio"].diff().astype(np.float32)

    # --- ROE QoQ 變化 ---
    bs_df["roe_trend"] = bs_df["roe_annualized"].diff().astype(np.float32)

    # 重新排序按 available_date
    bs_df = bs_df.sort_values("available_date").reset_index(drop=True)

    # point-in-time merge
    feature_cols = [
        "available_date",
        "debt_ratio",
        "current_ratio",
        "roe_annualized",
        "roa_annualized",
        "book_value_per_share",
        "equity_ratio",
        "debt_ratio_trend",
        "roe_trend",
    ]
    bs_features = bs_df[feature_cols].copy()

    out = out.sort_values("Date").reset_index(drop=True)
    bs_features = bs_features.sort_values("available_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        bs_features,
        left_on="Date",
        right_on="available_date",
        direction="backward",
    )

    out.drop(columns=["available_date"], inplace=True, errors="ignore")
    return out


# 本模組產出的特徵欄位名稱
BALANCE_SHEET_FEATURE_COLS = [
    "debt_ratio",
    "current_ratio",
    "roe_annualized",
    "roa_annualized",
    "book_value_per_share",
    "equity_ratio",
    "debt_ratio_trend",
    "roe_trend",
]
