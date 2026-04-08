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

    def _merge_feat(base, new_feat):
        if base is None:
            return new_feat
        return pd.merge_asof(
            base.sort_values("Date"),
            new_feat.sort_values("Date"),
            on="Date", direction="backward",
        )

    # TWII 加權指數
    twii = _load_index(index_dir, "index_TWII.csv")
    if twii is not None:
        twii = twii.copy()
        c = pd.to_numeric(twii["Close"], errors="coerce")
        twii["twii_return_5d"] = c.pct_change(5).astype(np.float32)
        twii["twii_return_20d"] = c.pct_change(20).astype(np.float32)
        ma5 = c.rolling(5, min_periods=3).mean()
        ma20 = c.rolling(20, min_periods=10).mean()
        ma60 = c.rolling(60, min_periods=30).mean()
        twii["twii_above_ma5"] = (c > ma5).astype(np.float32)
        twii["twii_above_ma20"] = (c > ma20).astype(np.float32)
        twii["twii_above_ma60"] = (c > ma60).astype(np.float32)
        twii["twii_ma5_slope"] = (ma5.pct_change(3) * 100).clip(-5, 5).astype(np.float32)
        twii["twii_ma20_slope"] = (ma20.pct_change(5) * 100).clip(-5, 5).astype(np.float32)
        twii["twii_volatility_20d"] = c.pct_change().rolling(20, min_periods=10).std().astype(np.float32)
        twii_cols = [col for col in twii.columns if col.startswith("twii_")]
        features = twii[["Date"] + twii_cols].copy()

    # VIX
    vix = _load_index(index_dir, "index_VIX.csv")
    if vix is not None:
        vix = vix.copy()
        c = pd.to_numeric(vix["Close"], errors="coerce")
        # 用 60 日滾動百分位取代絕對值，避免 regime-specific overfitting
        vix["vix_percentile_60d"] = c.rolling(60, min_periods=20).apply(
            lambda x: (x[-1] >= x[:-1]).mean() if len(x) > 1 else 0.5, raw=True
        ).astype(np.float32)
        vix["vix_change_5d"] = c.pct_change(5).astype(np.float32)
        vix_ma20 = c.rolling(20, min_periods=10).mean()
        vix["vix_ma20_ratio"] = (c / vix_ma20.replace(0, np.nan)).clip(0.5, 2.0).astype(np.float32)
        vix_feat = vix[["Date", "vix_percentile_60d", "vix_change_5d", "vix_ma20_ratio"]].copy()
        features = _merge_feat(features, vix_feat)

    # SOX 費城半導體
    sox = _load_index(index_dir, "index_SOX.csv")
    if sox is not None:
        sox = sox.copy()
        c = pd.to_numeric(sox["Close"], errors="coerce")
        sox["sox_return_5d"] = c.pct_change(5).astype(np.float32)
        ma20 = c.rolling(20, min_periods=10).mean()
        sox["sox_above_ma20"] = (c > ma20).astype(np.float32)
        sox_feat = sox[["Date", "sox_return_5d", "sox_above_ma20"]].copy()
        features = _merge_feat(features, sox_feat)

    # GSPC S&P 500
    gspc = _load_index(index_dir, "index_GSPC.csv")
    if gspc is not None:
        gspc = gspc.copy()
        c = pd.to_numeric(gspc["Close"], errors="coerce")
        gspc["gspc_return_5d"] = c.pct_change(5).astype(np.float32)
        ma20 = c.rolling(20, min_periods=10).mean()
        gspc["gspc_above_ma20"] = (c > ma20).astype(np.float32)
        gspc_feat = gspc[["Date", "gspc_return_5d", "gspc_above_ma20"]].copy()
        features = _merge_feat(features, gspc_feat)

    # USD/TWD
    usdtwd = _load_index(index_dir, "index_USDTWDX.csv")
    if usdtwd is not None:
        usdtwd = usdtwd.copy()
        usdtwd["usdtwd_change_5d"] = usdtwd["Close"].pct_change(5).astype(np.float32)
        usd_feat = usdtwd[["Date", "usdtwd_change_5d"]].copy()
        features = _merge_feat(features, usd_feat)

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
    # V3: Target 已改超額報酬，移除大盤/國際指數的絕對報酬特徵
    # 原 twii_return_5d, twii_return_20d, sox_return_5d, gspc_return_5d,
    # vix_change_5d, usdtwd_change_5d 都是 beta 信號，預測 alpha 時無用
    # 只保留體制型（二元/相對/百分位）特徵
    "vix_percentile_60d",   # 相對恐慌度（百分位）
    "twii_above_ma5",       # 短期趨勢方向（二元）
    "twii_above_ma20",      # 中期趨勢方向（二元）
    "twii_above_ma60",      # 長期趨勢方向（二元）
    "twii_ma5_slope",       # 趨勢斜率（方向性，非報酬）
    "twii_ma20_slope",      # 趨勢斜率
    "twii_volatility_20d",  # 波動度體制
    "vix_ma20_ratio",       # VIX 相對自身均值（比率）
    "gspc_above_ma20",      # 美股趨勢（二元）
    "sox_above_ma20",       # 費半趨勢（二元）
]
