"""資料集組裝：合併特徵 + 目標、分割訓練/驗證/測試集"""
import glob
import os
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from ml.config import (
    DAILY_K_DIR, REVENUE_DIR, FINANCIAL_DIR, INDEX_DIR, VALUATION_DIR,
    MODEL_DIR,
    MIN_AVG_VOLUME, MIN_PRICE, MIN_HISTORY_DAYS,
    WALK_FORWARD_TRAIN_MONTHS, WALK_FORWARD_VAL_MONTHS,
    WALK_FORWARD_TEST_MONTHS,
)
from ml.target import compute_target
from ml.features.technical import compute_technical_features
from ml.features.institutional import compute_institutional_features
from ml.features.revenue import compute_revenue_features
from ml.features.fundamental import compute_fundamental_features
from ml.features.market import compute_market_features
from ml.features.valuation import compute_valuation_features
from ml.features.eps import compute_eps_features
from ml.features.sentiment import compute_sentiment_features
from ml.features.sector import compute_sector_features
from ml.features.tdcc import compute_tdcc_features
from ml.features.news import compute_news_features
from ml.features.balance_sheet import compute_balance_sheet_features
from ml.features.entry import compute_entry_features
from ml.features.registry import get_available_features, get_feature_columns


def _list_daily_tickers() -> list[str]:
    """列出本地日K資料中的股票代碼。"""
    return sorted(
        [
            os.path.splitext(f)[0]
            for f in os.listdir(DAILY_K_DIR)
            if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
        ]
    )


def diagnose_stock_eligibility(ticker: str) -> dict:
    """回傳單一股票是否納入 ML 預測 universe，以及排除原因。"""
    info = {
        "ticker": ticker,
        "eligible": False,
        "reason_code": "missing_kline",
        "message": f"找不到 {ticker} 的日K資料。",
        "history_days": 0,
        "min_history_days": int(MIN_HISTORY_DAYS),
        "avg_volume_60": None,
        "min_avg_volume": int(MIN_AVG_VOLUME),
        "last_close": None,
        "min_price": float(MIN_PRICE),
        "last_date": None,
    }

    kline_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(kline_path):
        return info

    df = pd.read_csv(kline_path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    info["history_days"] = int(len(df))
    if len(df) > 0:
        info["last_date"] = df["Date"].iloc[-1].strftime("%Y-%m-%d")
        info["last_close"] = float(df["Close"].iloc[-1])
        info["avg_volume_60"] = float(df["Volume"].tail(60).mean())

    if len(df) < MIN_HISTORY_DAYS:
        info["reason_code"] = "insufficient_history"
        info["message"] = (
            f"未納入 ML 預測，歷史資料僅 {len(df)} 天，"
            f"低於門檻 {MIN_HISTORY_DAYS} 天。"
        )
        return info

    avg_vol = float(df["Volume"].tail(60).mean())
    last_price = float(df["Close"].iloc[-1])

    if avg_vol < MIN_AVG_VOLUME:
        info["reason_code"] = "low_volume"
        info["message"] = (
            f"未納入 ML 預測，近60日平均成交量 {avg_vol:,.0f} 股，"
            f"低於門檻 {MIN_AVG_VOLUME:,.0f} 股。"
        )
        return info

    if last_price < MIN_PRICE:
        info["reason_code"] = "low_price"
        info["message"] = (
            f"未納入 ML 預測，最新收盤價 {last_price:.2f} 元，"
            f"低於門檻 {MIN_PRICE:.2f} 元。"
        )
        return info

    info["eligible"] = True
    info["reason_code"] = "eligible"
    info["message"] = "已納入 ML 預測範圍。"
    return info


def _find_ticker_in_quarter_files(ticker: str, directory: str, prefix: str) -> list[str]:
    """回傳某代碼出現在季度資料中的檔名標籤清單，最新排前面。"""
    if not os.path.isdir(directory):
        return []

    matches = []
    for fname in sorted(os.listdir(directory), reverse=True):
        if not fname.startswith(prefix) or not fname.endswith(".csv"):
            continue
        fpath = os.path.join(directory, fname)
        try:
            qdf = pd.read_csv(fpath, usecols=["Ticker"], dtype={"Ticker": str})
        except ValueError:
            try:
                qdf = pd.read_csv(fpath, dtype={"Ticker": str})
            except Exception:
                continue
        except Exception:
            continue

        if "Ticker" not in qdf.columns:
            continue
        if (qdf["Ticker"] == str(ticker)).any():
            matches.append(fname.replace(f"{prefix}_", "").replace(".csv", ""))
    return matches


def _find_recent_valuation_date(ticker: str, max_files: int = 120) -> tuple[str | None, int]:
    """回傳某代碼最近出現在估值資料的日期，並限制掃描最近 N 個交易日。"""
    if not os.path.isdir(VALUATION_DIR):
        return None, 0

    val_files = sorted(glob.glob(os.path.join(VALUATION_DIR, "valuation_*.csv")))
    if not val_files:
        return None, 0

    files_to_check = val_files[-max_files:]
    scanned = 0
    for fpath in reversed(files_to_check):
        scanned += 1
        try:
            vdf = pd.read_csv(fpath, usecols=["Ticker"], dtype={"Ticker": str})
        except ValueError:
            try:
                vdf = pd.read_csv(fpath, dtype={"Ticker": str})
            except Exception:
                continue
        except Exception:
            continue

        if "Ticker" not in vdf.columns:
            continue
        if (vdf["Ticker"] == str(ticker)).any():
            date_str = os.path.basename(fpath).replace("valuation_", "").replace(".csv", "")
            try:
                return pd.Timestamp(date_str).strftime("%Y-%m-%d"), scanned
            except Exception:
                return date_str, scanned

    return None, len(files_to_check)


def diagnose_stock_data_status(ticker: str) -> dict:
    """整理單一股票的 ML 納入條件與各資料來源覆蓋狀態。"""
    ticker = str(ticker)
    eligibility = diagnose_stock_eligibility(ticker)
    available_features = sorted(get_available_features().keys())
    sources = []

    if eligibility["last_date"] is not None:
        kline_detail = (
            f"最新 {eligibility['last_date']}，"
            f"{eligibility['history_days']} 天歷史，"
            f"近60日均量 {eligibility['avg_volume_60']:,.0f} 股"
        )
    else:
        kline_detail = "找不到日K資料"
    sources.append({
        "key": "kline",
        "label": "日K",
        "ok": eligibility["last_date"] is not None,
        "detail": kline_detail,
    })

    revenue_path = os.path.join(REVENUE_DIR, f"revenue_{ticker}.csv")
    if os.path.exists(revenue_path):
        try:
            rev_df = pd.read_csv(revenue_path, dtype={"Date": str})
            last_rev = str(rev_df["Date"].iloc[-1]) if len(rev_df) > 0 and "Date" in rev_df.columns else "-"
            revenue_detail = f"{len(rev_df)} 筆，最新 {last_rev}"
        except Exception:
            revenue_detail = "檔案存在，但讀取失敗"
        revenue_ok = True
    else:
        revenue_detail = f"找不到 revenue_{ticker}.csv"
        revenue_ok = False
    sources.append({
        "key": "revenue",
        "label": "月營收",
        "ok": revenue_ok,
        "detail": revenue_detail,
    })

    fundamental_matches = _find_ticker_in_quarter_files(ticker, FINANCIAL_DIR, "financial")
    sources.append({
        "key": "fundamental",
        "label": "季報財務",
        "ok": bool(fundamental_matches),
        "detail": (
            f"{len(fundamental_matches)} 季，最新 {fundamental_matches[0]}"
            if fundamental_matches else
            "在 financial_*.csv 中找不到該代碼"
        ),
    })

    eps_matches = _find_ticker_in_quarter_files(ticker, FINANCIAL_DIR, "eps")
    sources.append({
        "key": "eps",
        "label": "EPS",
        "ok": bool(eps_matches),
        "detail": (
            f"{len(eps_matches)} 季，最新 {eps_matches[0]}"
            if eps_matches else
            "在 eps_*.csv 中找不到該代碼"
        ),
    })

    valuation_date, valuation_scanned = _find_recent_valuation_date(ticker)
    sources.append({
        "key": "valuation",
        "label": "估值",
        "ok": valuation_date is not None,
        "detail": (
            f"最近估值資料 {valuation_date}"
            if valuation_date is not None else
            f"最近 {valuation_scanned} 個估值交易日未找到"
        ),
    })

    twii_df = _load_twii()
    market_ok = twii_df is not None and len(twii_df) > 0
    market_detail = (
        f"TWII 最新 {twii_df['Date'].iloc[-1].strftime('%Y-%m-%d')}"
        if market_ok else
        "找不到 index_TWII.csv"
    )
    sources.append({
        "key": "market",
        "label": "大盤指數",
        "ok": market_ok,
        "detail": market_detail,
    })

    sector_map_path = os.path.join(os.path.dirname(__file__), "data", "sector_mapping.csv")
    sector_ok = False
    sector_detail = "找不到產業對照表"
    if os.path.exists(sector_map_path):
        try:
            sector_df = pd.read_csv(sector_map_path, dtype={"Ticker": str, "ticker": str})
            ticker_col = "ticker" if "ticker" in sector_df.columns else "Ticker" if "Ticker" in sector_df.columns else None
            sector_col = "sector_name" if "sector_name" in sector_df.columns else "Sector" if "Sector" in sector_df.columns else None
            row = sector_df[sector_df[ticker_col] == ticker] if ticker_col else pd.DataFrame()
            if not row.empty:
                sector_ok = True
                sector_name = row.iloc[0].get(sector_col, "") if sector_col else ""
                sector_detail = f"已對應 {sector_name or '產業類別'}"
            else:
                sector_detail = "產業對照表中找不到該代碼"
        except Exception:
            sector_detail = "產業對照表存在，但讀取失敗"
    sources.append({
        "key": "sector",
        "label": "產業對照",
        "ok": sector_ok,
        "detail": sector_detail,
    })

    return {
        "ticker": ticker,
        "summary": eligibility["message"],
        "eligibility": eligibility,
        "sources": sources,
        "available_feature_groups": available_features,
        "available_source_count": sum(1 for s in sources if s["ok"]),
        "total_source_count": len(sources),
    }


def _load_issued_shares() -> dict[str, float]:
    """從外資持股目錄載入各股發行股數 (取最新一天的資料)。

    Returns dict {ticker: issued_shares_in_shares}
    """
    foreign_dir = os.path.join(DAILY_K_DIR, os.pardir, "外資持股")
    if not os.path.isdir(foreign_dir):
        return {}
    files = sorted(glob.glob(os.path.join(foreign_dir, "*.csv")))
    if not files:
        return {}
    try:
        df = pd.read_csv(files[-1], dtype={"Ticker": str})
        return dict(zip(df["Ticker"], df["Issued_Shares"]))
    except Exception:
        return {}


# 模組級快取，避免每支股票重複讀取
_ISSUED_SHARES_CACHE: dict[str, float] | None = None


def _get_issued_shares() -> dict[str, float]:
    global _ISSUED_SHARES_CACHE
    if _ISSUED_SHARES_CACHE is None:
        _ISSUED_SHARES_CACHE = _load_issued_shares()
    return _ISSUED_SHARES_CACHE


def load_single_stock(ticker: str, twii_df: pd.DataFrame = None) -> pd.DataFrame | None:
    """載入單支股票並計算所有可用特徵"""
    kline_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(kline_path):
        return None

    df = pd.read_csv(kline_path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    if len(df) < MIN_HISTORY_DAYS:
        return None

    # 流動性過濾
    avg_vol = df["Volume"].tail(60).mean()
    last_price = df["Close"].iloc[-1]
    if avg_vol < MIN_AVG_VOLUME or last_price < MIN_PRICE:
        return None

    # 計算目標（超額報酬）
    df = compute_target(df, twii_df=twii_df)

    # 計算特徵（依可用資料）
    available = get_available_features()

    if "technical" in available:
        df = compute_technical_features(df)
    if "institutional" in available:
        df = compute_institutional_features(df)

    if "revenue" in available:
        rev_path = os.path.join(REVENUE_DIR, f"revenue_{ticker}.csv")
        if os.path.exists(rev_path):
            rev_df = pd.read_csv(rev_path, dtype={"Date": str})
            df = compute_revenue_features(df, rev_df)

    if "fundamental" in available:
        df = compute_fundamental_features(df, ticker, FINANCIAL_DIR)

    if "market" in available:
        df = compute_market_features(df, INDEX_DIR)

    if "valuation" in available:
        df = compute_valuation_features(df, ticker, VALUATION_DIR)

    if "eps" in available:
        df = compute_eps_features(df, ticker, FINANCIAL_DIR)

    if "sentiment" in available:
        df = compute_sentiment_features(df)

    if "tdcc" in available:
        tdcc_path = os.path.join(DAILY_K_DIR, os.pardir, "集保分散", "tdcc_summary.csv")
        df = compute_tdcc_features(df, ticker, tdcc_path)

    if "news" in available:
        news_dir = os.path.join(DAILY_K_DIR, os.pardir, "新聞資料")
        df = compute_news_features(df, ticker, news_dir)

    if "balance_sheet" in available:
        bs_dir = os.path.join(DAILY_K_DIR, os.pardir, "資產負債")
        df = compute_balance_sheet_features(df, ticker, bs_dir, FINANCIAL_DIR)

    if "entry" in available:
        df = compute_entry_features(df)

    # --- 周轉率 (turnover_rate) ---
    issued = _get_issued_shares()
    shares = issued.get(ticker)
    if shares and shares > 0 and "Volume" in df.columns:
        # Volume 已是股數; 轉張數再算周轉率 (%)
        lots_outstanding = shares / 1000  # 發行張數
        df["turnover_rate"] = np.where(
            lots_outstanding > 0,
            ((df["Volume"] / 1000) / lots_outstanding * 100).astype(np.float32),
            np.nan,
        )
    else:
        df["turnover_rate"] = np.nan

    df["ticker"] = ticker
    return df


def _load_twii() -> pd.DataFrame:
    """載入加權指數資料供超額報酬計算"""
    twii_path = os.path.join(INDEX_DIR, "index_TWII.csv")
    if not os.path.exists(twii_path):
        return None
    df = pd.read_csv(twii_path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df[["Date", "Close"]].rename(columns={"Close": "twii_close"})


def _apply_percentile_ranking(dataset: pd.DataFrame) -> pd.DataFrame:
    """將法人籌碼絕對值轉為每日截面百分位排名 (0~1)

    跨股票的絕對買賣超無法比較（台積 vs 小型股），
    轉為同日所有股票中的百分位可讓模型學到相對強弱。
    """
    from ml.features.institutional import INSTITUTIONAL_FEATURE_COLS

    # chip_diverge 是跨股票可比的連續分數，不做百分位轉換
    _SKIP_RANK = {"chip_diverge_bear", "chip_diverge_bull"}
    rank_cols = [c for c in INSTITUTIONAL_FEATURE_COLS
                 if c in dataset.columns and "change" not in c
                 and "ratio" not in c and c not in _SKIP_RANK]

    for col in rank_cols:
        dataset[col] = dataset.groupby("Date")[col].rank(pct=True).astype(np.float32)

    return dataset


# --- 極端值截斷規則 ---
# 第一類：絕對門檻截斷 (財務比率、估值、報酬率等有明確商業邊界的特徵)
# key=欄位名, value=(下限, 上限)
_WINSORIZE_ABSOLUTE: dict[str, tuple[float | None, float | None]] = {
    # EPS 年增率：低基期(>+500%) / 高基期(<-100%) 皆為雜訊
    "eps_yoy":              (-1.0, 5.0),
    "eps_qoq":              (-1.0, 5.0),
    "eps_momentum":         (-2.0, 2.0),
    # 月營收年增率
    "revenue_yoy":          (-1.0, 5.0),
    "revenue_yoy_3m":       (-1.0, 5.0),
    "revenue_acc_yoy":      (-1.0, 5.0),
    "revenue_momentum":     (-2.0, 2.0),
    # 基本面利潤率
    "operating_margin_latest": (-1.0, 1.0),
    "net_margin_latest":       (-1.0, 1.0),
    "gross_margin_latest":     (-0.5, 1.0),
    "margin_trend":            (-0.5, 0.5),
    # PB
    "pb_ratio":             (0.0, 50.0),
    # 殖利率 (API 回傳百分比形式，正常台股 0~30%)
    "dividend_yield":       (0.0, 30.0),
    # 融資券比：分母趨近零時失去意義
    "margin_short_ratio":   (0.0, 100.0),
    # 報酬率截斷：防止極端事件扭曲分裂節點
    "return_1d":            (-0.20, 0.20),
    "return_3d":            (-0.30, 0.30),
    "return_5d":            (-0.40, 0.40),
    "return_10d":           (-0.50, 0.50),
    "return_20d":           (-0.60, 0.60),
    "return_60d":           (-0.80, 0.80),
}

# 第二類：分位數截斷 (技術指標等無明確商業邊界的特徵，頭尾 1% 截斷)
_WINSORIZE_QUANTILE_COLS = [
    "vol_ratio_5_20", "vol_zscore", "cmf_20", "obv_slope_20",
    "force_index_13", "price_vol_diverge",
    "atr_pct",  # ATR% (Step 2 新增)
    "turnover_rate",  # 周轉率 (Step 4 新增)
]


def _winsorize_features(df: pd.DataFrame) -> pd.DataFrame:
    """對指定特徵做極端值截斷 (Winsorization)。

    兩種策略：
    1. 絕對門檻截斷：財務比率等有明確商業邊界
    2. 分位數截斷 (1%/99%)：技術指標等分佈不固定

    NaN 保持不變（LightGBM 原生處理）。
    """
    # --- 絕對門檻截斷 ---
    for col, (lo, hi) in _WINSORIZE_ABSOLUTE.items():
        if col not in df.columns:
            continue
        df[col] = df[col].clip(lower=lo, upper=hi)

    # --- PE 特殊處理：虧損公司 PE<0 無意義，統一設上限 ---
    if "pe_ratio" in df.columns:
        df["pe_ratio"] = np.where(
            df["pe_ratio"] < 0, 500.0, df["pe_ratio"]
        )
        df["pe_ratio"] = df["pe_ratio"].clip(upper=500.0)

    # --- 分位數截斷 (1% / 99%) ---
    for col in _WINSORIZE_QUANTILE_COLS:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) < 100:
            continue
        lo = s.quantile(0.01)
        hi = s.quantile(0.99)
        df[col] = df[col].clip(lower=lo, upper=hi)

    return df


# Binary/event features that should NOT be z-scored
_ZSCORE_SKIP = {
    # Binary flags (0/1)
    "ma5_bounce", "ma20_bounce", "ma5_support_test", "ma20_support_test",
    "ma_bullish_align", "ma_golden_cross", "ma_death_cross",
    "macd_turn_positive", "kd_golden_cross", "rsi_oversold_bounce",
    "bullish_engulf", "bearish_engulf",
    "doji", "hammer", "hanging_man", "shooting_star",
    "morning_star", "evening_star",
    "three_white_soldiers", "three_black_crows",
    "donchian_breakout_up", "donchian_breakout_dn",
    "squeeze",
    "macd_bearish_div", "macd_bullish_div",
    "rsi_bearish_div", "rsi_bullish_div",
    "elder_bull", "elder_bear",
    "price_vol_diverge",
    # Already percentile-ranked
    "atr_pct_rank",
    # Binary market regime
    "twii_above_ma5", "twii_above_ma20", "twii_above_ma60",
    "gspc_above_ma20", "sox_above_ma20",
    # Percentile features
    "vix_percentile_60d",
    # Chip divergence scores (already cross-sectionally comparable)
    "chip_diverge_bear", "chip_diverge_bull",
}


def _apply_cross_sectional_zscore(dataset: pd.DataFrame) -> pd.DataFrame:
    """對連續特徵做每日截面 z-score 標準化。

    z = (x - 當日均值) / 當日標準差

    讓模型看到「這支股票今天相對全市場的位置」而非絕對數值。
    好處：
    - 消除不同時期的絕對水位差異（2024 和 2026 的 PE 不可比）
    - 強迫模型學截面選股能力，而非時序擇時
    - LightGBM 分裂點自動適應 z-score 尺度
    """
    feature_cols = get_feature_columns()
    # 只 z-score 連續特徵（排除二元/事件/已排名）
    cols_to_zscore = [c for c in feature_cols if c in dataset.columns and c not in _ZSCORE_SKIP]

    if not cols_to_zscore:
        return dataset

    grouped = dataset.groupby("Date")[cols_to_zscore]
    means = grouped.transform("mean")
    stds = grouped.transform("std")
    # 避免除以零（只有一支股票的日子，std=0）
    stds = stds.replace(0, np.nan)
    dataset[cols_to_zscore] = ((dataset[cols_to_zscore] - means) / stds).astype(np.float32)

    return dataset


def _get_cache_path():
    """快取檔案路徑（當日有效）"""
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    return os.path.join(MODEL_DIR, f"dataset_cache_{today}.parquet")


def _save_raw_cache(dataset: pd.DataFrame, verbose: bool = True):
    """儲存 winsorize 前的原始資料集（供 v2 重用，省去重新載入）"""
    cache_path = _get_cache_path()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    # 清理舊快取（只保留當日）
    for old in glob.glob(os.path.join(MODEL_DIR, "dataset_cache_*.parquet")):
        if old != cache_path:
            os.remove(old)
    dataset.to_parquet(cache_path, index=False)
    if verbose:
        size_mb = os.path.getsize(cache_path) / 1024 / 1024
        print(f"  快取已儲存: {cache_path} ({size_mb:.0f} MB)")


def load_raw_cache(verbose: bool = True):
    """載入當日快取（percentile ranking + sector 後、winsorize 前）。

    Returns None 如果快取不存在、已過期、或底層資料已更新。
    """
    cache_path = _get_cache_path()
    if not os.path.exists(cache_path):
        return None

    cache_mtime = os.path.getmtime(cache_path)

    # 檢查估值資料是否比 cache 更新（防止 cache 用舊的錯誤資料）
    val_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "估值資料")
    if os.path.isdir(val_dir):
        val_files = glob.glob(os.path.join(val_dir, "valuation_*.csv"))
        if val_files:
            latest_val_mtime = max(os.path.getmtime(f) for f in val_files)
            if latest_val_mtime > cache_mtime:
                if verbose:
                    print("  快取已過期（估值資料已更新），將重新建構...")
                os.remove(cache_path)
                return None

    if verbose:
        size_mb = os.path.getsize(cache_path) / 1024 / 1024
        print(f"  載入快取: {cache_path} ({size_mb:.0f} MB)")
    df = pd.read_parquet(cache_path)
    return df


def build_dataset(max_stocks: int = 0, verbose: bool = True) -> pd.DataFrame:
    """組裝完整資料集：所有股票合併

    Args:
        max_stocks: 限制股票數（0=全部，用於測試）
        verbose: 顯示進度條
    Returns:
        合併後的 DataFrame，含 ticker, Date, 所有特徵, target
    """
    # 嘗試載入快取（跳過逐檔載入 + 特徵計算）
    cached = load_raw_cache(verbose=verbose) if max_stocks == 0 else None
    if cached is not None:
        dataset = cached
        if verbose:
            print(f"  從快取載入: {len(dataset):,} 行, {dataset['ticker'].nunique()} 支股票")
    else:
        tickers = _list_daily_tickers()
        if max_stocks > 0:
            tickers = tickers[:max_stocks]

        # 載入 TWII 供超額報酬計算
        twii_df = _load_twii()

        frames = []
        iterator = tqdm(tickers, desc="組裝資料集") if verbose else tickers
        for ticker in iterator:
            df = load_single_stock(ticker, twii_df=twii_df)
            if df is not None and len(df) > 0:
                frames.append(df)

        if not frames:
            raise ValueError("無可用資料")

        dataset = pd.concat(frames, ignore_index=True)
        # 移除無目標的行
        dataset = dataset.dropna(subset=["target"])
        dataset["target"] = dataset["target"].astype(int)

        # === 後處理：需要全體股票資料的特徵 ===

        # 1. 法人籌碼百分位排名
        if verbose:
            print("  後處理: 法人籌碼百分位排名...")
        dataset = _apply_percentile_ranking(dataset)

        # 2. 產業類股特徵
        available = get_available_features()
        if "sector" in available:
            if verbose:
                print("  後處理: 產業類股特徵...")
            dataset = compute_sector_features(dataset)

        # 2b. atr_pct 截面百分位排名（同一天跨股票排序，削弱波動度絕對值的宰制力）
        if "atr_pct" in dataset.columns:
            if verbose:
                print("  後處理: atr_pct 截面百分位排名...")
            dataset["atr_pct_rank"] = dataset.groupby("Date")["atr_pct"].rank(pct=True).astype(np.float32)
        else:
            dataset["atr_pct_rank"] = np.nan

        # 3. 儲存快取（winsorize 前，供 v2 重用）
        if max_stocks == 0:
            _save_raw_cache(dataset, verbose=verbose)

    # 3. 極端值截斷 (Winsorization)
    if verbose:
        print("  後處理: 極端值截斷 (Winsorization)...")
    dataset = _winsorize_features(dataset)

    # 3b. Cross-sectional z-score: 每日截面標準化連續特徵
    #     讓模型看到的是「相對全市場的排名/位置」而非絕對數值
    if verbose:
        print("  後處理: 截面 z-score 標準化...")
    dataset = _apply_cross_sectional_zscore(dataset)

    # 4. 降低記憶體：float64 → float32（ML 訓練不需要 float64 精度）
    float64_cols = dataset.select_dtypes(include=["float64"]).columns
    if len(float64_cols) > 0:
        dataset[float64_cols] = dataset[float64_cols].astype(np.float32)
        if verbose:
            print(f"  記憶體優化: {len(float64_cols)} 個 float64 欄位已降為 float32")

    if verbose:
        print(f"  資料集: {len(dataset):,} 行, {dataset['ticker'].nunique()} 支股票")
        print(f"  特徵數: {len(get_feature_columns())} 個")
        print(f"  類別分佈: {dict(dataset['target'].value_counts().sort_index())}")

    return dataset


def build_latest_snapshot(max_stocks: int = 0, verbose: bool = True) -> pd.DataFrame:
    """建立最新推論快照：每檔股票只保留最新一列，並套用截面特徵。"""
    tickers = _list_daily_tickers()
    if max_stocks > 0:
        tickers = tickers[:max_stocks]

    twii_df = _load_twii()
    rows = []
    iterator = tqdm(tickers, desc="建立最新快照") if verbose else tickers
    for ticker in iterator:
        df = load_single_stock(ticker, twii_df=twii_df)
        if df is None or len(df) == 0:
            continue
        rows.append(df.iloc[[-1]].copy())

    if not rows:
        raise ValueError("無可用推論資料")

    snapshot = pd.concat(rows, ignore_index=True)
    snapshot = snapshot.sort_values(["Date", "ticker"]).reset_index(drop=True)

    if verbose:
        print("  後處理: 法人籌碼百分位排名...")
    snapshot = _apply_percentile_ranking(snapshot)

    available = get_available_features()
    if "sector" in available:
        if verbose:
            print("  後處理: 產業類股特徵...")
        snapshot = compute_sector_features(snapshot)

    # atr_pct 截面百分位（snapshot 只有一天，直接 rank）
    if "atr_pct" in snapshot.columns:
        snapshot["atr_pct_rank"] = snapshot["atr_pct"].rank(pct=True).astype(np.float32)
    else:
        snapshot["atr_pct_rank"] = np.nan

    if verbose:
        print("  後處理: 極端值截斷 (Winsorization)...")
    snapshot = _winsorize_features(snapshot)

    return snapshot


def walk_forward_split(dataset: pd.DataFrame):
    """Walk-forward 分割產生器

    Yields:
        (fold_idx, train_df, val_df, test_df, test_end_date)
    """
    dates = sorted(dataset["Date"].unique())
    min_date = pd.Timestamp(dates[0])
    max_date = pd.Timestamp(dates[-1])

    # 第一個測試窗口起點：訓練+驗證之後
    train_months = WALK_FORWARD_TRAIN_MONTHS
    val_months = WALK_FORWARD_VAL_MONTHS
    test_months = WALK_FORWARD_TEST_MONTHS

    test_start = min_date + relativedelta(months=train_months + val_months)
    fold = 0

    while test_start < max_date:
        test_end = test_start + relativedelta(months=test_months)
        val_start = test_start - relativedelta(months=val_months)
        train_start = min_date  # 擴張式窗口

        train_mask = (dataset["Date"] >= train_start) & (dataset["Date"] < val_start)
        val_mask = (dataset["Date"] >= val_start) & (dataset["Date"] < test_start)
        test_mask = (dataset["Date"] >= test_start) & (dataset["Date"] < test_end)

        train_df = dataset[train_mask]
        val_df = dataset[val_mask]
        test_df = dataset[test_mask]

        if len(train_df) > 100 and len(val_df) > 10 and len(test_df) > 10:
            yield fold, train_df, val_df, test_df, test_end
            fold += 1

        test_start += relativedelta(months=test_months)
