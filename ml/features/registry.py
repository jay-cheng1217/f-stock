"""特徵註冊中心

管理所有特徵群組的元資料，提供特徵可用性檢查
與欄位名稱查詢功能。
"""

import os
from typing import Dict, List

from ml.config import (
    DAILY_K_DIR,
    REVENUE_DIR,
    FINANCIAL_DIR,
    INDEX_DIR,
    VALUATION_DIR,
    REGIME_FEATURE_COLS,
)
from ml.features.technical import TECHNICAL_FEATURE_COLS
from ml.features.institutional import INSTITUTIONAL_FEATURE_COLS
from ml.features.revenue import REVENUE_FEATURE_COLS
from ml.features.fundamental import FUNDAMENTAL_FEATURE_COLS
from ml.features.market import MARKET_FEATURE_COLS
from ml.features.valuation import VALUATION_FEATURE_COLS
from ml.features.eps import EPS_FEATURE_COLS
from ml.features.sentiment import SENTIMENT_FEATURE_COLS
from ml.features.sector import SECTOR_FEATURE_COLS
from ml.features.tdcc import TDCC_FEATURE_COLS
from ml.features.news import NEWS_FEATURE_COLS
from ml.features.balance_sheet import BALANCE_SHEET_FEATURE_COLS
from ml.features.entry import ENTRY_FEATURE_COLS
from ml.features.industry import INDUSTRY_FEATURE_COLS


# --- 特徵群組定義 ---
FEATURE_REGISTRY: Dict[str, dict] = {
    "technical": {
        "columns": TECHNICAL_FEATURE_COLS,
        "group": "technical",
        "requires": [DAILY_K_DIR],
        "description": "技術面特徵：均線距離、布林、動量、波動度等",
    },
    "institutional": {
        "columns": INSTITUTIONAL_FEATURE_COLS,
        "group": "institutional",
        "requires": [DAILY_K_DIR],
        "description": "籌碼面特徵：三大法人累積買賣超、融資融券",
    },
    "revenue": {
        "columns": REVENUE_FEATURE_COLS,
        "group": "revenue",
        "requires": [REVENUE_DIR],
        "description": "月營收特徵：年增率、動能、累計年增率",
    },
    "fundamental": {
        "columns": FUNDAMENTAL_FEATURE_COLS,
        "group": "fundamental",
        "requires": [FINANCIAL_DIR],
        "description": "基本面特徵：毛利率、營益率、淨利率、趨勢",
    },
    "market": {
        "columns": MARKET_FEATURE_COLS,
        "group": "market",
        "requires": [INDEX_DIR],
        "description": "大盤環境特徵：加權指數、VIX、費半、匯率",
    },
    "market_regime": {
        "columns": REGIME_FEATURE_COLS,
        "group": "market_regime",
        "requires": [INDEX_DIR, DAILY_K_DIR],
        "description": "市場體制百分位特徵：TAIEX 趨勢與市場廣度，嚴格使用 T-1 資料",
    },
    "valuation": {
        "columns": VALUATION_FEATURE_COLS,
        "group": "valuation",
        "requires": [VALUATION_DIR],
        "description": "估值面特徵：PE、PB、殖利率、PE歷史百分位",
    },
    "eps": {
        "columns": EPS_FEATURE_COLS,
        "group": "eps",
        "requires": [FINANCIAL_DIR],
        "description": "EPS 特徵：每股盈餘、TTM、年增率、季增率、動能",
    },
    "sentiment": {
        "columns": SENTIMENT_FEATURE_COLS,
        "group": "sentiment",
        "requires": [DAILY_K_DIR],
        "description": "消息面代理特徵：異常量比、連漲跌、量價背離、K線形態",
    },
    "sector": {
        "columns": SECTOR_FEATURE_COLS,
        "group": "sector",
        "requires": [DAILY_K_DIR],
        "description": "產業類股特徵：同產業相對表現、產業動能、類股輪動",
    },
    "tdcc": {
        "columns": TDCC_FEATURE_COLS,
        "group": "tdcc",
        "requires": [os.path.join(DAILY_K_DIR, os.pardir, "集保分散")],
        "description": "集保分散特徵：大戶散戶持股比、籌碼集中度趨勢",
    },
    "news": {
        "columns": NEWS_FEATURE_COLS,
        "group": "news",
        "requires": [os.path.join(DAILY_K_DIR, os.pardir, "新聞資料")],
        "description": "消息面特徵：重大訊息公告頻率、異常公告量",
    },
    "balance_sheet": {
        "columns": BALANCE_SHEET_FEATURE_COLS,
        "group": "balance_sheet",
        "requires": [os.path.join(DAILY_K_DIR, os.pardir, "資產負債")],
        "description": "資產負債表特徵：ROE、ROA、負債比、流動比、每股淨值",
    },
    "entry": {
        "columns": ENTRY_FEATURE_COLS,
        "group": "entry",
        "requires": [DAILY_K_DIR],
        "description": "進場信號特徵：法人成本、量能密集帶、階段辨識、共振評分",
    },
    "industry": {
        "columns": INDUSTRY_FEATURE_COLS,
        "group": "industry",
        "requires": [DAILY_K_DIR],
        "description": "產業聚合先行特徵：產業資金流、營收動能、領頭羊效應、大戶群體動態",
    },
}


def _dir_has_data(dirpath: str) -> bool:
    """檢查目錄是否存在且含有 csv 檔案。"""
    if not os.path.isdir(dirpath):
        return False
    return any(f.endswith(".csv") for f in os.listdir(dirpath))


def get_available_features() -> Dict[str, dict]:
    """回傳目前資料目錄齊全的特徵群組。

    Returns
    -------
    dict
        key 為群組名稱，value 為該群組的元資料 dict。
        只回傳 requires 中所有資料目錄皆存在且有 CSV 的群組。
    """
    available = {}
    for name, meta in FEATURE_REGISTRY.items():
        if all(_dir_has_data(d) for d in meta["requires"]):
            available[name] = meta
    return available


def get_feature_columns(groups: List[str] | None = None) -> List[str]:
    """回傳指定群組（或所有可用群組）的特徵欄位名稱清單。

    Parameters
    ----------
    groups : list of str or None
        指定群組名稱清單。若為 None 則回傳所有可用群組的欄位。

    Returns
    -------
    list of str
        特徵欄位名稱清單（去重、保序）。
    """
    if groups is None:
        available = get_available_features()
        groups = list(available.keys())

    seen = set()
    columns = []
    for g in groups:
        meta = FEATURE_REGISTRY.get(g)
        if meta is None:
            continue
        for col in meta["columns"]:
            if col not in seen:
                seen.add(col)
                columns.append(col)
    return columns
