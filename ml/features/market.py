"""大盤指數／市場環境特徵工程模組

從大盤指數資料（加權指數、VIX、費半、美元台幣）
衍生市場環境特徵，並合併至個股日K資料。
"""

import os
import pandas as pd
import numpy as np

# 預先計算好的市場特徵 DataFrame 快取
_MARKET_FEATURES_CACHE: dict = {}


def _load_index(index_dir: str, filename: str) -> pd.DataFrame | None:
    """載入單一指數 CSV。"""
    fpath = os.path.join(index_dir, filename)
    if not os.path.exists(fpath):
        return None
    try:
        idx = pd.read_csv(fpath, parse_dates=["Date"])
        idx = idx[["Date", "Close"]].dropna(subset=["Close"])
        idx = idx.sort_values("Date").reset_index(drop=True)
        return idx
    except Exception:
        return None


def _build_market_features(index_dir: str) -> pd.DataFrame:
    """一次性建構市場特徵 DataFrame（所有指數合併）"""
    if index_dir in _MARKET_FEATURES_CACHE:
        return _MARKET_FEATURES_CACHE[index_dir]

    features = None

    # TWII 加權指數
    twii = _load_index(index_dir, "index_TWII.csv")
    if twii is not None:
        twii = twii.copy()
        twii["twii_return_5d"] = twii["Close"].pct_change(5).astype(np.float32)
        twii["twii_return_20d"] = twii["Close"].pct_change(20).astype(np.float32)
        features = twii[["Date", "twii_return_5d", "twii_return_20d"]].copy()

    # VIX
    vix = _load_index(index_dir, "index_VIX.csv")
    if vix is not None:
        vix = vix.copy()
        vix["vix_level"] = vix["Close"].astype(np.float32)
        vix["vix_change_5d"] = vix["Close"].pct_change(5).astype(np.float32)
        vix_feat = vix[["Date", "vix_level", "vix_change_5d"]].copy()
        if features is not None:
            features = pd.merge_asof(
                features.sort_values("Date"),
                vix_feat.sort_values("Date"),
                on="Date", direction="backward",
            )
        else:
            features = vix_feat

    # SOX 費城半導體
    sox = _load_index(index_dir, "index_SOX.csv")
    if sox is not None:
        sox = sox.copy()
        sox["sox_return_5d"] = sox["Close"].pct_change(5).astype(np.float32)
        sox_feat = sox[["Date", "sox_return_5d"]].copy()
        if features is not None:
            features = pd.merge_asof(
                features.sort_values("Date"),
                sox_feat.sort_values("Date"),
                on="Date", direction="backward",
            )
        else:
            features = sox_feat

    # USD/TWD
    usdtwd = _load_index(index_dir, "index_USDTWDX.csv")
    if usdtwd is not None:
        usdtwd = usdtwd.copy()
        usdtwd["usdtwd_change_5d"] = usdtwd["Close"].pct_change(5).astype(np.float32)
        usd_feat = usdtwd[["Date", "usdtwd_change_5d"]].copy()
        if features is not None:
            features = pd.merge_asof(
                features.sort_values("Date"),
                usd_feat.sort_values("Date"),
                on="Date", direction="backward",
            )
        else:
            features = usd_feat

    if features is None:
        features = pd.DataFrame(columns=["Date"] + MARKET_FEATURE_COLS)

    _MARKET_FEATURES_CACHE[index_dir] = features
    return features


def compute_market_features(
    daily_df: pd.DataFrame,
    index_dir: str,
) -> pd.DataFrame:
    """從大盤指數計算市場環境特徵並合併至日K。"""
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])
    out = out.sort_values("Date").reset_index(drop=True)

    market_feat = _build_market_features(index_dir)

    if market_feat.empty:
        for col in MARKET_FEATURE_COLS:
            out[col] = np.nan
        return out

    out = pd.merge_asof(
        out,
        market_feat.sort_values("Date"),
        on="Date",
        direction="backward",
    )

    # 補齊可能缺少的欄位
    for col in MARKET_FEATURE_COLS:
        if col not in out.columns:
            out[col] = np.nan

    return out


# 本模組產出的特徵欄位名稱
MARKET_FEATURE_COLS = [
    "twii_return_5d",
    "twii_return_20d",
    "vix_level",
    "vix_change_5d",
    "sox_return_5d",
    "usdtwd_change_5d",
]
