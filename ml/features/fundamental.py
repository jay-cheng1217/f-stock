"""基本面（季報財務）特徵工程模組

從季報財務資料衍生毛利率、營益率、淨利率等基本面特徵，
並透過 point-in-time join 對齊至日K資料時間軸。

季報公布時程 (保守估計):
  Q1 → 5/15,  Q2 → 8/14,  Q3 → 11/14,  Q4 → 隔年 3/31
"""

import os
import pandas as pd
import numpy as np


# 季報公布日 (保守 lag)
_QUARTER_AVAILABLE = {
    1: (0, 5, 15),   # Q1 → 同年 5/15
    2: (0, 8, 14),   # Q2 → 同年 8/14
    3: (0, 11, 14),  # Q3 → 同年 11/14
    4: (1, 3, 31),   # Q4 → 隔年 3/31
}

# 季報資料快取
_FINANCIAL_CACHE: dict = {}


def _parse_quarter_file(filepath: str) -> tuple:
    """解析季報檔名取得年份與季度。

    Returns (year, season) or None.
    """
    basename = os.path.basename(filepath)  # financial_2025Q3.csv
    name = basename.replace("financial_", "").replace(".csv", "")
    try:
        year = int(name[:4])
        season = int(name[5])
        return year, season
    except (ValueError, IndexError):
        return None


def _get_available_date(year: int, season: int) -> pd.Timestamp:
    """根據年份與季度回傳保守可用日期。"""
    year_offset, month, day = _QUARTER_AVAILABLE[season]
    return pd.Timestamp(year=year + year_offset, month=month, day=day)


def compute_fundamental_features(
    daily_df: pd.DataFrame,
    ticker: str,
    financial_dir: str,
) -> pd.DataFrame:
    """從季報財務資料計算基本面特徵並合併至日K。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位。
    ticker : str
        股票代碼 (如 '2330')。
    financial_dir : str
        季報財務目錄路徑 (如 F:\\stock\\季報財務)。

    Returns
    -------
    pd.DataFrame
        daily_df + 基本面特徵欄位
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    # 載入並快取季報檔案
    if financial_dir not in _FINANCIAL_CACHE:
        cache = {}
        for fname in os.listdir(financial_dir):
            if not fname.startswith("financial_") or not fname.endswith(".csv"):
                continue
            parsed = _parse_quarter_file(fname)
            if parsed is None:
                continue
            fpath = os.path.join(financial_dir, fname)
            try:
                cache[parsed] = pd.read_csv(fpath, dtype={"Ticker": str})
            except Exception:
                continue
        _FINANCIAL_CACHE[financial_dir] = cache

    records = []
    for (year, season), qdf in _FINANCIAL_CACHE[financial_dir].items():
        row = qdf[qdf["Ticker"] == str(ticker)]
        if row.empty:
            continue
        row = row.iloc[0]
        records.append({
            "year": year,
            "season": season,
            "available_date": _get_available_date(year, season),
            "gross_margin_latest": row.get("Gross_Margin_Pct", np.nan),
            "operating_margin_latest": row.get("Operating_Margin_Pct", np.nan),
            "net_margin_latest": row.get("Net_Margin_Pct", np.nan),
            "pretax_margin_latest": row.get("Pretax_Margin_Pct", np.nan),
            "revenue_m": row.get("Revenue_M", np.nan),
        })

    if not records:
        # 無季報資料，填 NaN
        for col in FUNDAMENTAL_FEATURE_COLS:
            out[col] = np.nan
        return out

    fund = pd.DataFrame(records)
    fund = fund.sort_values(["year", "season"]).reset_index(drop=True)

    # 轉 float32
    for col in ["gross_margin_latest", "operating_margin_latest",
                 "net_margin_latest", "pretax_margin_latest", "revenue_m"]:
        fund[col] = pd.to_numeric(fund[col], errors="coerce").astype(np.float32)

    # 原始資料為百分比數值 (33.36 = 33.36%)，統一轉小數 (0.3336)
    for col in ["gross_margin_latest", "operating_margin_latest",
                 "net_margin_latest", "pretax_margin_latest"]:
        fund[col] = fund[col] / 100.0

    # --- 營益率 QoQ 變化 ---
    fund["margin_trend"] = fund["operating_margin_latest"].diff().astype(np.float32)

    # --- 毛利率 QoQ 變化 ---
    fund["gross_margin_trend"] = fund["gross_margin_latest"].diff().astype(np.float32)

    # --- 營收季增率 (QoQ) ---
    prev_rev = fund["revenue_m"].shift(1)
    fund["revenue_qoq"] = np.where(
        prev_rev.abs() > 0.01,
        (fund["revenue_m"] - prev_rev) / prev_rev.abs(),
        np.nan,
    ).astype(np.float32)

    # --- 營收 YoY (與去年同季比) ---
    rev_yoy = []
    for i in range(len(fund)):
        curr = fund.iloc[i]
        prev = fund[(fund["year"] == curr["year"] - 1) & (fund["season"] == curr["season"])]
        if not prev.empty:
            p = prev.iloc[0]["revenue_m"]
            c = curr["revenue_m"]
            if pd.notna(p) and pd.notna(c) and abs(p) > 0.01:
                rev_yoy.append((c - p) / abs(p))
            else:
                rev_yoy.append(np.nan)
        else:
            rev_yoy.append(np.nan)
    fund["revenue_yoy_q"] = np.array(rev_yoy, dtype=np.float32)

    # --- 毛利率 vs 營益率差距 (管銷費用佔比) ---
    fund["margin_spread"] = (
        fund["gross_margin_latest"] - fund["operating_margin_latest"]
    ).astype(np.float32)

    # --- 稅前 vs 淨利差 (稅率/業外影響) ---
    fund["tax_effect"] = (
        fund["pretax_margin_latest"] - fund["net_margin_latest"]
    ).astype(np.float32)

    # --- 營益率 YoY 變化 ---
    op_margin_yoy = []
    for i in range(len(fund)):
        curr = fund.iloc[i]
        prev = fund[(fund["year"] == curr["year"] - 1) & (fund["season"] == curr["season"])]
        if not prev.empty:
            op_margin_yoy.append(curr["operating_margin_latest"] - prev.iloc[0]["operating_margin_latest"])
        else:
            op_margin_yoy.append(np.nan)
    fund["margin_yoy_change"] = np.array(op_margin_yoy, dtype=np.float32)

    # --- 營收規模 log ---
    fund["revenue_log_scale"] = np.log1p(fund["revenue_m"].clip(lower=0)).astype(np.float32)

    # 重新排序按 available_date
    fund = fund.sort_values("available_date").reset_index(drop=True)

    # point-in-time merge
    feature_cols = [
        "available_date",
        "gross_margin_latest",
        "operating_margin_latest",
        "net_margin_latest",
        "margin_trend",
        "gross_margin_trend",
        "revenue_qoq",
        "revenue_yoy_q",
        "margin_spread",
        "tax_effect",
        "margin_yoy_change",
        "revenue_log_scale",
    ]
    fund_features = fund[feature_cols].copy()

    out = out.sort_values("Date").reset_index(drop=True)
    fund_features = fund_features.sort_values("available_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        fund_features,
        left_on="Date",
        right_on="available_date",
        direction="backward",
    )

    out.drop(columns=["available_date"], inplace=True, errors="ignore")

    return out


# 本模組產出的特徵欄位名稱
FUNDAMENTAL_FEATURE_COLS = [
    "gross_margin_latest",
    "operating_margin_latest",
    "net_margin_latest",
    "margin_trend",
    "gross_margin_trend",
    "revenue_qoq",
    "revenue_yoy_q",
    "margin_spread",
    "tax_effect",
    "margin_yoy_change",
    "revenue_log_scale",
]
