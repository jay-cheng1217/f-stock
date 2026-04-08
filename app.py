"""台股分析平台 — FastAPI 主程式

啟動方式:
    python app.py                    # 預設 http://localhost:8000
    python app.py --port 8080        # 自訂 port
    python app.py --reload           # 開發模式 (自動重載)

Log 檔案:
    logs/web_access.log    — API 請求記錄 (endpoint, 耗時, 狀態碼)
    logs/web_error.log     — 例外堆疊追蹤
    logs/pipeline.log      — 資料更新 / 模型訓練記錄
"""

import os
import time
import glob
import json
import re
import duckdb
import pandas as pd
import threading
import logging
import traceback
import argparse
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.routers import stocks, market, charts, rankings, scoring, news, t1, value, shadow
from backend.routers.t1 import _NAME_LOOKUP

# ==============================================================================
# Logging 設定
# ==============================================================================
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def _setup_logger(name: str, filename: str, level=logging.INFO) -> logging.Logger:
    """建立帶有檔案 handler 的 logger"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重複 handler
    if logger.handlers:
        return logger

    # 檔案 handler (append, UTF-8)
    fh = logging.FileHandler(
        os.path.join(LOG_DIR, filename),
        encoding="utf-8",
        mode="a",
    )
    fh.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 同時輸出到 console
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# 三個 logger
access_log = _setup_logger("web.access", "web_access.log")
error_log = _setup_logger("web.error", "web_error.log", logging.ERROR)
pipeline_log = _setup_logger("pipeline", "pipeline.log")


_PREDICTION_CACHE_LOCK = threading.Lock()
_PREDICTION_CACHE = {
    "context_key": None,
    "snapshot": None,
    "model": None,
    "meta": None,
    "pred_df": None,
    "loading": False,
    "last_error": None,
}
_SNAPSHOT_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "ml", "models", "latest_snapshot_cache.pkl"
)
_SNAPSHOT_CACHE_META_PATH = os.path.join(
    os.path.dirname(__file__), "ml", "models", "latest_snapshot_cache_meta.json"
)
BUY_PROB_EDGE_MIN = 0.0
STRONG_BUY_PROB_EDGE_MIN = 0.05
RECOMMENDATION_OVERHEAT_THRESHOLD = 0.18
STRONG_BUY_MAX_INST_SELL_PCT = 20.0
TOP30_EXCLUDE_INST_SELL_PCT = 30.0
PAPER_PORTFOLIO_DB_PATH = os.path.join(os.path.dirname(__file__), "paper_portfolio.db")
PAPER_PORTFOLIO_START_DATE = "2026-03-18"
PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")


def _artifact_fingerprint(path: str | None) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    st = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


def _glob_artifact_fingerprints(pattern: str, limit: int | None = None) -> list[dict]:
    paths = sorted(glob.glob(pattern))
    if limit is not None:
        paths = paths[-limit:]
    return [
        fp
        for fp in (_artifact_fingerprint(path) for path in paths)
        if fp is not None
    ]


def _prediction_context_key() -> str | None:
    from ml.config import (
        DOWN_THRESHOLD,
        FORWARD_DAYS,
        MIN_AVG_VOLUME,
        MIN_HISTORY_DAYS,
        MIN_PRICE,
        MODEL_DIR,
        UP_THRESHOLD,
    )
    from ml.model_selection import MODEL_SELECTION_PATH, resolve_base_meta_path

    selected_meta_path = resolve_base_meta_path("production")
    if not selected_meta_path:
        return None

    pred_files = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(path))
    ]
    ml_dir = os.path.join(os.path.dirname(__file__), "ml")
    quarter_financial_dir = os.path.join(os.path.dirname(__file__), "季報財務")
    quarter_bs_dir = os.path.join(os.path.dirname(__file__), "資產負債")
    payload = {
        "cache_schema": 4,
        "meta": _artifact_fingerprint(selected_meta_path),
        "model_selection": _artifact_fingerprint(MODEL_SELECTION_PATH),
        "predictions": _artifact_fingerprint(pred_files[-1]) if pred_files else None,
        "filters": {
            "forward_days": FORWARD_DAYS,
            "up_threshold": UP_THRESHOLD,
            "down_threshold": DOWN_THRESHOLD,
            "min_avg_volume": MIN_AVG_VOLUME,
            "min_price": MIN_PRICE,
            "min_history_days": MIN_HISTORY_DAYS,
        },
        "code": {
            "app": _artifact_fingerprint(__file__),
            "config": _artifact_fingerprint(os.path.join(ml_dir, "config.py")),
            "dataset": _artifact_fingerprint(os.path.join(ml_dir, "dataset.py")),
            "registry": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "registry.py")
            ),
            "technical": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "technical.py")
            ),
            "sector": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "sector.py")
            ),
            "fundamental": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "fundamental.py")
            ),
            "eps": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "eps.py")
            ),
            "balance_sheet": _artifact_fingerprint(
                os.path.join(ml_dir, "features", "balance_sheet.py")
            ),
        },
        "live_inputs": {
            "financial": _glob_artifact_fingerprints(
                os.path.join(quarter_financial_dir, "financial_*.csv")
            ),
            "eps": _glob_artifact_fingerprints(
                os.path.join(quarter_financial_dir, "eps_*.csv")
            ),
            "balance_sheet": _glob_artifact_fingerprints(
                os.path.join(quarter_bs_dir, "bs_*.csv")
            ),
        },
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _load_snapshot_cache_from_disk(context_key: str):
    import pandas as pd

    if not (
        os.path.exists(_SNAPSHOT_CACHE_PATH) and os.path.exists(_SNAPSHOT_CACHE_META_PATH)
    ):
        return None

    try:
        with open(_SNAPSHOT_CACHE_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("context_key") != context_key:
            return None
        return pd.read_pickle(_SNAPSHOT_CACHE_PATH)
    except Exception as e:
        pipeline_log.warning(f"載入 snapshot 快取失敗，將重新建置: {e}")
        return None


def _save_snapshot_cache_to_disk(context_key: str, snapshot) -> None:
    meta_tmp = _SNAPSHOT_CACHE_META_PATH + ".tmp"
    data_tmp = _SNAPSHOT_CACHE_PATH + ".tmp"
    snapshot.to_pickle(data_tmp)
    with open(meta_tmp, "w", encoding="utf-8") as f:
        json.dump({"context_key": context_key, "rows": int(len(snapshot))}, f, ensure_ascii=False)
    os.replace(data_tmp, _SNAPSHOT_CACHE_PATH)
    os.replace(meta_tmp, _SNAPSHOT_CACHE_META_PATH)


def _load_pipeline_snapshot():
    """從 pipeline predict 階段存的 snapshot_cache.pkl 載入"""
    import pandas as pd
    from ml.predict import SNAPSHOT_CACHE_PATH

    if not os.path.exists(SNAPSHOT_CACHE_PATH):
        return None
    try:
        return pd.read_pickle(SNAPSHOT_CACHE_PATH)
    except Exception as e:
        pipeline_log.warning(f"載入 pipeline snapshot 失敗: {e}")
        return None


def _load_live_financial_override_df(financial_dir: str):
    import numpy as np
    import pandas as pd

    files = sorted(glob.glob(os.path.join(financial_dir, "financial_*.csv")))
    if not files:
        return pd.DataFrame(columns=["ticker"])

    frames = []
    keep_cols = [
        "Ticker",
        "Year",
        "Season",
        "Revenue_M",
        "Gross_Margin_Pct",
        "Operating_Margin_Pct",
        "Net_Margin_Pct",
        "Pretax_Margin_Pct",
    ]
    for path in files:
        try:
            df = pd.read_csv(path, dtype={"Ticker": str})
        except Exception:
            continue
        if df.empty:
            continue
        for col in keep_cols:
            if col not in df.columns:
                df[col] = np.nan
        frames.append(df[keep_cols].copy())

    if not frames:
        return pd.DataFrame(columns=["ticker"])

    fund = pd.concat(frames, ignore_index=True)
    fund = fund.rename(
        columns={
            "Ticker": "ticker",
            "Year": "year",
            "Season": "season",
            "Revenue_M": "revenue_m",
            "Gross_Margin_Pct": "gross_margin_latest",
            "Operating_Margin_Pct": "operating_margin_latest",
            "Net_Margin_Pct": "net_margin_latest",
            "Pretax_Margin_Pct": "pretax_margin_latest",
        }
    )
    fund["ticker"] = fund["ticker"].astype(str)
    fund["year"] = pd.to_numeric(fund["year"], errors="coerce")
    fund["season"] = pd.to_numeric(fund["season"], errors="coerce")
    fund = fund.dropna(subset=["year", "season"])
    fund["year"] = fund["year"].astype(int)
    fund["season"] = fund["season"].astype(int)

    numeric_cols = [
        "revenue_m",
        "gross_margin_latest",
        "operating_margin_latest",
        "net_margin_latest",
        "pretax_margin_latest",
    ]
    for col in numeric_cols:
        fund[col] = pd.to_numeric(fund[col], errors="coerce").astype(np.float32)

    for col in [
        "gross_margin_latest",
        "operating_margin_latest",
        "net_margin_latest",
        "pretax_margin_latest",
    ]:
        fund[col] = (fund[col] / 100.0).astype(np.float32)

    fund = fund.sort_values(["ticker", "year", "season"]).reset_index(drop=True)
    by_ticker = fund.groupby("ticker", sort=False)
    fund["margin_trend"] = by_ticker["operating_margin_latest"].diff().astype(np.float32)
    fund["gross_margin_trend"] = by_ticker["gross_margin_latest"].diff().astype(np.float32)

    prev_rev = by_ticker["revenue_m"].shift(1)
    fund["revenue_qoq"] = np.where(
        prev_rev.abs() > 0.01,
        (fund["revenue_m"] - prev_rev) / prev_rev.abs(),
        np.nan,
    ).astype(np.float32)

    yoy_base = fund[["ticker", "year", "season", "revenue_m", "operating_margin_latest"]].copy()
    yoy_base["year"] = yoy_base["year"] + 1
    yoy_base = yoy_base.rename(
        columns={
            "revenue_m": "prev_revenue_same_q",
            "operating_margin_latest": "prev_op_margin_same_q",
        }
    )
    fund = fund.merge(yoy_base, on=["ticker", "year", "season"], how="left")
    fund["revenue_yoy_q"] = np.where(
        fund["prev_revenue_same_q"].abs() > 0.01,
        (fund["revenue_m"] - fund["prev_revenue_same_q"]) / fund["prev_revenue_same_q"].abs(),
        np.nan,
    ).astype(np.float32)
    fund["margin_yoy_change"] = (
        fund["operating_margin_latest"] - fund["prev_op_margin_same_q"]
    ).astype(np.float32)
    fund["margin_spread"] = (
        fund["gross_margin_latest"] - fund["operating_margin_latest"]
    ).astype(np.float32)
    fund["tax_effect"] = (
        fund["pretax_margin_latest"] - fund["net_margin_latest"]
    ).astype(np.float32)
    fund["revenue_log_scale"] = np.log1p(
        fund["revenue_m"].clip(lower=0)
    ).astype(np.float32)

    latest = fund.groupby("ticker", sort=False).tail(1).reset_index(drop=True)
    return latest[
        [
            "ticker",
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
    ]


def _load_live_eps_override_df(financial_dir: str):
    import numpy as np
    import pandas as pd

    files = sorted(glob.glob(os.path.join(financial_dir, "eps_*.csv")))
    if not files:
        return pd.DataFrame(columns=["ticker"])

    frames = []
    keep_cols = ["Ticker", "Year", "Season", "EPS_Basic"]
    for path in files:
        try:
            df = pd.read_csv(path, dtype={"Ticker": str})
        except Exception:
            continue
        if df.empty:
            continue
        for col in keep_cols:
            if col not in df.columns:
                df[col] = np.nan
        frames.append(df[keep_cols].copy())

    if not frames:
        return pd.DataFrame(columns=["ticker"])

    eps_df = pd.concat(frames, ignore_index=True).rename(
        columns={
            "Ticker": "ticker",
            "Year": "year",
            "Season": "season",
            "EPS_Basic": "eps_cumul",
        }
    )
    eps_df["ticker"] = eps_df["ticker"].astype(str)
    eps_df["year"] = pd.to_numeric(eps_df["year"], errors="coerce")
    eps_df["season"] = pd.to_numeric(eps_df["season"], errors="coerce")
    eps_df = eps_df.dropna(subset=["year", "season"])
    eps_df["year"] = eps_df["year"].astype(int)
    eps_df["season"] = eps_df["season"].astype(int)
    eps_df["eps_cumul"] = pd.to_numeric(eps_df["eps_cumul"], errors="coerce").astype(np.float32)
    eps_df = eps_df.sort_values(["ticker", "year", "season"]).reset_index(drop=True)

    by_year = eps_df.groupby(["ticker", "year"], sort=False)
    prev_cumul = by_year["eps_cumul"].shift(1)
    prev_season = by_year["season"].shift(1)
    eps_df["eps_basic"] = np.where(
        eps_df["season"] == 1,
        eps_df["eps_cumul"],
        np.where(prev_season == (eps_df["season"] - 1), eps_df["eps_cumul"] - prev_cumul, np.nan),
    ).astype(np.float32)

    by_ticker = eps_df.groupby("ticker", sort=False)
    eps_df["eps_ttm"] = (
        by_ticker["eps_basic"]
        .rolling(4, min_periods=4)
        .sum()
        .reset_index(level=0, drop=True)
        .astype(np.float32)
    )
    prev_eps = by_ticker["eps_basic"].shift(1)
    prev_eps_2q = by_ticker["eps_basic"].shift(2)

    yoy_base = eps_df[["ticker", "year", "season", "eps_basic"]].copy()
    yoy_base["year"] = yoy_base["year"] + 1
    yoy_base = yoy_base.rename(columns={"eps_basic": "prev_eps_same_q"})
    eps_df = eps_df.merge(yoy_base, on=["ticker", "year", "season"], how="left")
    eps_df["eps_yoy"] = np.where(
        eps_df["prev_eps_same_q"].abs() > 0.001,
        (eps_df["eps_basic"] - eps_df["prev_eps_same_q"]) / eps_df["prev_eps_same_q"].abs(),
        np.nan,
    ).astype(np.float32)

    eps_df["eps_qoq"] = np.where(
        prev_eps.abs() > 0.001,
        (eps_df["eps_basic"] - prev_eps) / prev_eps.abs(),
        np.nan,
    ).astype(np.float32)
    eps_df["eps_momentum"] = (
        eps_df["eps_basic"] - prev_eps_2q
    ).astype(np.float32)

    latest = eps_df.groupby("ticker", sort=False).tail(1).reset_index(drop=True)
    return latest[
        [
            "ticker",
            "eps_basic",
            "eps_ttm",
            "eps_yoy",
            "eps_qoq",
            "eps_momentum",
        ]
    ]


def _load_live_balance_sheet_override_df(bs_dir: str, financial_dir: str):
    import numpy as np
    import pandas as pd

    bs_files = sorted(glob.glob(os.path.join(bs_dir, "bs_*.csv")))
    if not bs_files:
        return pd.DataFrame(columns=["ticker"])

    bs_frames = []
    bs_keep_cols = [
        "Ticker",
        "Year",
        "Season",
        "Current_Assets",
        "Total_Assets",
        "Current_Liabilities",
        "Total_Liabilities",
        "Total_Equity",
        "Book_Value_Per_Share",
        "Debt_Ratio",
        "Current_Ratio",
    ]
    for path in bs_files:
        try:
            df = pd.read_csv(path, dtype={"Ticker": str})
        except Exception:
            continue
        if df.empty:
            continue
        for col in bs_keep_cols:
            if col not in df.columns:
                df[col] = np.nan
        bs_frames.append(df[bs_keep_cols].copy())

    if not bs_frames:
        return pd.DataFrame(columns=["ticker"])

    bs_df = pd.concat(bs_frames, ignore_index=True).rename(
        columns={
            "Ticker": "ticker",
            "Year": "year",
            "Season": "season",
            "Current_Assets": "current_assets",
            "Total_Assets": "total_assets",
            "Current_Liabilities": "current_liabilities",
            "Total_Liabilities": "total_liabilities",
            "Total_Equity": "total_equity",
            "Book_Value_Per_Share": "book_value_per_share",
            "Debt_Ratio": "debt_ratio",
            "Current_Ratio": "current_ratio",
        }
    )
    bs_df["ticker"] = bs_df["ticker"].astype(str)
    bs_df["year"] = pd.to_numeric(bs_df["year"], errors="coerce")
    bs_df["season"] = pd.to_numeric(bs_df["season"], errors="coerce")
    bs_df = bs_df.dropna(subset=["year", "season"])
    bs_df["year"] = bs_df["year"].astype(int)
    bs_df["season"] = bs_df["season"].astype(int)

    numeric_cols = [
        "current_assets",
        "total_assets",
        "current_liabilities",
        "total_liabilities",
        "total_equity",
        "book_value_per_share",
        "debt_ratio",
        "current_ratio",
    ]
    for col in numeric_cols:
        bs_df[col] = pd.to_numeric(bs_df[col], errors="coerce").astype(np.float32)

    need_debt = bs_df["debt_ratio"].isna() & bs_df["total_liabilities"].notna() & bs_df["total_assets"].gt(0)
    bs_df.loc[need_debt, "debt_ratio"] = (
        bs_df.loc[need_debt, "total_liabilities"] / bs_df.loc[need_debt, "total_assets"] * 100.0
    ).astype(np.float32)

    need_current = (
        bs_df["current_ratio"].isna()
        & bs_df["current_assets"].notna()
        & bs_df["current_liabilities"].gt(0)
    )
    bs_df.loc[need_current, "current_ratio"] = (
        bs_df.loc[need_current, "current_assets"] / bs_df.loc[need_current, "current_liabilities"] * 100.0
    ).astype(np.float32)

    net_income_df = pd.DataFrame(columns=["ticker", "year", "season", "net_income_m"])
    financial_files = sorted(glob.glob(os.path.join(financial_dir, "financial_*.csv")))
    if financial_files:
        fin_frames = []
        fin_keep_cols = ["Ticker", "Year", "Season", "Revenue_M", "Net_Margin_Pct", "Net_Income_M"]
        for path in financial_files:
            try:
                df = pd.read_csv(path, dtype={"Ticker": str})
            except Exception:
                continue
            if df.empty:
                continue
            for col in fin_keep_cols:
                if col not in df.columns:
                    df[col] = np.nan
            fin_frames.append(df[fin_keep_cols].copy())
        if fin_frames:
            fin_df = pd.concat(fin_frames, ignore_index=True).rename(
                columns={
                    "Ticker": "ticker",
                    "Year": "year",
                    "Season": "season",
                    "Revenue_M": "revenue_m",
                    "Net_Margin_Pct": "net_margin_pct",
                    "Net_Income_M": "net_income_m",
                }
            )
            fin_df["ticker"] = fin_df["ticker"].astype(str)
            fin_df["year"] = pd.to_numeric(fin_df["year"], errors="coerce")
            fin_df["season"] = pd.to_numeric(fin_df["season"], errors="coerce")
            fin_df = fin_df.dropna(subset=["year", "season"])
            fin_df["year"] = fin_df["year"].astype(int)
            fin_df["season"] = fin_df["season"].astype(int)
            fin_df["revenue_m"] = pd.to_numeric(fin_df["revenue_m"], errors="coerce").astype(np.float32)
            fin_df["net_margin_pct"] = pd.to_numeric(fin_df["net_margin_pct"], errors="coerce").astype(np.float32)
            fin_df["net_income_m"] = pd.to_numeric(fin_df["net_income_m"], errors="coerce").astype(np.float32)
            need_net_income = fin_df["net_income_m"].isna() & fin_df["revenue_m"].notna() & fin_df["net_margin_pct"].notna()
            fin_df.loc[need_net_income, "net_income_m"] = (
                fin_df.loc[need_net_income, "revenue_m"] * fin_df.loc[need_net_income, "net_margin_pct"] / 100.0
            ).astype(np.float32)
            net_income_df = fin_df[["ticker", "year", "season", "net_income_m"]].copy()

    bs_df = bs_df.merge(net_income_df, on=["ticker", "year", "season"], how="left")
    ni_thousands = bs_df["net_income_m"] * 1000.0
    bs_df["roe_annualized"] = np.where(
        bs_df["total_equity"].abs() > 0.01,
        ni_thousands / bs_df["total_equity"] * 100.0,
        np.nan,
    ).astype(np.float32)
    bs_df["roa_annualized"] = np.where(
        bs_df["total_assets"].abs() > 0.01,
        ni_thousands / bs_df["total_assets"] * 100.0,
        np.nan,
    ).astype(np.float32)
    bs_df["equity_ratio"] = np.where(
        bs_df["total_assets"] > 0,
        bs_df["total_equity"] / bs_df["total_assets"] * 100.0,
        np.nan,
    ).astype(np.float32)

    bs_df = bs_df.sort_values(["ticker", "year", "season"]).reset_index(drop=True)
    by_ticker = bs_df.groupby("ticker", sort=False)
    bs_df["debt_ratio_trend"] = by_ticker["debt_ratio"].diff().astype(np.float32)
    bs_df["roe_trend"] = by_ticker["roe_annualized"].diff().astype(np.float32)

    latest = bs_df.groupby("ticker", sort=False).tail(1).reset_index(drop=True)
    return latest[
        [
            "ticker",
            "debt_ratio",
            "current_ratio",
            "roe_annualized",
            "roa_annualized",
            "book_value_per_share",
            "equity_ratio",
            "debt_ratio_trend",
            "roe_trend",
        ]
    ]


def _apply_live_latest_quarter_overrides(snapshot):
    import numpy as np
    import pandas as pd
    from ml.config import BASE_DIR, FINANCIAL_DIR
    from ml.dataset import _winsorize_features

    if snapshot is None or getattr(snapshot, "empty", True) or "ticker" not in snapshot.columns:
        return snapshot

    bs_dir = os.path.join(BASE_DIR, "資產負債")
    if not os.path.isdir(FINANCIAL_DIR) or not os.path.isdir(bs_dir):
        return snapshot

    override_parts = [
        _load_live_financial_override_df(FINANCIAL_DIR),
        _load_live_eps_override_df(FINANCIAL_DIR),
        _load_live_balance_sheet_override_df(bs_dir, FINANCIAL_DIR),
    ]
    override_parts = [part for part in override_parts if not part.empty]
    if not override_parts:
        return snapshot

    override_df = override_parts[0]
    for part in override_parts[1:]:
        override_df = override_df.merge(part, on="ticker", how="outer")

    out = snapshot.copy()
    out["ticker"] = out["ticker"].astype(str)
    override_df["ticker"] = override_df["ticker"].astype(str)
    override_df = override_df.drop_duplicates(subset=["ticker"], keep="last").set_index("ticker")

    updated_cols = []
    for col in [c for c in override_df.columns if c != "ticker"]:
        mapped = out["ticker"].map(override_df[col])
        if not mapped.notna().any():
            continue
        existing = out[col] if col in out.columns else pd.Series(np.nan, index=out.index)
        out[col] = existing.where(mapped.isna(), mapped)
        updated_cols.append(col)

    if not updated_cols:
        return snapshot

    out = _winsorize_features(out)
    pipeline_log.info(
        "live snapshot 已套用最新季度覆蓋: %s",
        ", ".join(updated_cols),
    )
    return out


def _get_prediction_context():
    from ml.predict import load_latest_model
    from ml.dataset import build_latest_snapshot

    context_key = _prediction_context_key()
    if context_key is None:
        raise FileNotFoundError("找不到最新模型 metadata")

    with _PREDICTION_CACHE_LOCK:
        if (
            _PREDICTION_CACHE["context_key"] == context_key
            and _PREDICTION_CACHE["snapshot"] is not None
            and _PREDICTION_CACHE["model"] is not None
            and _PREDICTION_CACHE["meta"] is not None
        ):
            return (
                _PREDICTION_CACHE["snapshot"],
                _PREDICTION_CACHE["model"],
                _PREDICTION_CACHE["meta"],
            )
        if _PREDICTION_CACHE["loading"] and _PREDICTION_CACHE["context_key"] == context_key:
            raise RuntimeError("模型解釋快取建置中，預測列表可正常瀏覽，explain 約需 10 分鐘。")
        _PREDICTION_CACHE["loading"] = True
        _PREDICTION_CACHE["context_key"] = context_key
        _PREDICTION_CACHE["last_error"] = None
        _PREDICTION_CACHE["pred_df"] = None

    try:
        model, meta = load_latest_model()

        # 優先從 pipeline 存的 snapshot 載入（秒級），避免重算 12 分鐘
        snapshot = _load_pipeline_snapshot()
        if snapshot is not None:
            pipeline_log.info(f"已從 pipeline 快取載入 snapshot: {len(snapshot):,} 筆")
        else:
            # 再嘗試 server 自己的 disk cache
            snapshot = _load_snapshot_cache_from_disk(context_key)
            if snapshot is not None:
                pipeline_log.info(f"已載入 server snapshot 快取: {len(snapshot):,} 筆")

        if snapshot is None:
            # 最後才從頭建置
            started = time.time()
            pipeline_log.info("開始建置 explain snapshot 快取...")
            snapshot = build_latest_snapshot(verbose=False)
            _save_snapshot_cache_to_disk(context_key, snapshot)
            pipeline_log.info(
                f"explain snapshot 快取建置完成: {len(snapshot):,} 筆, "
                f"{time.time() - started:.1f}s"
            )

        snapshot = _apply_live_latest_quarter_overrides(snapshot)

        with _PREDICTION_CACHE_LOCK:
            _PREDICTION_CACHE["snapshot"] = snapshot
            _PREDICTION_CACHE["model"] = model
            _PREDICTION_CACHE["meta"] = meta
            _PREDICTION_CACHE["loading"] = False
            _PREDICTION_CACHE["last_error"] = None
        return snapshot, model, meta
    except Exception as e:
        with _PREDICTION_CACHE_LOCK:
            _PREDICTION_CACHE["loading"] = False
            _PREDICTION_CACHE["last_error"] = str(e)
        raise


def _warm_prediction_context_async() -> None:
    context_key = _prediction_context_key()
    if context_key is None:
        return

    with _PREDICTION_CACHE_LOCK:
        has_ready_cache = (
            _PREDICTION_CACHE["context_key"] == context_key
            and _PREDICTION_CACHE["snapshot"] is not None
            and _PREDICTION_CACHE["model"] is not None
            and _PREDICTION_CACHE["meta"] is not None
        )
        already_loading = (
            _PREDICTION_CACHE["loading"]
            and _PREDICTION_CACHE["context_key"] == context_key
        )

    if has_ready_cache or already_loading:
        return

    def _runner():
        try:
            _get_prediction_context()
        except RuntimeError:
            pass
        except Exception as e:
            pipeline_log.error(f"explain snapshot 預熱失敗: {e}\n{traceback.format_exc()}")

    threading.Thread(
        target=_runner,
        name="prediction-snapshot-warm",
        daemon=True,
    ).start()


def _build_live_prediction_df(snapshot, model, meta):
    import numpy as np
    import pandas as pd
    from ml.features.entry import ENTRY_INFO_COLS
    from ml.predict import (
        load_v2_model,
        _compute_dimension_scores,
        _apply_recommendation_rules,
        _sort_prediction_df,
    )

    feature_cols = meta["feature_columns"]
    X = pd.DataFrame(
        {
            col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
            for col in feature_cols
        }
    )
    proba = model.predict(X.values)

    # 基本欄位 + 進場信號 + 指標欄位
    keep_cols = ["ticker", "Date", "Close"]
    _extra = ["entry_score", "phase", "position_52w", "price_vs_ma20",
              "dist_to_high_20d", "dist_to_high_60d"]
    for col in ENTRY_INFO_COLS + _extra:
        if col in snapshot.columns:
            keep_cols.append(col)
    pred_df = snapshot[keep_cols].copy()
    pred_df["date"] = pred_df["Date"].dt.strftime("%Y-%m-%d")
    pred_df["close"] = pred_df["Close"]
    pred_df["up_prob"] = proba[:, 2]
    pred_df["flat_prob"] = proba[:, 1]
    pred_df["down_prob"] = proba[:, 0]
    pred_df["signal"] = np.where(
        pred_df["up_prob"] >= pred_df["down_prob"],
        "UP",
        np.where(
            pred_df["flat_prob"] >= pred_df["down_prob"],
            "FLAT",
            "DOWN",
        ),
    )

    # --- v2 迴歸模型 (20天報酬 + 四面向拆解) ---
    v2_model, v2_meta = load_v2_model()
    if v2_model is not None and v2_meta is not None:
        v2_cols = v2_meta["feature_columns"]
        X_v2 = pd.DataFrame(
            {
                col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
                for col in v2_cols
            }
        )
        pred_df["pred_return_20d"] = v2_model.predict(X_v2.values)

        # 四面向拆解
        dim_scores = _compute_dimension_scores(v2_model, X_v2.values, v2_cols)
        for dim_name, scores in dim_scores.items():
            pred_df[f"dim_{dim_name}"] = scores

        # 推薦等級與風險標籤統一走 ml.predict 的規則，避免 live/API/匯出分叉
        pred_df = _apply_recommendation_rules(pred_df, snapshot)

    # 產業標籤
    from ml.predict import _SECTOR_LOOKUP
    pred_df["sector"] = pred_df["ticker"].map(_SECTOR_LOOKUP).fillna("其他")

    out_cols = ["ticker", "date", "close", "up_prob", "flat_prob", "down_prob", "signal"]
    _extra = ["entry_score", "phase", "position_52w", "price_vs_ma20",
              "dist_to_high_20d", "dist_to_high_60d"]
    for col in ENTRY_INFO_COLS + _extra:
        if col in pred_df.columns:
            out_cols.append(col)
    # v2 欄位
    for col in ["pred_return_20d", "prob_edge", "risk_adjusted_return", "leaderboard_score",
                "recommendation", "risk_tags", "sector",
                "dim_籌碼與波動引擎", "dim_總經與大盤環境", "dim_基本面防禦網"]:
        if col in pred_df.columns:
            out_cols.append(col)
    pred_df = pred_df[out_cols]

    # 排序：與 ml.predict 共用同一套榜單排序規則
    return _sort_prediction_df(pred_df)


def _load_foreign_ownership():
    """讀取最新的外資持股資料，回傳 {ticker: foreign_pct} dict。"""
    import pandas as pd
    fdir = os.path.join(os.path.dirname(__file__), "外資持股")
    if not os.path.isdir(fdir):
        return {}
    files = sorted(glob.glob(os.path.join(fdir, "*.csv")))
    if not files:
        return {}
    try:
        df = pd.read_csv(files[-1], dtype={"Ticker": str})
        return dict(zip(df["Ticker"], df["Foreign_Pct"]))
    except Exception:
        return {}


def _load_predictions_csv_fallback():
    """快取建置中時，從已存的 predictions CSV 提供預測（不含 explain）"""
    import pandas as pd
    from ml.config import MODEL_DIR
    from ml.predict import load_latest_model, _sort_prediction_df

    pred_files = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(path))
    ]
    if not pred_files:
        return None, None
    try:
        _, meta = load_latest_model()
        df = pd.read_csv(pred_files[-1])
        df = _sort_prediction_df(df)
        return df, meta
    except Exception:
        return None, None


def _get_live_prediction_df():
    context_key = _prediction_context_key()

    # 先檢查快取中是否已有 pred_df
    with _PREDICTION_CACHE_LOCK:
        if (
            _PREDICTION_CACHE["context_key"] == context_key
            and _PREDICTION_CACHE["pred_df"] is not None
            and _PREDICTION_CACHE["meta"] is not None
        ):
            return _PREDICTION_CACHE["pred_df"].copy(), _PREDICTION_CACHE["meta"]

    try:
        snapshot, model, meta = _get_prediction_context()
    except RuntimeError:
        # 快取建置中 → 用 CSV fallback
        df, meta = _load_predictions_csv_fallback()
        if df is not None:
            return df, meta
        raise  # 連 CSV 都沒有才報錯

    pred_df = _build_live_prediction_df(snapshot, model, meta)

    with _PREDICTION_CACHE_LOCK:
        if _PREDICTION_CACHE["context_key"] == context_key:
            _PREDICTION_CACHE["pred_df"] = pred_df.copy()

    return pred_df, meta


def _prediction_unavailable_response(ticker: str):
    from ml.dataset import diagnose_stock_data_status

    diagnostics = diagnose_stock_data_status(ticker)
    eligibility = diagnostics["eligibility"]
    if not eligibility.get("eligible"):
        return {
            "error": diagnostics["summary"],
            "status": "excluded",
            "ticker": ticker,
            "eligibility": eligibility,
            "diagnostics": diagnostics,
        }

    return {
        "error": f"{ticker} 不在本次 ML 預測快照中，請重新建置最新 snapshot。",
        "status": "missing",
        "ticker": ticker,
        "eligibility": eligibility,
        "diagnostics": diagnostics,
    }


# ==============================================================================
# FastAPI App
# ==============================================================================
app = FastAPI(
    title="台股分析平台",
    description="台股四大面向分析 + ML 預測系統",
    version="1.0.0",
)


# ==============================================================================
# Middleware: 請求 log + 錯誤攔截
# ==============================================================================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """記錄每個 API 請求的 endpoint、耗時、狀態碼、錯誤回應"""
    start = time.time()
    client = request.client.host if request.client else "-"
    method = request.method
    path = request.url.path
    query = str(request.url.query) if request.url.query else ""
    q_str = f"?{query}" if query else ""

    try:
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000
        status = response.status_code

        # 非 2xx 狀態碼記錄為警告
        if status >= 400:
            error_log.error(
                f"{client} | {method} {path}{q_str} | {status} | {elapsed_ms:.1f}ms"
            )
        else:
            access_log.info(
                f"{client} | {method} {path}{q_str} | {status} | {elapsed_ms:.1f}ms"
            )

        # 慢查詢警告 (> 3 秒)
        if elapsed_ms > 3000:
            access_log.warning(
                f"SLOW | {client} | {method} {path}{q_str} | {elapsed_ms:.0f}ms"
            )

        return response

    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        error_log.error(
            f"{client} | {method} {path}{q_str} | EXCEPTION | {elapsed_ms:.1f}ms\n"
            f"  {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "detail": str(e)},
        )


# === 前端錯誤回報 API ===
@app.post("/api/log/error", tags=["logging"])
async def log_frontend_error(request: Request):
    """前端 JS 錯誤回報端點"""
    try:
        body = await request.json()
    except Exception:
        body = {"raw": (await request.body()).decode("utf-8", errors="replace")}

    client = request.client.host if request.client else "-"
    msg = body.get("message", str(body))
    url = body.get("url", "-")
    stack = body.get("stack", "")

    error_log.error(
        f"FRONTEND | {client} | {url}\n"
        f"  message: {msg}\n"
        f"  stack: {stack}"
    )
    return {"ok": True}


# ==============================================================================
# 掛載 API Routers
# ==============================================================================
app.include_router(stocks.router)
app.include_router(market.router)
app.include_router(charts.router)
app.include_router(rankings.router)
app.include_router(scoring.router)
app.include_router(news.router)
app.include_router(t1.router)
app.include_router(value.router)
app.include_router(shadow.router)


# ==============================================================================
# Paper Portfolio API
# ==============================================================================
def _load_duckdb_sqlite_extension(conn) -> None:
    try:
        conn.execute("LOAD sqlite")
    except Exception:
        conn.execute("INSTALL sqlite")
        conn.execute("LOAD sqlite")


def _empty_portfolio_summary() -> dict:
    return {
        "open_positions": 0,
        "pending_positions": 0,
        "open_tickers": 0,
        "alerted_positions": 0,
        "avg_unrealized_pct": None,
        "latest_mark_date": None,
    }


def _load_portfolio_detail_and_summary():
    import pandas as pd

    columns = [
        "ticker",
        "position_count",
        "selection_history",
        "avg_entry_price",
        "latest_price",
        "avg_unrealized_pct",
        "max_drawdown_pct",
        "latest_mark_date",
    ]
    if not os.path.exists(PAPER_PORTFOLIO_DB_PATH):
        return pd.DataFrame(columns=columns), _empty_portfolio_summary()

    conn = None
    try:
        conn = duckdb.connect()
        _load_duckdb_sqlite_extension(conn)
        escaped_db_path = PAPER_PORTFOLIO_DB_PATH.replace("'", "''")
        conn.execute(
            f"ATTACH '{escaped_db_path}' AS ledger (TYPE SQLITE, READ_ONLY)"
        )

        latest_marks_cte = """
            WITH latest_marks AS (
                SELECT position_id, mark_date, close, close_return_pct
                FROM (
                    SELECT
                        position_id,
                        mark_date,
                        close,
                        close_return_pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY position_id
                            ORDER BY mark_date DESC
                        ) AS rn
                    FROM ledger.portfolio_marks
                ) ranked_marks
                WHERE rn = 1
            )
        """

        detail_query = f"""
            {latest_marks_cte}
            SELECT
                p.ticker AS ticker,
                COUNT(*) AS position_count,
                STRING_AGG(
                    p.prediction_date || '|' ||
                    '#' || CAST(p.selection_rank AS VARCHAR) || '|' ||
                    COALESCE(p.recommendation, '未標記'),
                    '\n'
                    ORDER BY p.prediction_date DESC, p.selection_rank ASC
                ) AS selection_history,
                ROUND(AVG(p.entry_open), 2) AS avg_entry_price,
                ROUND(ARG_MAX(lm.close, lm.mark_date), 2) AS latest_price,
                ROUND(AVG(lm.close_return_pct) * 100, 2) AS avg_unrealized_pct,
                ROUND(MIN(p.max_drawdown_pct) * 100, 2) AS max_drawdown_pct,
                MAX(lm.mark_date) AS latest_mark_date
            FROM ledger.portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.status = 'open'
              AND p.entry_open IS NOT NULL
              AND p.prediction_date >= '{PAPER_PORTFOLIO_START_DATE}'
            GROUP BY p.ticker
            ORDER BY avg_unrealized_pct DESC NULLS LAST, p.ticker
        """
        detail_df = conn.execute(detail_query).df()

        summary_query = f"""
            {latest_marks_cte}
            SELECT
                SUM(CASE WHEN p.status = 'open' THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END) AS pending_positions,
                COUNT(DISTINCT CASE WHEN p.status = 'open' THEN p.ticker END) AS open_tickers,
                SUM(
                    CASE
                        WHEN p.status = 'open' AND p.max_drawdown_pct <= -0.10 THEN 1
                        ELSE 0
                    END
                ) AS alerted_positions,
                ROUND(
                    AVG(
                        CASE
                            WHEN p.status = 'open' THEN lm.close_return_pct
                            ELSE NULL
                        END
                    ) * 100,
                    2
                ) AS avg_unrealized_pct,
                MAX(CASE WHEN p.status = 'open' THEN lm.mark_date END) AS latest_mark_date
            FROM ledger.portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.prediction_date >= '{PAPER_PORTFOLIO_START_DATE}'
        """
        summary_row = conn.execute(summary_query).fetchone()
        summary = {
            "open_positions": int(summary_row[0] or 0),
            "pending_positions": int(summary_row[1] or 0),
            "open_tickers": int(summary_row[2] or 0),
            "alerted_positions": int(summary_row[3] or 0),
            "avg_unrealized_pct": (
                round(float(summary_row[4]), 2) if summary_row[4] is not None else None
            ),
            "latest_mark_date": summary_row[5],
        }
        return detail_df, summary

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _format_selection_history_for_export(history: str) -> str:
    if not history:
        return ""

    items = []
    for raw_line in str(history).splitlines():
        parts = raw_line.split("|", 2)
        if len(parts) != 3:
            continue
        date, rank, recommendation = parts
        items.append(f"{date} {rank} {recommendation}")
    return " / ".join(items)


def _prepare_portfolio_export_df(detail_df):
    export_df = detail_df.copy()
    if "selection_history" in export_df.columns:
        export_df["selection_history"] = export_df["selection_history"].fillna("").apply(
            _format_selection_history_for_export
        )

    rename_map = {
        "ticker": "股票代號",
        "position_count": "持有筆數",
        "selection_history": "入選紀錄",
        "avg_entry_price": "平均進場成本",
        "latest_price": "最新收盤價",
        "avg_unrealized_pct": "平均帳面損益(%)",
        "max_drawdown_pct": "最大盤中回撤(%)",
        "latest_mark_date": "最新更新日",
    }
    available = [col for col in rename_map if col in export_df.columns]
    return export_df.loc[:, available].rename(columns=rename_map)


@app.get("/api/portfolio", tags=["portfolio"])
def get_paper_portfolio():
    try:
        detail_df, summary = _load_portfolio_detail_and_summary()

        detail_records = []
        if not detail_df.empty:
            detail_df["name"] = detail_df["ticker"].map(_NAME_LOOKUP).fillna("")
            detail_records = json.loads(
                detail_df.to_json(orient="records", force_ascii=False)
            )

        return JSONResponse(
            content={"status": "success", "data": detail_records, "summary": summary}
        )

    except Exception as e:
        error_log.error(
            "Paper portfolio API failed\n" + traceback.format_exc()
        )
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )


@app.get("/api/portfolio/download", tags=["portfolio"])
def download_paper_portfolio():
    try:
        detail_df, summary = _load_portfolio_detail_and_summary()
        export_df = _prepare_portfolio_export_df(detail_df)
        file_date = summary.get("latest_mark_date") or datetime.now().strftime("%Y-%m-%d")
        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="paper_portfolio_{file_date}.csv"'
                )
            },
        )
    except Exception as e:
        error_log.error(
            "Paper portfolio download failed\n" + traceback.format_exc()
        )
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )


# ==============================================================================
# ML 預測 API
# ==============================================================================
@app.get("/api/predictions/latest", tags=["predictions"])
def latest_predictions(top_n: int = 30):
    """取得最新 ML 預測結果"""
    import pandas as pd

    try:
        df, meta = _get_live_prediction_df()
    except FileNotFoundError:
        return {"error": "尚無預測結果，請先執行模型預測", "data": []}

    except RuntimeError as e:
        return {"error": str(e), "status": "warming", "data": []}

    pred_date = str(df["date"].iloc[0]) if not df.empty else ""

    # 階段名稱映射
    _PHASE_NAMES = {0: "超賣低檔", 1: "盤整等待", 2: "強勢延續", 3: "過熱警示"}

    has_v2 = "pred_return_20d" in df.columns
    foreign_map = _load_foreign_ownership()

    # 預計算三層次百分位排名 (全市場基準)
    dim_pct = {}
    if has_v2:
        for dim in ["籌碼與波動引擎", "總經與大盤環境", "基本面防禦網"]:
            col = f"dim_{dim}"
            if col in df.columns:
                dim_pct[col] = df[col].rank(pct=True)

    top_up = _get_prediction_leaderboard_df(df, top_n=top_n)
    records = []
    for _, row in top_up.iterrows():
        rec = {
            "ticker": row["ticker"],
            "name": _NAME_LOOKUP.get(str(row["ticker"]), ""),
            "close": round(float(row["close"]), 2) if pd.notna(row.get("close")) else None,
            "up_prob": round(float(row["up_prob"]), 4),
            "flat_prob": round(float(row["flat_prob"]), 4),
            "down_prob": round(float(row["down_prob"]), 4),
            "signal": row.get("signal", ""),
        }
        # v2 欄位
        if has_v2 and pd.notna(row.get("pred_return_20d")):
            ret_pct = round(float(row["pred_return_20d"]) * 100, 2)
            rec["pred_return_20d"] = ret_pct
            rec["recommendation"] = str(row.get("recommendation", ""))
            if abs(ret_pct) > 50:
                rec["pred_warning"] = "預測值超出合理範圍，建議重新執行預測"
        if has_v2:
            # 風險標籤
            risk_tags = str(row.get("risk_tags", ""))
            if risk_tags:
                rec["risk_tags"] = risk_tags
            # 產業
            sector = str(row.get("sector", ""))
            if sector:
                rec["sector"] = sector
            for dim in ["籌碼與波動引擎", "總經與大盤環境", "基本面防禦網"]:
                col = f"dim_{dim}"
                if pd.notna(row.get(col)):
                    rec[col] = round(float(row[col]), 4)
                    # 百分位排名 (0~100)，讓前端判斷好壞
                    if col in dim_pct and pd.notna(dim_pct[col].loc[row.name]):
                        rec[f"{col}_pct"] = round(float(dim_pct[col].loc[row.name]) * 100, 1)
        # 進場信號欄位
        if pd.notna(row.get("entry_score")):
            rec["entry_score"] = round(float(row["entry_score"]), 1)
        if pd.notna(row.get("phase")):
            rec["phase"] = _PHASE_NAMES.get(int(row["phase"]), "")
        # 52週位置: 0%=年度最低, 100%=年度最高
        if pd.notna(row.get("position_52w")):
            rec["pos_52w"] = round(float(row["position_52w"]) * 100, 1)
        # 過熱程度: 偏離 MA20 的幅度 (%)
        if pd.notna(row.get("price_vs_ma20")):
            rec["overheat"] = round(float(row["price_vs_ma20"]) * 100, 1)
        # 法人成本 + 部位 + 與現價比較
        if pd.notna(row.get("inst_cost_10d")):
            inst_cost = float(row["inst_cost_10d"])
            rec["inst_cost"] = round(inst_cost, 2)
            close = float(row["close"]) if pd.notna(row.get("close")) else None
            if close and close > 0:
                rec["inst_cost_diff"] = round((close / inst_cost - 1) * 100, 1)
        if pd.notna(row.get("inst_net_10d")):
            rec["inst_net_10d"] = round(float(row["inst_net_10d"]) / 1000)  # 股→張
        if pd.notna(row.get("inst_vol_pct_10d")):
            rec["inst_vol_pct"] = round(float(row["inst_vol_pct_10d"]), 1)
        # 外資持股比率
        t = str(row["ticker"])
        if t in foreign_map and pd.notna(foreign_map[t]):
            rec["foreign_pct"] = round(float(foreign_map[t]), 2)
        # 成交密集區
        if pd.notna(row.get("poc_20d")):
            rec["poc_20d"] = round(float(row["poc_20d"]), 2)
        if pd.notna(row.get("va_low_20d")):
            rec["va_low_20d"] = round(float(row["va_low_20d"]), 2)
        if pd.notna(row.get("va_high_20d")):
            rec["va_high_20d"] = round(float(row["va_high_20d"]), 2)
        records.append(rec)

    # 推薦分佈
    rec_dist = {}
    if has_v2 and "recommendation" in df.columns:
        rec_dist = {k: int(v) for k, v in df["recommendation"].value_counts().items()}

    # 全市場報酬統計
    market_info = {}
    if has_v2 and "market_sentiment" in df.columns:
        market_info = {
            "market_sentiment": str(df["market_sentiment"].iloc[0]),
            "market_return_median": round(float(df["market_return_median"].iloc[0]) * 100, 2),
            "market_return_q25": round(float(df["market_return_q25"].iloc[0]) * 100, 2),
            "market_return_q75": round(float(df["market_return_q75"].iloc[0]) * 100, 2),
        }

    return {
        "prediction_date": pred_date,
        "model_date": meta.get("trained_at", "")[:10] if meta.get("trained_at") else "",
        "model_f1": round(meta.get("avg_f1_weighted", 0), 4) if meta.get("avg_f1_weighted") else None,
        "has_v2": has_v2,
        "total_stocks": len(df),
        "signal_dist": {k: int(v) for k, v in df["signal"].value_counts().items()} if "signal" in df.columns else {},
        "rec_dist": rec_dist,
        "market": market_info,
        "data": records,
    }


_PREDICTION_EXPORT_RENAME_MAP = {
    "ticker": "股票代號",
    "date": "日期",
    "close": "收盤價",
    "prediction_direction": "預測方向",
    "up_prob": "上漲機率",
    "flat_prob": "持平機率",
    "down_prob": "下跌機率",
    "signal": "v1信號",
    "pred_return_20d": "預估20日報酬",
    "leaderboard_score": "榜單排序分數",
    "recommendation": "推薦等級",
    "risk_tags": "風險標籤",
    "positive_factor_text": "利多因素",
    "negative_factor_text": "利空因素",
    "sector": "產業",
    "dim_籌碼與波動引擎": "波動籌碼貢獻",
    "dim_總經與大盤環境": "總經貢獻",
    "dim_基本面防禦網": "基本面貢獻",
    "historical_win_rate": "歷史勝率",
    "historical_avg_return": "歷史平均報酬",
    "market_sentiment": "市場氣氛",
}

_PREDICTION_EXPORT_PRIORITY_COLUMNS = [
    "股票代號",
    "日期",
    "收盤價",
    "預測方向",
    "推薦等級",
    "預估20日報酬",
    "上漲機率",
    "持平機率",
    "下跌機率",
    "v1信號",
    "風險標籤",
    "利多因素",
    "利空因素",
    "產業",
    "波動籌碼貢獻",
    "總經貢獻",
    "基本面貢獻",
    "歷史勝率",
    "歷史平均報酬",
    "市場氣氛",
]


def _prediction_direction_label(row) -> str:
    import pandas as pd

    pred_return = row.get("pred_return_20d")
    if pd.notna(pred_return):
        pred_return = float(pred_return)
        if pred_return > 0:
            return "看多"
        if pred_return < 0:
            return "看空"
        return "中性"

    signal = str(row.get("signal", "")).strip()
    return {"UP": "看多", "DOWN": "看空", "FLAT": "中性"}.get(signal, "")


def _get_prediction_leaderboard_df(df, top_n: int = 30):
    has_v2 = "pred_return_20d" in df.columns

    # ML 頁面顯示的是「買進榜單」，不是全市場原始排序。
    if has_v2 and "sector" in df.columns:
        from ml.predict import apply_sector_cap, SECTOR_CAP_RATIO

        return apply_sector_cap(df, top_n=top_n, cap_ratio=SECTOR_CAP_RATIO)

    return df.head(top_n).copy()


def _prediction_metric_value(container, name: str):
    value = container.get(name) if hasattr(container, "get") else None
    if value is None or pd.isna(value):
        return None
    return float(value)


def _prediction_institutional_sell_pressure(container) -> float | None:
    inst_net = _prediction_metric_value(container, "inst_net_10d")
    inst_vol_pct = _prediction_metric_value(container, "inst_vol_pct_10d")
    if inst_net is not None and inst_vol_pct is not None:
        sign = 1.0 if inst_net > 0 else -1.0 if inst_net < 0 else 0.0
        return sign * (inst_vol_pct / 100.0)

    return _prediction_metric_value(container, "inst_buy_ratio_20d")


def _prediction_turnaround_signals(container) -> dict[str, object]:
    revenue_yoy_latest = _prediction_metric_value(container, "revenue_yoy_latest")
    revenue_yoy_3m_avg = _prediction_metric_value(container, "revenue_yoy_3m_avg")
    revenue_yoy_momentum = _prediction_metric_value(container, "revenue_yoy_momentum")
    eps_yoy = _prediction_metric_value(container, "eps_yoy")
    eps_qoq = _prediction_metric_value(container, "eps_qoq")
    eps_momentum = _prediction_metric_value(container, "eps_momentum")
    margin_trend = _prediction_metric_value(container, "margin_trend")
    gross_margin = _prediction_metric_value(container, "gross_margin_latest")
    operating_margin = _prediction_metric_value(container, "operating_margin_latest")

    profit_recovery = any(
        v is not None and v > threshold
        for v, threshold in [
            (eps_qoq, 0.0),
            (eps_momentum, 0.05),
            (eps_yoy, 0.10),
        ]
    )
    demand_recovery = any(
        v is not None and v > threshold
        for v, threshold in [
            (revenue_yoy_latest, 0.15),
            (revenue_yoy_3m_avg, 0.10),
            (revenue_yoy_momentum, 0.03),
        ]
    )
    margin_recovery = (
        (margin_trend is not None and margin_trend > 0.03)
        or (
            operating_margin is not None
            and gross_margin is not None
            and operating_margin > -0.08
            and gross_margin > 0.20
        )
    )
    score = int(profit_recovery) + int(demand_recovery) + int(margin_recovery)
    return {
        "active": score >= 2,
        "score": score,
        "profit_recovery": profit_recovery,
        "demand_recovery": demand_recovery,
        "margin_recovery": margin_recovery,
    }


_PREDICTION_EXPORT_FACTOR_SOURCE_COLUMNS = [
    "ticker",
    "Foreign_BuySell",
    "Trust_BuySell",
    "Dealer_BuySell",
    "return_20d",
    "return_60d",
    "price_vs_ma20",
    "revenue_yoy_latest",
    "revenue_yoy_3m_avg",
    "revenue_yoy_momentum",
    "gross_margin_latest",
    "operating_margin_latest",
    "margin_trend",
    "pe_ratio",
    "pb_ratio",
    "eps_ttm",
    "eps_yoy",
    "eps_qoq",
    "eps_momentum",
    "sector_momentum_5d",
    "sector_momentum_20d",
    "chip_diverge_bear",
    "chip_diverge_bull",
    "inst_total_20d_norm",
    "foreign_buy_streak",
    "trust_buy_streak",
    "whale_pct",
    "retail_pct",
    "whale_pct_chg",
    "retail_pct_chg",
    "price_vs_inst_cost",
    "inst_buy_ratio_20d",
]


def _build_prediction_export_factor_texts(row) -> tuple[str, str]:
    import pandas as pd

    positive_factors = []
    negative_factors = []

    def _add(target: list[str], text: str | None) -> None:
        if not text:
            return
        if text in positive_factors or text in negative_factors:
            return
        target.append(text)

    def _value(name: str):
        value = row.get(name)
        return None if pd.isna(value) else float(value)

    turnaround = _prediction_turnaround_signals(row)
    turnaround_active = turnaround["active"]

    revenue_yoy_latest = _value("revenue_yoy_latest")
    if revenue_yoy_latest is not None:
        pct = revenue_yoy_latest * 100
        if pct > 20:
            _add(positive_factors, f"最新月營收年增{pct:.1f}%，成長強勁")
        elif pct > 0:
            _add(positive_factors, f"最新月營收年增{pct:.1f}%")
        elif pct < 0:
            _add(negative_factors, f"最新月營收年減{abs(pct):.1f}%")

    revenue_yoy_3m_avg = _value("revenue_yoy_3m_avg")
    if revenue_yoy_3m_avg is not None:
        pct = revenue_yoy_3m_avg * 100
        if pct > 5:
            _add(positive_factors, f"近3月營收年增率均值{pct:+.1f}%")
        elif pct < -5:
            _add(negative_factors, f"近3月營收年增率均值{pct:+.1f}%")

    revenue_yoy_momentum = _value("revenue_yoy_momentum")
    if revenue_yoy_momentum is not None:
        if revenue_yoy_momentum > 0.05:
            _add(positive_factors, "月營收成長加速中")
        elif revenue_yoy_momentum < -0.05:
            _add(negative_factors, "月營收成長動能放緩")

    eps_ttm = _value("eps_ttm")
    if eps_ttm is not None:
        if eps_ttm > 5:
            _add(positive_factors, f"近四季EPS合計{eps_ttm:.2f}元，獲利穩健")
        elif eps_ttm > 1:
            _add(positive_factors, f"近四季EPS合計{eps_ttm:.2f}元")
        elif eps_ttm > 0:
            _add(negative_factors, f"近四季EPS合計{eps_ttm:.2f}元，獲利偏低")
        elif eps_ttm < 0:
            if turnaround_active:
                _add(negative_factors, f"近四季EPS合計{eps_ttm:.2f}元，尚未完全轉盈")
                _add(positive_factors, "單季獲利與營運動能回升，虧損收斂中")
            else:
                _add(negative_factors, f"近四季EPS合計{eps_ttm:.2f}元，處於虧損")

    eps_yoy = _value("eps_yoy")
    if eps_yoy is not None:
        pct = eps_yoy * 100
        if pct > 500:
            _add(negative_factors, f"EPS年增{pct:.0f}%（低基期效應，去年同期EPS極低，非實質成長）")
        elif pct > 20:
            if eps_ttm is not None and eps_ttm < 0 and turnaround_active:
                _add(positive_factors, f"EPS年增{pct:.0f}%，虧損收斂速度加快")
            else:
                _add(positive_factors, f"EPS年增{pct:.0f}%，獲利大幅成長")
        elif pct > 0:
            if eps_ttm is not None and eps_ttm < 0 and turnaround_active:
                _add(positive_factors, f"EPS年增{pct:.0f}%，轉機修復延續")
            else:
                _add(positive_factors, f"EPS年增{pct:.0f}%")
        elif pct < -20:
            _add(negative_factors, f"EPS年減{abs(pct):.0f}%，獲利大幅衰退")
        elif pct < 0:
            _add(negative_factors, f"EPS年減{abs(pct):.0f}%")

    eps_momentum = _value("eps_momentum")
    if eps_momentum is not None:
        if eps_momentum > 0.1:
            if eps_ttm is not None and eps_ttm < 0 and turnaround_active:
                _add(positive_factors, "EPS修復動能加速，接近轉盈")
            else:
                _add(positive_factors, "EPS成長動能加速")
        elif eps_momentum < -0.1:
            _add(negative_factors, "EPS成長動能減速")

    gross_margin_latest = _value("gross_margin_latest")
    if gross_margin_latest is not None:
        pct = gross_margin_latest * 100
        if pct > 40:
            _add(positive_factors, f"毛利率{pct:.1f}%，獲利能力佳")
        elif pct < 10:
            _add(negative_factors, f"毛利率僅{pct:.1f}%，獲利空間有限")

    operating_margin_latest = _value("operating_margin_latest")
    if operating_margin_latest is not None:
        pct = operating_margin_latest * 100
        if pct > 15:
            _add(positive_factors, f"營業利益率{pct:.1f}%，本業獲利佳")
        elif pct < 0:
            if turnaround_active and pct > -8:
                _add(negative_factors, f"營業利益率{pct:.1f}%，本業仍虧損")
                _add(positive_factors, "營業利益率較前期改善，本業修復中")
            else:
                _add(negative_factors, f"營業利益率{pct:.1f}%，本業虧損")

    return_60d = _value("return_60d")
    if return_60d is not None:
        pct = return_60d * 100
        if pct > 10:
            _add(positive_factors, f"近60日大漲{pct:.1f}%，短線動能強勁")
        elif pct > 0:
            _add(positive_factors, f"近60日上漲{pct:.1f}%")
        elif pct < -10:
            _add(negative_factors, f"近60日大跌{abs(pct):.1f}%，趨勢偏弱")
        elif pct < 0:
            _add(negative_factors, f"近60日下跌{abs(pct):.1f}%")

    return_20d = _value("return_20d")
    if return_20d is not None:
        pct = return_20d * 100
        if pct > 10:
            _add(positive_factors, f"近20日大漲{pct:.1f}%，中期趨勢偏多")
        elif pct < -10:
            _add(negative_factors, f"近20日大跌{abs(pct):.1f}%，中期趨勢偏空")

    sector_momentum_20d = _value("sector_momentum_20d")
    if sector_momentum_20d is not None:
        if sector_momentum_20d > 0.05:
            _add(positive_factors, "產業中期動能增強")
        elif sector_momentum_20d < -0.05:
            _add(negative_factors, "產業中期動能減弱")

    sector_momentum_5d = _value("sector_momentum_5d")
    if sector_momentum_5d is not None:
        if sector_momentum_5d > 0.05:
            _add(positive_factors, "產業短期動能增強")
        elif sector_momentum_5d < -0.05:
            _add(negative_factors, "產業短期動能減弱")

    foreign_buy_sell = _value("Foreign_BuySell")
    trust_buy_sell = _value("Trust_BuySell")
    if foreign_buy_sell is not None and trust_buy_sell is not None:
        if foreign_buy_sell > 0 and trust_buy_sell > 0:
            _add(positive_factors, "外資與投信同步買超，法人共識偏多")
        elif foreign_buy_sell < 0 and trust_buy_sell < 0:
            _add(negative_factors, "外資與投信同步賣超，法人共識偏空")
        elif foreign_buy_sell != 0 or trust_buy_sell != 0:
            _add(negative_factors, "外資投信方向分歧，法人無共識")

    inst_total_20d_norm = _value("inst_total_20d_norm")
    if inst_total_20d_norm is not None:
        if inst_total_20d_norm > 0.05:
            _add(positive_factors, "近20日法人累積買盤占成交比偏高")
        elif inst_total_20d_norm < -0.05:
            _add(negative_factors, "近20日法人累積賣壓偏重")

    foreign_buy_streak = _value("foreign_buy_streak")
    if foreign_buy_streak is not None and foreign_buy_streak >= 3:
        _add(positive_factors, f"外資連續買超{int(foreign_buy_streak)}日")

    trust_buy_streak = _value("trust_buy_streak")
    if trust_buy_streak is not None and trust_buy_streak >= 3:
        _add(positive_factors, f"投信連續買超{int(trust_buy_streak)}日")

    whale_pct_chg = _value("whale_pct_chg")
    retail_pct_chg = _value("retail_pct_chg")
    if whale_pct_chg is not None and retail_pct_chg is not None:
        if whale_pct_chg > 0 and retail_pct_chg < 0:
            _add(positive_factors, "大戶比重上升、散戶比重下降，籌碼轉佳")
        elif whale_pct_chg < 0 and retail_pct_chg > 0:
            _add(negative_factors, "大戶比重下滑、散戶比重上升，籌碼轉亂")

    price_vs_inst_cost = _value("price_vs_inst_cost")
    if price_vs_inst_cost is not None:
        pct = price_vs_inst_cost * 100
        if 0 <= pct <= 5:
            _add(positive_factors, f"股價貼近法人成本區（高於約{pct:.1f}%）")
        elif pct > 15:
            _add(negative_factors, f"股價高於法人成本區約{pct:.1f}%，追價壓力較大")

    inst_buy_ratio_20d = _value("inst_buy_ratio_20d")
    if inst_buy_ratio_20d is not None:
        pct = inst_buy_ratio_20d * 100
        if pct > 5:
            _add(positive_factors, f"近20日法人買盤占量比約{pct:.1f}%")

    chip_diverge_bear = _value("chip_diverge_bear")
    if chip_diverge_bear is not None:
        if chip_diverge_bear >= 1.0:
            _add(negative_factors, f"頂部籌碼背離（{chip_diverge_bear:.1f}）：股價漲但法人倒貨+散戶融資追買")
        elif chip_diverge_bear >= 0.3:
            _add(negative_factors, f"籌碼背離初現（{chip_diverge_bear:.1f}）：法人賣超伴隨融資增加")

    chip_diverge_bull = _value("chip_diverge_bull")
    if chip_diverge_bull is not None:
        if chip_diverge_bull >= 1.0:
            _add(positive_factors, f"底部籌碼背離（{chip_diverge_bull:.1f}）：股價跌但法人吃貨+散戶融資斷頭")
        elif chip_diverge_bull >= 0.3:
            _add(positive_factors, f"底部吸籌跡象（{chip_diverge_bull:.1f}）：法人逢低承接")

    pb_ratio = _value("pb_ratio")
    if pb_ratio is not None:
        if pb_ratio < 1:
            _add(positive_factors, f"股價淨值比{pb_ratio:.2f}，低於淨值")
        elif pb_ratio > 5:
            _add(negative_factors, f"股價淨值比{pb_ratio:.1f}，估值偏高")

    pe_ratio = _value("pe_ratio")
    if pe_ratio is not None:
        if pe_ratio <= 0:
            _add(negative_factors, "本益比為負(虧損股)")
        elif pe_ratio > 50:
            _add(negative_factors, f"本益比{pe_ratio:.1f}倍，估值極高（注意EPS縮水導致倍數虛胖）")
        elif pe_ratio > 25:
            _add(negative_factors, f"本益比{pe_ratio:.1f}倍，估值偏高")
        elif pe_ratio < 8:
            _add(positive_factors, f"本益比{pe_ratio:.1f}倍，估值偏低")

    price_vs_ma20 = _value("price_vs_ma20")
    if price_vs_ma20 is not None:
        pct = price_vs_ma20 * 100
        if pct > 5:
            _add(positive_factors, f"股價高於月線{pct:.1f}%，中期偏多")
        elif pct < -5:
            _add(negative_factors, f"股價跌破月線{abs(pct):.1f}%，中期偏空")

    risk_tags = str(row.get("risk_tags", "")).strip()
    if risk_tags:
        for tag in [part.strip() for part in risk_tags.split("⚠️") if part.strip()]:
            _add(negative_factors, f"風險提示：{tag}")

    return "；".join(positive_factors[:8]), "；".join(negative_factors[:6])


def _append_prediction_export_factors(df, snapshot=None):
    out = df.copy()
    out["positive_factor_text"] = ""
    out["negative_factor_text"] = ""

    if snapshot is None or getattr(snapshot, "empty", True):
        return out

    factor_cols = [
        col for col in _PREDICTION_EXPORT_FACTOR_SOURCE_COLUMNS
        if col in snapshot.columns
    ]
    if "ticker" not in factor_cols:
        return out

    factor_source = out.merge(snapshot[factor_cols], on="ticker", how="left")
    pos_values = []
    neg_values = []
    for row in factor_source.to_dict("records"):
        pos_text, neg_text = _build_prediction_export_factor_texts(row)
        pos_values.append(pos_text)
        neg_values.append(neg_text)

    out["positive_factor_text"] = pos_values
    out["negative_factor_text"] = neg_values
    return out


def _prepare_prediction_export_df(df, snapshot=None, direction_filter: str | None = None):
    import pandas as pd

    export_df = df.copy()
    if export_df.empty:
        export_df["prediction_direction"] = pd.Series(dtype="object")
    else:
        export_df["prediction_direction"] = export_df.apply(_prediction_direction_label, axis=1)

    if direction_filter:
        export_df = export_df[export_df["prediction_direction"] == direction_filter].copy()

    export_df = _append_prediction_export_factors(export_df, snapshot=snapshot)

    export_df = export_df.rename(
        columns={
            src: dst
            for src, dst in _PREDICTION_EXPORT_RENAME_MAP.items()
            if src in export_df.columns
        }
    )

    ordered_columns = [
        col for col in _PREDICTION_EXPORT_PRIORITY_COLUMNS if col in export_df.columns
    ]
    ordered_columns.extend(col for col in export_df.columns if col not in ordered_columns)
    return export_df.loc[:, ordered_columns]


def _prediction_csv_response(export_df, filename: str):
    csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/predictions/download/all", tags=["predictions"])
def download_all_predictions(top_n: int = 30):
    """匯出 ML 預測頁面榜單 CSV（預設 Top 30）"""
    try:
        df, _ = _get_live_prediction_df()
    except FileNotFoundError:
        return JSONResponse({"error": "尚無預測結果"}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e), "status": "warming"}, status_code=503)

    try:
        snapshot, _, _ = _get_prediction_context()
    except (FileNotFoundError, RuntimeError):
        snapshot = None

    leaderboard_df = _get_prediction_leaderboard_df(df, top_n=top_n)
    pred_date = str(df["date"].iloc[0]) if not df.empty else "unknown"
    export_df = _prepare_prediction_export_df(leaderboard_df, snapshot=snapshot)
    return _prediction_csv_response(export_df, f"predictions_top{top_n}_{pred_date}.csv")


@app.get("/api/predictions/download/bullish", tags=["predictions"])
def download_bullish_predictions():
    """匯出看多股票 CSV（依預測方向篩選）"""
    try:
        df, _ = _get_live_prediction_df()
    except FileNotFoundError:
        return JSONResponse({"error": "尚無預測結果"}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e), "status": "warming"}, status_code=503)

    try:
        snapshot, _, _ = _get_prediction_context()
    except (FileNotFoundError, RuntimeError):
        snapshot = None

    pred_date = str(df["date"].iloc[0]) if not df.empty else "unknown"
    export_df = _prepare_prediction_export_df(df, snapshot=snapshot, direction_filter="看多")
    return _prediction_csv_response(export_df, f"predictions_bullish_{pred_date}.csv")


@app.get("/api/predictions/download/bearish", tags=["predictions"])
def download_bearish_predictions():
    """匯出看空股票 CSV（依預測方向篩選）"""
    try:
        df, _ = _get_live_prediction_df()
    except FileNotFoundError:
        return JSONResponse({"error": "尚無預測結果"}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e), "status": "warming"}, status_code=503)

    try:
        snapshot, _, _ = _get_prediction_context()
    except (FileNotFoundError, RuntimeError):
        snapshot = None

    pred_date = str(df["date"].iloc[0]) if not df.empty else "unknown"
    export_df = _prepare_prediction_export_df(df, snapshot=snapshot, direction_filter="看空")
    return _prediction_csv_response(export_df, f"predictions_bearish_{pred_date}.csv")


@app.get("/api/predictions/{ticker}", tags=["predictions"])
def stock_prediction(ticker: str):
    """取得單支股票的 ML 預測"""
    import pandas as pd

    try:
        df, _ = _get_live_prediction_df()
    except FileNotFoundError:
        return {"error": "尚無預測結果", "ticker": ticker}

    except RuntimeError as e:
        return {"error": str(e), "status": "warming", "ticker": ticker}

    row = df[df["ticker"] == ticker]
    if row.empty:
        return _prediction_unavailable_response(ticker)

    r = row.iloc[0]
    raw_rank = int(row.index[0]) + 1

    # 過濾後排名：排除被硬擋降級的股票（和 Top N 列表一致）
    _hard_filter_prefix = "觀望（"  # 被硬擋的推薦都以 "觀望（" 開頭
    if "recommendation" in df.columns:
        normal_df = df[~df["recommendation"].str.startswith(_hard_filter_prefix, na=False)].reset_index(drop=True)
        normal_row = normal_df[normal_df["ticker"] == ticker]
        if not normal_row.empty:
            rank = int(normal_row.index[0]) + 1
            total_filtered = len(normal_df)
        else:
            # 該股自身也被硬擋，顯示原始排名
            rank = raw_rank
            total_filtered = len(df)
    else:
        rank = raw_rank
        total_filtered = len(df)

    _PHASE_NAMES = {0: "超賣低檔", 1: "盤整等待", 2: "強勢延續", 3: "過熱警示"}

    result = {
        "ticker": ticker,
        "rank": rank,
        "total": total_filtered,
        "close": round(float(r["close"]), 2) if pd.notna(r.get("close")) else None,
        "up_prob": round(float(r["up_prob"]), 4),
        "flat_prob": round(float(r["flat_prob"]), 4),
        "down_prob": round(float(r["down_prob"]), 4),
        "signal": r.get("signal", ""),
    }
    # v2 欄位
    if pd.notna(r.get("pred_return_20d")):
        result["pred_return_20d"] = round(float(r["pred_return_20d"]) * 100, 2)
        result["recommendation"] = str(r.get("recommendation", ""))
    for dim in ["技術面", "籌碼面", "基本面", "產業面"]:
        col = f"dim_{dim}"
        if pd.notna(r.get(col)):
            result[col] = round(float(r[col]), 4)
            # 百分位排名
            if col in df.columns:
                pct = df[col].rank(pct=True).loc[row.index[0]]
                if pd.notna(pct):
                    result[f"{col}_pct"] = round(float(pct) * 100, 1)
    # 全市場報酬語境
    if pd.notna(r.get("market_sentiment")):
        result["market_sentiment"] = str(r["market_sentiment"])
    if pd.notna(r.get("market_return_median")):
        result["market_return_median"] = round(float(r["market_return_median"]) * 100, 2)
    # 進場信號欄位
    if pd.notna(r.get("entry_score")):
        result["entry_score"] = round(float(r["entry_score"]), 1)
    if pd.notna(r.get("phase")):
        result["phase"] = _PHASE_NAMES.get(int(r["phase"]), "")
    if pd.notna(r.get("position_52w")):
        result["pos_52w"] = round(float(r["position_52w"]) * 100, 1)
    if pd.notna(r.get("price_vs_ma20")):
        result["overheat"] = round(float(r["price_vs_ma20"]) * 100, 1)
    if pd.notna(r.get("inst_cost_10d")):
        inst_cost = float(r["inst_cost_10d"])
        result["inst_cost"] = round(inst_cost, 2)
        close = float(r["close"]) if pd.notna(r.get("close")) else None
        if close and close > 0:
            result["inst_cost_diff"] = round((close / inst_cost - 1) * 100, 1)
    if pd.notna(r.get("inst_net_10d")):
        result["inst_net_10d"] = round(float(r["inst_net_10d"]) / 1000)  # 股→張
    if pd.notna(r.get("inst_vol_pct_10d")):
        result["inst_vol_pct"] = round(float(r["inst_vol_pct_10d"]), 1)
    # 外資持股比率
    foreign_map = _load_foreign_ownership()
    if ticker in foreign_map and pd.notna(foreign_map[ticker]):
        result["foreign_pct"] = round(float(foreign_map[ticker]), 2)
    if pd.notna(r.get("poc_20d")):
        result["poc_20d"] = round(float(r["poc_20d"]), 2)
    if pd.notna(r.get("va_low_20d")):
        result["va_low_20d"] = round(float(r["va_low_20d"]), 2)
    if pd.notna(r.get("va_high_20d")):
        result["va_high_20d"] = round(float(r["va_high_20d"]), 2)
    return result


@app.get("/api/pipeline/status", tags=["system"])
def pipeline_status():
    """回傳資料管線最新狀態，供前端顯示資料鮮度"""
    import json as _json
    from datetime import datetime as _dt
    status_path = os.path.join(os.path.dirname(__file__), "ml", "models", "pipeline_status.json")
    if not os.path.exists(status_path):
        return {"error": "尚無 pipeline 執行紀錄", "stale": True}
    with open(status_path, "r", encoding="utf-8") as f:
        status = _json.load(f)
    # 計算資料是否過期 (超過 24 小時視為過期)
    try:
        last_run = _dt.strptime(status["last_run"], "%Y-%m-%d %H:%M:%S")
        hours_ago = (_dt.now() - last_run).total_seconds() / 3600
        status["hours_since_update"] = round(hours_ago, 1)
        status["stale"] = hours_ago > 24
    except (KeyError, ValueError):
        status["stale"] = True
    return status


@app.get("/api/predictions/{ticker}/explain", tags=["predictions"])
def prediction_explain(ticker: str):
    """ML 預測解釋 — 用自然語言敘述為何看漲/看跌."""
    import numpy as np
    import pandas as pd
    from ml.dataset import load_single_stock, _load_twii
    from ml.features.institutional import INSTITUTIONAL_FEATURE_COLS
    from ml.predict import load_v2_model, _DIMENSION_MAP

    # 處置股硬擋（最優先）：不需要載入模型，直接回傳
    from ml.predict import _load_disposition_set
    if ticker in _load_disposition_set():
        return {
            "ticker": ticker,
            "recommendation": "觀望（處置股）",
            "domain_warnings": [
                "此股票目前處於主管機關處置期間，採分盤撮合交易（每20分鐘撮合一次），"
                "流動性極差，進場後難以迅速出場，系統強制觀望。"
            ],
            "narrative": "【處置股警示】此股票目前受主管機關處置，改為分盤交易，"
                         "每20分鐘才撮合一次，買賣價差極大，不建議操作。",
        }

    try:
        snapshot, model, meta = _get_prediction_context()
    except FileNotFoundError:
        return {"error": "尚無模型", "ticker": ticker}

    except RuntimeError as e:
        return {"error": str(e), "status": "warming", "ticker": ticker}

    feature_cols = meta["feature_columns"]
    twii_df = _load_twii()
    raw_df = load_single_stock(ticker, twii_df=twii_df)
    if raw_df is None or len(raw_df) == 0:
        return _prediction_unavailable_response(ticker)

    latest_rows = snapshot[snapshot["ticker"] == ticker]
    if latest_rows.empty:
        return _prediction_unavailable_response(ticker)
    latest = latest_rows.tail(1).copy()

    raw_latest = raw_df.iloc[[-1]].copy()
    ranked_inst_cols = {
        c for c in INSTITUTIONAL_FEATURE_COLS
        if "change" not in c and "ratio" not in c
    }

    # 嘗試 v2 迴歸模型
    v2_model, v2_meta = load_v2_model()
    use_v2 = v2_model is not None and v2_meta is not None

    if use_v2:
        v2_cols = v2_meta["feature_columns"]
        X = pd.DataFrame(columns=v2_cols)
        display_values = []
        for col in v2_cols:
            model_val = latest[col].values if col in latest.columns else [np.nan]
            X[col] = model_val
            if col in ranked_inst_cols and col in raw_latest.columns:
                raw_val = raw_latest[col].iloc[0]
                display_values.append(raw_val if not pd.isna(raw_val) else model_val[0])
            else:
                display_values.append(model_val[0])

        contribs = v2_model.predict(X.values, pred_contrib=True)[0]
        pred_return = v2_model.predict(X.values)[0]
        # 迴歸 pred_contrib: shape = (n_features + 1,)
        n_feat = len(v2_cols)
        feat_contribs = contribs[:n_feat]  # 正=推高報酬(看多), 負=壓低報酬(看空)
        feat_values = X.values[0]
        feat_display_values = np.array(display_values, dtype=object)
        active_feature_cols = v2_cols
        FEAT_GROUP = _DIMENSION_MAP  # 3 層次: 籌碼與波動引擎/總經與大盤環境/基本面防禦網

        # 推薦等級（與預測列表一致的百分位邏輯）
        try:
            df, _ = _get_live_prediction_df()
            row = df[df["ticker"] == ticker]
            recommendation = str(row.iloc[0].get("recommendation", "觀望")) if not row.empty else "觀望"
        except Exception:
            recommendation = "觀望"

        signal = "UP" if pred_return > 0.02 else "DOWN" if pred_return < -0.02 else "FLAT"
        is_regression = True
    else:
        X = pd.DataFrame(columns=feature_cols)
        display_values = []
        for col in feature_cols:
            model_val = latest[col].values if col in latest.columns else [np.nan]
            X[col] = model_val
            if col in ranked_inst_cols and col in raw_latest.columns:
                raw_val = raw_latest[col].iloc[0]
                display_values.append(raw_val if not pd.isna(raw_val) else model_val[0])
            else:
                display_values.append(model_val[0])

        contribs = model.predict(X.values, pred_contrib=True)[0]
        proba = model.predict(X.values)[0]
        signal_idx = int(np.argmax(proba))
        signal = ["DOWN", "FLAT", "UP"][signal_idx]

        n_classes = 3
        n_feat = len(feature_cols)
        contrib_matrix = contribs.reshape(n_feat + 1, n_classes)
        feat_contribs = contrib_matrix[:n_feat, signal_idx]
        feat_values = X.values[0]
        feat_display_values = np.array(display_values, dtype=object)
        active_feature_cols = feature_cols
        recommendation = None
        is_regression = False
        pred_return = None
        FEAT_GROUP = _DIMENSION_MAP  # 統一用 3 層次

    def _fmt_lots(v, unit="shares"):
        """格式化張數顯示。

        Parameters
        ----------
        v : float
            原始值（預設為股數）
        unit : str
            "shares" = 原始值是股數（API 原始單位），自動除 1000 轉張
            "lots" = 原始值已經是張數
        """
        if unit == "shares":
            av = abs(v) / 1000  # 股 → 張
        else:
            av = abs(v)
        if av >= 10_000:
            return f"{av/10_000:.1f}萬張"
        if av >= 100:
            return f"{av/1_000:.1f}千張"
        return f"{av:.1f}張"

    def _interpret(feat_name, is_missing, val, contrib, abs_contrib):
        """把特徵值翻譯成人話.

        Returns
        -------
        tuple (text, sentiment) or (None, None)
            text: 人類可讀的描述
            sentiment: "positive" / "negative" / None
                positive/negative = 基於財務常識的固有方向（不看 SHAP）
                None = 方向不明確，由 SHAP 決定（如技術指標）
        """
        # NaN → 一律不顯示。缺失資料不是投資理由。
        if is_missing:
            return None, None

        # --- 技術面 (sentiment=None: 方向不固定，由 SHAP 決定) ---
        if feat_name == "price_vs_ma5":
            pct = val * 100
            if pct > 3:    return f"股價高於5日均線{pct:.1f}%，短線偏強", "positive"
            if pct > 0:    return f"股價站在5日均線之上({pct:+.1f}%)", "positive"
            if pct > -3:   return f"股價略低於5日均線({pct:+.1f}%)", "negative"
            return f"股價跌破5日均線{abs(pct):.1f}%，短線轉弱", "negative"
        if feat_name == "price_vs_ma20":
            pct = val * 100
            if pct > 5:    return f"股價高於月線{pct:.1f}%，中期偏多", "positive"
            if pct > 0:    return f"股價在月線之上({pct:+.1f}%)", "positive"
            if pct > -5:   return f"股價略低於月線({pct:+.1f}%)", "negative"
            return f"股價跌破月線{abs(pct):.1f}%，中期偏空", "negative"
        if feat_name == "price_vs_ma60":
            pct = val * 100
            if pct > 0:    return f"股價站穩季線之上({pct:+.1f}%)，長線偏多", "positive"
            return f"股價跌破季線({pct:+.1f}%)，長線偏空", "negative"
        if feat_name == "ma_slope_5":
            if val > 0.01:   return "5日均線向上翹，短期趨勢上揚", "positive"
            if val < -0.01:  return "5日均線下彎，短期趨勢轉弱", "negative"
            return "5日均線走平，短期方向不明", None
        if feat_name == "ma_slope_20":
            if val > 0:  return "20日均線走升，中期趨勢向上", "positive"
            return "20日均線走跌，中期趨勢向下", "negative"
        if feat_name == "rsi_6":
            if val > 80:   return f"RSI(6)={val:.0f}，短線動能極度強勢（呈現軋空姿態）", None
            if val > 60:   return f"RSI(6)={val:.0f}，短線動能偏強", None
            if val > 40:   return f"RSI(6)={val:.0f}，短線動能中性", None
            if val > 20:   return f"RSI(6)={val:.0f}，短線動能偏弱", None
            return f"RSI(6)={val:.0f}，短線動能極度疲軟（超賣區）", None
        if feat_name == "rsi_14":
            if val > 70:   return f"RSI(14)={val:.0f}，中期動能強勢，趨勢延續中", None
            if val > 50:   return f"RSI(14)={val:.0f}，中期動能偏多", None
            if val > 30:   return f"RSI(14)={val:.0f}，中期動能偏弱", None
            return f"RSI(14)={val:.0f}，中期動能極弱（超賣區）", None
        if feat_name == "kd_k":
            if val > 80:   return f"K值={val:.0f}，進入高檔鈍化區", None
            if val < 20:   return f"K值={val:.0f}，進入低檔超賣區", None
            return f"K值={val:.0f}", None
        if feat_name == "kd_d":
            if val > 80:   return f"D值={val:.0f}，KD高檔注意死亡交叉", None
            if val < 20:   return f"D值={val:.0f}，KD低檔等待黃金交叉", None
            return None, None
        if feat_name == "macd_hist":
            if val > 0:  return f"MACD柱狀體為正({val:.3f})，多方動能持續", None
            return f"MACD柱狀體為負({val:.3f})，空方動能主導", None
        if feat_name == "bb_position":
            if val > 0.8:   return f"股價位於布林通道上緣({val:.0%})，短線壓力大", None
            if val < 0.2:   return f"股價位於布林通道下緣({val:.0%})，短線支撐近", None
            return None, None
        if feat_name.startswith("return_"):
            days = feat_name.replace("return_", "").replace("d", "")
            pct = val * 100
            if abs(pct) < 0.3:  return f"近{days}日持平", None
            # 漲跌有固有方向：漲=positive, 跌=negative
            # 即使模型認為「跌深反彈」，文字不可把暴跌當利多
            if pct > 10:   return f"近{days}日大漲{pct:.1f}%，短線動能強勁", "positive"
            if pct > 0:    return f"近{days}日上漲{pct:.1f}%", "positive"
            if pct > -10:  return f"近{days}日下跌{abs(pct):.1f}%", "negative"
            # 暴跌 > 10% 用特殊語句，明確標為利空
            return f"近{days}日暴跌{abs(pct):.1f}%，跌勢劇烈（模型捕捉到跌深反彈機會，但追價風險極高）", "negative"
        if feat_name.startswith("volatility_"):
            days = feat_name.replace("volatility_", "").replace("d", "")
            pct = val * 100
            if pct > 5:    return f"近{days}日波動劇烈({pct:.1f}%)，不確定性高", None
            if pct > 3:    return f"近{days}日波動偏大({pct:.1f}%)", None
            return None, None
        if feat_name == "vol_ratio_5_20":
            if val > 2:    return f"近5日成交量為20日均量的{val:.1f}倍，量能放大", None
            if val > 1.3:  return f"近5日量能略增(均量{val:.1f}倍)", None
            if val < 0.5:  return f"近5日成交量萎縮至均量{val:.0%}，交投清淡", None
            if val < 0.8:  return f"近5日量能偏低(均量{val:.0%})", None
            return None, None
        if feat_name == "high_low_range":
            pct = val * 100
            if pct > 5:  return f"今日振幅{pct:.1f}%，盤中震盪劇烈", None
            return None, None
        if feat_name == "gap_pct":
            pct = val * 100
            if pct > 1:    return f"今日跳空上漲{pct:.1f}%", None
            if pct < -1:   return f"今日跳空下跌{abs(pct):.1f}%", None
            return None, None
        if feat_name == "atr_14":
            return None, None

        # --- 籌碼面 (買超=positive, 賣超=negative) ---
        if feat_name in ("foreign_cumsum_5d", "foreign_cumsum_10d", "foreign_cumsum_20d"):
            days = feat_name.split("_")[-1].replace("d", "")
            lots = _fmt_lots(val)
            if val > 0:  return f"外資近{days}日累計買超{lots}", "positive"
            if val < 0:  return f"外資近{days}日累計賣超{lots}", "negative"
            return None, None
        if feat_name in ("trust_cumsum_5d", "trust_cumsum_10d", "trust_cumsum_20d"):
            days = feat_name.split("_")[-1].replace("d", "")
            lots = _fmt_lots(val)
            if val > 0:  return f"投信近{days}日累計買超{lots}", "positive"
            if val < 0:  return f"投信近{days}日累計賣超{lots}", "negative"
            return None, None
        if feat_name in ("dealer_cumsum_5d", "dealer_cumsum_10d"):
            days = feat_name.split("_")[-1].replace("d", "")
            lots = _fmt_lots(val)
            if val > 0:  return f"自營商近{days}日累計買超{lots}", "positive"
            if val < 0:  return f"自營商近{days}日累計賣超{lots}", "negative"
            return None, None
        if feat_name in ("inst_total_5d", "inst_total_10d", "inst_total_20d"):
            days = feat_name.split("_")[-1].replace("d", "")
            lots = _fmt_lots(val)
            if val > 0:  return f"三大法人近{days}日合計買超{lots}", "positive"
            if val < 0:  return f"三大法人近{days}日合計賣超{lots}", "negative"
            return None, None
        if feat_name == "foreign_trust_sync":
            if val > 0.5:   return "外資與投信同步買超，法人共識偏多", "positive"
            if val < -0.5:  return "外資與投信同步賣超，法人共識偏空", "negative"
            # 方向不一致 = 沒共識 = 中立偏空（不該算利多）
            if val > 0:     return "外資投信方向略同但力道不足", None
            return "外資投信方向分歧，法人無共識", "negative"
        if feat_name == "chip_diverge_bear":
            if val >= 1.0:  return f"頂部籌碼背離（{val:.1f}）：股價漲但法人倒貨+散戶融資追買", "negative"
            if val >= 0.3:  return f"籌碼背離初現（{val:.1f}）：法人賣超伴隨融資增加", "negative"
            return None, None
        if feat_name == "chip_diverge_bull":
            if val >= 1.0:  return f"底部籌碼背離（{val:.1f}）：股價跌但法人吃貨+散戶融資斷頭", "positive"
            if val >= 0.3:  return f"底部吸籌跡象（{val:.1f}）：法人逢低承接", "positive"
            return None, None
        if feat_name in ("margin_change_5d", "margin_change_10d"):
            days = feat_name.split("_")[-1].replace("d", "")
            # margin_change 是 pct_change（小數），不是張數
            pct = val * 100
            if pct > 5:     return f"近{days}日融資增加{pct:.1f}%，散戶追買", "negative"
            if pct < -5:    return f"近{days}日融資減少{abs(pct):.1f}%，散戶退場（籌碼沉澱）", "positive"
            return None, None
        if feat_name in ("short_change_5d", "short_change_10d"):
            days = feat_name.split("_")[-1].replace("d", "")
            pct = val * 100
            if pct > 5:     return f"近{days}日融券增加{pct:.1f}%，放空力道增加", "negative"
            if pct < -5:    return f"近{days}日融券減少{abs(pct):.1f}%，空方回補", "positive"
            return None, None
        if feat_name == "margin_short_ratio":
            # 融資券比高 = 散戶偏多 = 籌碼凌亂 = 利空（不管 SHAP 怎麼說）
            if val > 20:  return f"融資券比{val:.0f}倍，散戶極度偏多，籌碼凌亂", "negative"
            if val > 10:  return f"融資券比{val:.0f}倍，散戶偏多", "negative"
            return None, None

        # --- 基本面 (用財務常識判斷方向，不看 SHAP) ---
        if feat_name == "revenue_yoy_latest":
            pct = val * 100
            if pct > 20:    return f"最新月營收年增{pct:.1f}%，成長強勁", "positive"
            if pct > 0:     return f"最新月營收年增{pct:.1f}%", "positive"
            return f"最新月營收年減{abs(pct):.1f}%", "negative"
        if feat_name == "revenue_yoy_3m_avg":
            pct = val * 100
            if pct > 5:   return f"近3月營收年增率均值{pct:+.1f}%", "positive"
            if pct < -5:  return f"近3月營收年增率均值{pct:+.1f}%", "negative"
            return None, None
        if feat_name == "revenue_yoy_momentum":
            if val > 0.05:   return "月營收成長加速中", "positive"
            if val < -0.05:  return "月營收成長動能放緩", "negative"
            return None, None
        if feat_name == "revenue_cumulative_yoy":
            pct = val * 100
            if pct > 5:   return f"累計營收年增{pct:+.1f}%", "positive"
            if pct < -5:  return f"累計營收年增{pct:+.1f}%", "negative"
            return None, None
        if feat_name == "gross_margin_latest":
            pct = val * 100
            if pct > 40:   return f"毛利率{pct:.1f}%，獲利能力佳", "positive"
            if pct < 10:   return f"毛利率僅{pct:.1f}%，獲利空間有限", "negative"
            return None, None
        if feat_name == "operating_margin_latest":
            pct = val * 100
            if pct < 0:  return f"營業利益率{pct:.1f}%，本業虧損", "negative"
            if pct > 15: return f"營業利益率{pct:.1f}%，本業獲利佳", "positive"
            return None, None
        if feat_name == "net_margin_latest":
            pct = val * 100
            if pct < 0:  return f"淨利率{pct:.1f}%，公司虧損中", "negative"
            return None, None
        if feat_name == "margin_trend":
            if val > 0.03:   return "利潤率持續改善", "positive"
            if val < -0.03:  return "利潤率持續下滑", "negative"
            return None, None

        # --- 大盤環境 ---
        if feat_name == "twii_return_5d":
            pct = val * 100
            if abs(pct) < 0.3:  return None, None
            if pct > 0:  return f"大盤近5日漲{pct:.1f}%，整體偏多", "positive"
            return f"大盤近5日跌{abs(pct):.1f}%，整體偏空", "negative"
        if feat_name == "twii_return_20d":
            pct = val * 100
            if abs(pct) < 0.5:  return None, None
            if pct > 0:  return f"大盤近月漲{pct:.1f}%", "positive"
            return f"大盤近月跌{abs(pct):.1f}%", "negative"
        if feat_name == "vix_level":
            if val > 30:   return f"VIX={val:.1f}，市場極度恐慌", "negative"
            if val > 20:   return f"VIX={val:.1f}，市場偏謹慎", "negative"
            return f"VIX={val:.1f}，市場平穩", "positive"
        if feat_name == "vix_percentile_60d":
            pct = val * 100
            if pct > 80:   return f"VIX處近60日{pct:.0f}%高位，恐慌偏高", "negative"
            if pct < 20:   return f"VIX處近60日{pct:.0f}%低位，市場平穩", "positive"
            return f"VIX處近60日{pct:.0f}%分位", None
        if feat_name == "vix_ma20_ratio":
            if val > 1.2:  return f"VIX高於20日均值{(val-1)*100:.0f}%，恐慌突升", "negative"
            if val < 0.8:  return f"VIX低於20日均值{(1-val)*100:.0f}%，恐慌偏低", "positive"
            return None, None
        if feat_name == "vix_change_5d":
            if val > 3:    return f"VIX近5日上升{val:.1f}點，恐慌升溫", "negative"
            if val < -3:   return f"VIX近5日下降{abs(val):.1f}點，恐慌降溫", "positive"
            return None, None
        if feat_name == "sox_return_5d":
            pct = val * 100
            if abs(pct) < 0.5:  return None, None
            if pct > 0:  return f"費半近5日漲{pct:.1f}%，半導體轉強", "positive"
            return f"費半近5日跌{abs(pct):.1f}%，半導體轉弱", "negative"
        if feat_name == "usdtwd_change_5d":
            if val > 0.3:    return f"台幣近5日貶值{val:.2f}元", None
            if val < -0.3:   return f"台幣近5日升值{abs(val):.2f}元", None
            return None, None

        # --- 估值面 (高估=negative, 低估=positive，不因 SHAP 翻轉) ---
        if feat_name == "pe_ratio":
            if val <= 0:   return "本益比為負(虧損股)", "negative"
            if val > 50:   return f"本益比{val:.1f}倍，估值極高（注意EPS縮水導致倍數虛胖）", "negative"
            if val > 25:   return f"本益比{val:.1f}倍，估值偏高", "negative"
            if val < 8:    return f"本益比{val:.1f}倍，估值偏低", "positive"
            if val < 15:   return f"本益比{val:.1f}倍，估值合理偏低", "positive"
            return f"本益比{val:.1f}倍", None
        if feat_name == "pb_ratio":
            if val < 1:    return f"股價淨值比{val:.2f}，低於淨值", "positive"
            if val > 5:    return f"股價淨值比{val:.1f}，估值偏高", "negative"
            return f"股價淨值比{val:.2f}", None
        if feat_name == "dividend_yield":
            # API 回傳已是百分比形式 (5.38 = 5.38%)，不需再乘 100
            pct = val
            if pct > 6:    return f"殖利率{pct:.1f}%，高息吸引力強", "positive"
            if pct > 4:    return f"殖利率{pct:.1f}%", "positive"
            if pct > 0:    return f"殖利率{pct:.1f}%", None
            return None, None
        if feat_name == "pe_percentile_60d":
            pct = val * 100
            if pct > 80:   return f"PE處於60日內{pct:.0f}%高位，相對偏貴", "negative"
            if pct < 20:   return f"PE處於60日內{pct:.0f}%低位，相對便宜", "positive"
            return None, None
        if feat_name == "pb_change_20d":
            pct = val * 100
            if pct > 10:   return f"PB近20日上升{pct:.1f}%，估值攀升", None
            if pct < -10:  return f"PB近20日下降{abs(pct):.1f}%，估值回落", None
            return None, None

        # --- EPS (獲利事實=固有方向，不被 SHAP 翻轉) ---
        if feat_name == "eps_basic":
            if val > 0:  return f"最新單季EPS {val:.2f}元", "positive"
            if val < 0:  return f"最新單季EPS {val:.2f}元(虧損)", "negative"
            return None, None
        if feat_name == "eps_ttm":
            if val > 5:  return f"近四季EPS合計{val:.2f}元，獲利穩健", "positive"
            if val > 1:  return f"近四季EPS合計{val:.2f}元", "positive"
            if val > 0:  return f"近四季EPS合計{val:.2f}元，獲利偏低", "negative"
            if val < 0:  return f"近四季EPS合計{val:.2f}元，處於虧損", "negative"
            return None, None
        if feat_name == "eps_yoy":
            pct = val * 100
            # 極端值 = 低/高基期效應，不是真正的成長或衰退
            if pct > 500:    return f"EPS年增{pct:.0f}%（低基期效應，去年同期EPS極低，非實質成長）", "negative"
            if pct > 20:     return f"EPS年增{pct:.0f}%，獲利大幅成長", "positive"
            if pct > 0:      return f"EPS年增{pct:.0f}%", "positive"
            if pct < -500:   return f"EPS年減{abs(pct):.0f}%（高基期效應）", None
            if pct > -20:    return f"EPS年減{abs(pct):.0f}%", "negative"
            return f"EPS年減{abs(pct):.0f}%，獲利大幅衰退", "negative"
        if feat_name == "eps_qoq":
            pct = val * 100
            if abs(pct) > 20:
                if pct > 0:  return f"EPS季增{pct:.0f}%", "positive"
                return f"EPS季減{abs(pct):.0f}%", "negative"
            return None, None
        if feat_name == "eps_momentum":
            if val > 0.1:    return "EPS成長動能加速", "positive"
            if val < -0.1:   return "EPS成長動能減速", "negative"
            return None, None

        # --- 情緒面 (sentiment=None: SHAP 決定) ---
        if feat_name == "consecutive_days":
            if val >= 3:     return f"已連續上漲{val:.0f}天", None
            if val <= -3:    return f"已連續下跌{abs(val):.0f}天", None
            if val >= 1:     return f"連漲{val:.0f}天", None
            if val <= -1:    return f"連跌{abs(val):.0f}天", None
            return None, None
        if feat_name == "volume_surprise":
            if val > 3:      return f"成交量為均量的{val:.1f}倍，出現爆量", None
            if val > 1.5:    return f"成交量為均量{val:.1f}倍，量能偏大", None
            return None, None
        if feat_name == "gap_freq_10d":
            if val >= 5:     return f"近10日出現{val:.0f}次跳空，波動頻繁", None
            if val >= 3:     return f"近10日出現{val:.0f}次跳空", None
            return None, None
        if feat_name == "dist_from_20d_high":
            pct = val * 100
            if pct < -15:    return f"距20日高點已跌{abs(pct):.1f}%，跌幅深", None
            if pct < -5:     return f"距20日高點回落{abs(pct):.1f}%", None
            if abs(pct) < 2: return "接近20日高點", None
            return None, None
        if feat_name == "dist_from_20d_low":
            pct = val * 100
            if pct > 15:     return f"距20日低點已反彈{pct:.1f}%", None
            if pct > 5:      return f"距20日低點反彈{pct:.1f}%", None
            if abs(pct) < 2: return "接近20日低點", None
            return None, None
        if feat_name == "price_vol_divergence":
            if val > 0.5:    return "價漲量縮，上漲動能不足", None
            if val < -0.5:   return "價跌量增，賣壓沉重", None
            return None, None
        if feat_name == "upper_wick_ratio":
            if val > 0.5:    return "上影線明顯，上方賣壓重", None
            return None, None
        if feat_name == "lower_wick_ratio":
            if val > 0.5:    return "下影線明顯，下方有承接", None
            return None, None

        # --- 產業面 ---
        if feat_name == "sector_return_rank":
            if val < 0.2:    return "所屬產業報酬排名前段班", "positive"
            if val > 0.8:    return "所屬產業報酬排名後段班", "negative"
            return None, None
        if feat_name in ("sector_relative_return_5d", "sector_relative_return_20d"):
            days = "5" if "5d" in feat_name else "20"
            pct = val * 100
            if pct > 2:      return f"近{days}日表現優於同產業{pct:.1f}%", "positive"
            if pct < -2:     return f"近{days}日落後同產業{abs(pct):.1f}%", "negative"
            return None, None
        if feat_name in ("sector_avg_return_5d", "sector_avg_return_20d"):
            days = "5" if "5d" in feat_name else "月"
            pct = val * 100
            if abs(pct) < 0.3:  return None, None
            if pct > 0:  return f"所屬產業近{days}日整體漲{pct:.1f}%", "positive"
            return f"所屬產業近{days}日整體跌{abs(pct):.1f}%", "negative"
        if feat_name == "sector_breadth":
            if val > 0.7:    return f"產業內{val:.0%}個股上漲，板塊熱絡", "positive"
            if val < 0.3:    return f"產業內僅{val:.0%}個股上漲，板塊低迷", "negative"
            return None, None
        if feat_name in ("sector_momentum_5d", "sector_momentum_20d"):
            days = "短期" if "5d" in feat_name else "中期"
            if val > 0.05:   return f"產業{days}動能增強", "positive"
            if val < -0.05:  return f"產業{days}動能減弱", "negative"
            return None, None

        return None, None

    # 建立排序索引 (按貢獻絕對值)
    sorted_indices = np.argsort(np.abs(feat_contribs))[::-1]

    # v2 迴歸: contrib > 0 = 推高報酬 = 利多, contrib < 0 = 壓低報酬 = 利空
    # v1 分類: signal=DOWN 時 contrib>0 意味「支持 DOWN」= 利空
    is_down_signal = (not is_regression) and (signal == "DOWN")

    # 收集有意義的解釋句子，按影響力排序
    bullish_reasons = []   # 利多因素
    bearish_reasons = []   # 利空因素

    for idx in sorted_indices:
        col = active_feature_cols[idx]
        model_val = feat_values[idx]
        display_val = feat_display_values[idx]
        is_missing = pd.isna(model_val)
        val = float(display_val) if not pd.isna(display_val) else None
        c = float(feat_contribs[idx])
        group = FEAT_GROUP.get(col, "籌碼與波動引擎")

        abs_c = abs(c)
        market_dir = -c if is_down_signal else c

        desc, sentiment = _interpret(col, is_missing, val, c, abs_c)
        if desc is None:
            continue

        item = {
            "text": desc,
            "group": group,
            "contribution": round(c, 4),
            "is_missing_data": is_missing,
            "sentiment": sentiment,
        }
        item["market_dir"] = market_dir

        if abs(c) > 0.003:
            # 分類邏輯：有明確財務方向時用 sentiment，否則用 SHAP
            if sentiment == "positive":
                bullish_reasons.append(item)
            elif sentiment == "negative":
                bearish_reasons.append(item)
            elif market_dir > 0:
                bullish_reasons.append(item)
            else:
                bearish_reasons.append(item)

    # 分組統計：用各面向所有特徵的 market_dir 加總
    group_contrib_raw = {}
    for i in range(n_feat):
        col = active_feature_cols[i]
        group = FEAT_GROUP.get(col, "籌碼與波動引擎")
        raw_c = float(feat_contribs[i])
        md = -raw_c if is_down_signal else raw_c
        group_contrib_raw[group] = group_contrib_raw.get(group, 0) + md

    group_contrib = group_contrib_raw  # 已是市場方向
    sorted_groups = sorted(group_contrib.items(), key=lambda x: abs(x[1]), reverse=True)

    # ===================================================================
    # 第二層：財務常識防呆過濾器 (Domain Logic Guardrail)
    # 交叉檢查：當多個基本面指標同時惡化時，強制修正分類
    # ===================================================================
    _feat_lookup = {}
    for i, col in enumerate(active_feature_cols):
        v = feat_values[i]
        if not pd.isna(v):
            _feat_lookup[col] = float(v)

    op_margin = _feat_lookup.get("operating_margin_latest")
    op_margin_pct = op_margin * 100 if op_margin is not None else None
    has_operating_loss = op_margin_pct is not None and op_margin_pct < 0
    eps_ttm_val = _feat_lookup.get("eps_ttm")
    pe_val = _feat_lookup.get("pe_ratio")
    eps_yoy_val = _feat_lookup.get("eps_yoy")
    turnaround = _prediction_turnaround_signals(_feat_lookup)
    turnaround_active = turnaround["active"]

    domain_warnings = []  # 衝突警示

    def _append_manual_reason(target, text, group, sentiment):
        if any(item["text"] == text for item in bullish_reasons + bearish_reasons):
            return
        target.append(
            {
                "text": text,
                "group": group,
                "contribution": 0.0,
                "is_missing_data": False,
                "sentiment": sentiment,
                "market_dir": 0.0,
            }
        )

    if turnaround_active:
        _append_manual_reason(
            bullish_reasons,
            "單季獲利、營收與利潤率同步改善，屬轉機修復訊號",
            "基本面防禦網",
            "positive",
        )
        if eps_ttm_val is not None and eps_ttm_val < 0:
            _append_manual_reason(
                bearish_reasons,
                f"近四季EPS仍為{eps_ttm_val:.2f}元，尚未完全轉盈",
                "基本面防禦網",
                "negative",
            )

    if has_operating_loss:
        # 盈餘品質過濾器：本業虧損 + EPS 年增 → 低基期/業外收入
        if eps_yoy_val is not None and eps_yoy_val > 0:
            if turnaround_active:
                domain_warnings.append(
                    "本業仍虧損，但營收與獲利動能同步改善，EPS改善宜解讀為虧損收斂與轉機修復"
                )
                for item in bullish_reasons:
                    if "EPS成長動能" in item["text"] or "EPS年增" in item["text"]:
                        item["text"] += "（但目前仍屬虧損收斂階段）"
            else:
                domain_warnings.append(
                    "本業虧損但EPS年增，獲利可能來自業外收入或低基期效應，不具持續性"
                )
                # 把 eps_momentum / eps_yoy 從利多移到利空
                for item in list(bullish_reasons):
                    if "EPS成長動能" in item["text"] or "EPS年增" in item["text"]:
                        item["text"] += "（但本業虧損，可能為業外收入）"
                        bullish_reasons.remove(item)
                        bearish_reasons.append(item)

    # EPS 語意覆寫：EPS TTM < 0 但 EPS YoY > 0 → 「虧損收斂」不是「獲利成長」
    if eps_ttm_val is not None and eps_ttm_val < 0:
        for item in list(bullish_reasons):
            if "EPS年增" in item["text"] and "獲利" in item["text"]:
                pct_str = item["text"].split("EPS年增")[1].split("%")[0] if "EPS年增" in item["text"] else ""
                if turnaround_active:
                    item["text"] = f"EPS年增{pct_str}%，但仍在虧損中（虧損收斂 / 轉機修復）"
                else:
                    item["text"] = f"EPS年增{pct_str}%，但仍在虧損中（虧損收斂，非實質獲利成長）"
                    item["sentiment"] = "negative"
                    bullish_reasons.remove(item)
                    bearish_reasons.append(item)

    if has_operating_loss:
        # 估值虛胖過濾器：本業虧損 + PE > 50 → EPS 分母趨近零
        if pe_val is not None and pe_val > 50:
            domain_warnings.append(
                f"本益比{pe_val:.0f}倍為EPS趨近零導致的估值虛胖，非市場看好"
            )

    # EPS TTM 極低 + PE 極高 = 典型的「估值虛胖」
    # 但若本業有賺錢且具備轉機條件，可豁免警示
    if (eps_ttm_val is not None and 0 < eps_ttm_val < 1
            and pe_val is not None and pe_val > 50):
        if not has_operating_loss:  # 本業虧損永遠不豁免
            rev_yoy_3m = _feat_lookup.get("revenue_yoy_3m_avg")
            gross_margin = _feat_lookup.get("gross_margin_latest")
            pb_val = _feat_lookup.get("pb_ratio")
            rev_yoy_latest = _feat_lookup.get("revenue_yoy_latest")

            # 豁免條款 1：營收動能豁免 — 近 3 月營收年增 > 30%
            if rev_yoy_3m is not None and rev_yoy_3m > 0.30:
                domain_warnings.append(
                    f"近四季EPS僅{eps_ttm_val:.2f}元導致PE {pe_val:.0f}倍失真，"
                    f"但近3月營收年增達{rev_yoy_3m*100:.1f}%，具轉機動能，豁免估值警示"
                )
            # 豁免條款 2：本業獲利結構豁免 — 毛利率 > 20% 且營業利益率為正
            elif (gross_margin is not None and gross_margin > 0.20
                  and op_margin is not None and op_margin > 0):
                domain_warnings.append(
                    f"近四季EPS僅{eps_ttm_val:.2f}元導致PE {pe_val:.0f}倍失真，"
                    f"但毛利率{gross_margin*100:.1f}%、營業利益率{op_margin*100:.1f}%，"
                    "本業具競爭力，豁免估值警示"
                )
            # 豁免條款 3：資產保護傘豁免 — PB < 1.5 倍，下檔有撐
            elif pb_val is not None and pb_val < 1.5:
                domain_warnings.append(
                    f"近四季EPS僅{eps_ttm_val:.2f}元導致PE {pe_val:.0f}倍失真，"
                    f"但股價淨值比僅{pb_val:.2f}倍，下檔具資產保護，免除估值過高警示"
                )
            # 無豁免條件：維持原始警告
            else:
                domain_warnings.append(
                    f"近四季EPS僅{eps_ttm_val:.2f}元，本益比{pe_val:.0f}倍"
                    "為獲利偏低導致的倍數虛胖"
                )

    # PE 矛盾修正：PE > 50 時，pe_percentile_60d「低位」只是從極高掉到很高
    # 此時不應顯示「相對便宜」的利多，強制移到利空
    if pe_val is not None and pe_val > 50:
        for item in bullish_reasons[:]:
            if "PE處於" in item["text"] and "低位" in item["text"]:
                item["text"] = item["text"].replace("相對便宜", f"但絕對值仍極高（{pe_val:.0f}倍）")
                item["sentiment"] = "negative"
                bullish_reasons.remove(item)
                bearish_reasons.append(item)

    # 流動性過濾：取 VOL_MA_5 和當日 Volume 中較低者
    # 避免單日放量拉高均值、也避免單日異常低估
    _volume_raw = None
    _vol_ma5 = None
    _vol_today = None
    if "VOL_MA_5" in latest.columns:
        _v = latest["VOL_MA_5"].iloc[0]
        if not pd.isna(_v):
            _vol_ma5 = float(_v)
    if "Volume" in latest.columns:
        _v = latest["Volume"].iloc[0]
        if not pd.isna(_v):
            _vol_today = float(_v)
    # 取較低者（更保守）
    _vol_candidates = [v for v in [_vol_ma5, _vol_today] if v is not None]
    if _vol_candidates:
        _volume_raw = min(_vol_candidates)
    if _volume_raw is not None and _volume_raw < 500_000:
        _vol_label = f"當日成交量{int(_vol_today/1000)}張" if _vol_today and _vol_today < 500_000 else f"近5日均量{int(_vol_ma5/1000)}張"
        domain_warnings.append(
            f"{_vol_label}，流動性嚴重不足，"
            "買賣價差大且不易出場"
        )

    # 異常暴跌過濾：短期跌幅過大可能代表重大利空
    _ret_20d = _feat_lookup.get("return_20d")
    if _ret_20d is not None and _ret_20d < -0.40:
        domain_warnings.append(
            f"近20日跌幅{_ret_20d*100:.0f}%，可能有重大利空事件，"
            "技術面超跌反彈訊號不足以作為買進依據"
        )

    # 極端乖離過濾：股價大幅偏離均線 → 回歸均值風險
    _price_vs_ma20 = _feat_lookup.get("price_vs_ma20")
    if _price_vs_ma20 is not None:
        if _price_vs_ma20 > RECOMMENDATION_OVERHEAT_THRESHOLD:
            domain_warnings.append(
                f"股價高於20日均線{_price_vs_ma20*100:.0f}%，短線乖離過大，"
                "追高風險高，均值回歸壓力大"
            )
        elif _price_vs_ma20 < -RECOMMENDATION_OVERHEAT_THRESHOLD:
            domain_warnings.append(
                f"股價低於20日均線{abs(_price_vs_ma20)*100:.0f}%，乖離過大，"
                "超跌可能反映重大利空而非買點"
            )

    # 籌碼頂部背離過濾：主力出貨 + 散戶融資追買 = 頂部訊號
    _chip_bear = _feat_lookup.get("chip_diverge_bear")
    if _chip_bear is not None and _chip_bear >= 1.0:
        domain_warnings.append(
            f"籌碼頂部背離分數{_chip_bear:.1f}：股價上漲但法人持續倒貨、"
            "散戶融資追買，典型主力出貨格局，追高風險極大"
        )

    # 籌碼底部背離提示：法人吃貨 + 散戶斷頭 = 可能築底
    _chip_bull = _feat_lookup.get("chip_diverge_bull")
    _chip_bull_tag = ""
    if _chip_bull is not None and _chip_bull >= 1.0:
        _chip_bull_tag = (
            f"💡 底部籌碼背離（{_chip_bull:.1f}）：股價下跌但法人逢低吃貨、"
            "散戶融資斷頭，主力可能暗中佈局"
        )

    # ===================================================================

    # ===================================================================
    # 三層次動能解析：敘事生成
    # Layer 1: 籌碼與波動引擎（主導推升力道）
    # Layer 2: 總經與大盤環境（系統性風險評估）
    # Layer 3: 基本面防禦網（下檔風險檢驗）
    # ===================================================================
    narrative_parts = []

    # 收集每組中有解釋的句子
    group_sentences = {}
    for item in bullish_reasons + bearish_reasons:
        g = item["group"]
        if g not in group_sentences:
            group_sentences[g] = []
        group_sentences[g].append(item)

    def _top_real_items(group_name, limit=3):
        items = group_sentences.get(group_name, [])
        real = [r for r in items if not r.get("is_missing_data")]
        return sorted(real, key=lambda x: abs(x.get("market_dir", 0)),
                       reverse=True)[:limit]

    def _conflict_check(group_name):
        """偵測模型方向 vs 個別事實方向的矛盾"""
        items = group_sentences.get(group_name, [])
        real = [r for r in items if not r.get("is_missing_data")]
        gd = group_contrib.get(group_name, 0)
        n_pos = sum(1 for it in real if it.get("sentiment") == "positive")
        n_neg = sum(1 for it in real if it.get("sentiment") == "negative")
        return gd > 0 and n_neg > n_pos

    # --- Layer 1: 籌碼與波動引擎 ---
    _L1_NAME = "籌碼與波動引擎"
    l1_dir = group_contrib.get(_L1_NAME, 0)
    l1_top = _top_real_items(_L1_NAME, 3)

    _atr_pct = _feat_lookup.get("atr_pct")
    _trust_streak = _feat_lookup.get("trust_buy_streak") or 0
    _foreign_streak = _feat_lookup.get("foreign_buy_streak") or 0
    _turnover = _feat_lookup.get("turnover_rate")
    _ft_sync = _feat_lookup.get("foreign_trust_sync") or 0

    if l1_top:
        high_vol = _atr_pct is not None and _atr_pct > 0.05
        low_vol = _atr_pct is not None and _atr_pct < 0.02
        inst_buying = _trust_streak >= 3 or _foreign_streak >= 3
        low_turnover = _turnover is not None and _turnover < 1.0

        # 籌碼背離優先敘事（比一般模式優先級更高）
        if _chip_bear is not None and _chip_bear >= 1.0:
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(
                f"【{_L1_NAME}・🚨 籌碼頂部背離】"
                f"背離分數{_chip_bear:.1f}：股價上漲但法人持續倒貨，"
                f"散戶融資追買接刀，典型主力出貨格局。{details}。"
            )
        elif _chip_bull_tag and _chip_bull is not None and _chip_bull >= 1.0:
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(
                f"【{_L1_NAME}・💡 底部籌碼背離】"
                f"背離分數{_chip_bull:.1f}：股價下跌但法人逢低吃貨，"
                f"散戶融資斷頭，主力可能暗中佈局。{details}。"
            )
        elif _conflict_check(_L1_NAME):
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(
                f"【{_L1_NAME}・⚠️ 訊號矛盾】模型給予正向權重，"
                f"但數據偏空：{details}。"
            )
        elif high_vol and inst_buying:
            streak_parts = []
            if _trust_streak >= 3:
                streak_parts.append(f"投信連續買超{int(_trust_streak)}日")
            if _foreign_streak >= 3:
                streak_parts.append(f"外資連續買超{int(_foreign_streak)}日")
            headline = (
                f"模型偵測到強烈的籌碼與價量共振。"
                f"波動率(ATR%)達{_atr_pct*100:.1f}%顯著放大，"
                f"{'、'.join(streak_parts)}，觸發法人認養推升權重"
            )
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(f"【{_L1_NAME}】{headline}。{details}。")
        elif low_vol and low_turnover:
            headline = (
                f"波動率(ATR%)僅{_atr_pct*100:.1f}%"
                f"{'、周轉率' + f'{_turnover:.2f}%' if _turnover else ''}"
                "，均處於低位。缺乏資金動能，短期維持橫盤整理"
            )
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(f"【{_L1_NAME}】{headline}。{details}。")
        else:
            # 一般情境：顯示關鍵指標 + 方向
            metrics = []
            if _atr_pct is not None:
                metrics.append(f"ATR%={_atr_pct*100:.1f}%")
            if _trust_streak >= 1:
                metrics.append(f"投信連買{int(_trust_streak)}日")
            if _foreign_streak >= 1:
                metrics.append(f"外資連買{int(_foreign_streak)}日")
            if _turnover is not None:
                metrics.append(f"周轉率{_turnover:.2f}%")
            direction = "偏多" if l1_dir > 0 else "偏空"
            metric_str = f"（{'，'.join(metrics)}）" if metrics else ""
            details = "；".join([it["text"] for it in l1_top])
            narrative_parts.append(
                f"【{_L1_NAME}・{direction}】"
                f"籌碼與價量動能{direction}{metric_str}。{details}。"
            )

    # --- Layer 2: 總經與大盤環境 ---
    _L2_NAME = "總經與大盤環境"
    l2_dir = group_contrib.get(_L2_NAME, 0)
    l2_top = _top_real_items(_L2_NAME, 3)

    _vix_pct = _feat_lookup.get("vix_percentile_60d")
    _vix = _feat_lookup.get("vix_level")  # 向後兼容舊模型
    _twii_20d = _feat_lookup.get("twii_return_20d")
    _twii_5d = _feat_lookup.get("twii_return_5d")
    _usdtwd = _feat_lookup.get("usdtwd_change_5d")

    if l2_top:
        # 優先用百分位判斷，退回到絕對值（舊模型）
        if _vix_pct is not None:
            vix_high = _vix_pct > 0.80
            vix_low = _vix_pct < 0.20
        else:
            vix_high = _vix is not None and _vix > 25
            vix_low = _vix is not None and _vix < 15
        twii_bull = _twii_20d is not None and _twii_20d > 0.05
        twd_weak = _usdtwd is not None and _usdtwd > 0.01

        if _conflict_check(_L2_NAME):
            details = "；".join([it["text"] for it in l2_top])
            narrative_parts.append(
                f"【{_L2_NAME}・⚠️ 訊號矛盾】模型給予正向權重，"
                f"但數據偏空：{details}。"
            )
        elif vix_high or twd_weak:
            alarm = []
            if vix_high:
                if _vix_pct is not None:
                    alarm.append(f"VIX處近60日{_vix_pct*100:.0f}%高位")
                elif _vix is not None:
                    alarm.append(f"VIX指數飆升至{_vix:.1f}")
            if twd_weak:
                alarm.append("台幣承壓貶值")
            headline = (
                f"{'且'.join(alarm)}。系統性風險升溫，"
                "模型啟動防禦機制，全面調降個股預估報酬"
            )
            details = "；".join([it["text"] for it in l2_top])
            narrative_parts.append(f"【{_L2_NAME}】{headline}。{details}。")
        elif twii_bull and vix_low:
            vix_desc = f"VIX處低位({_vix_pct*100:.0f}%分位)" if _vix_pct is not None else (f"VIX={_vix:.1f}" if _vix is not None else "VIX偏低")
            headline = (
                f"大盤月線報酬{_twii_20d*100:+.1f}%"
                f"且{vix_desc}穩定偏低。"
                "系統性風險低，模型給予個股突破更高勝率權重"
            )
            details = "；".join([it["text"] for it in l2_top])
            narrative_parts.append(f"【{_L2_NAME}】{headline}。{details}。")
        else:
            direction = "偏正面" if l2_dir > 0 else "偏保守"
            verb = "有利" if l2_dir > 0 else "壓抑"
            details = "；".join([it["text"] for it in l2_top])
            narrative_parts.append(
                f"【{_L2_NAME}・{direction}】"
                f"總經環境{direction}，{verb}個股表現。{details}。"
            )

    # --- Layer 3: 基本面防禦網 ---
    _L3_NAME = "基本面防禦網"
    l3_dir = group_contrib.get(_L3_NAME, 0)
    l3_top = _top_real_items(_L3_NAME, 3)

    if l3_top:
        if _conflict_check(_L3_NAME):
            details = "；".join([it["text"] for it in l3_top])
            narrative_parts.append(
                f"【{_L3_NAME}・⚠️ 訊號矛盾】模型給予正向權重，"
                f"但數據偏空：{details}。"
            )
        elif pe_val is not None and pe_val >= 500:
            headline = (
                "本業虧損或估值極度異常（PE虛胖）。"
                "缺乏實質獲利支撐，已觸發財務懲罰機制"
            )
            details = "；".join([it["text"] for it in l3_top])
            narrative_parts.append(f"【{_L3_NAME}・⚠️ 警示】{headline}。{details}。")
        elif has_operating_loss:
            if turnaround_active:
                headline = (
                    f"營業利益率{op_margin_pct:.1f}%，本業仍虧損。"
                    "但單季獲利、營收與利潤率同步改善，屬轉機修復期"
                )
            else:
                headline = (
                    f"營業利益率{op_margin_pct:.1f}%，本業虧損。"
                    "基本面形成下檔風險，削弱籌碼動能可信度"
                )
            details = "；".join([it["text"] for it in l3_top])
            narrative_parts.append(f"【{_L3_NAME}・⚠️ 警示】{headline}。{details}。")
        elif (pe_val is not None and 0 < pe_val < 30
              and eps_ttm_val is not None and eps_ttm_val > 0):
            headline = (
                f"本益比{pe_val:.1f}倍落在合理區間，"
                f"EPS={eps_ttm_val:.2f}元具備實質獲利。"
                "未觸發懲罰機制，下檔具基本面保護"
            )
            details = "；".join([it["text"] for it in l3_top])
            narrative_parts.append(f"【{_L3_NAME}・安全】{headline}。{details}。")
        else:
            direction = "無重大風險" if l3_dir >= 0 else "存在疑慮"
            details = "；".join([it["text"] for it in l3_top])
            narrative_parts.append(
                f"【{_L3_NAME}・{direction}】{details}。"
            )

    # 加入財務常識警示
    if domain_warnings:
        narrative_parts.append(
            "⚠️ 系統綜合警示：" + "；".join(domain_warnings) + "。"
        )

    narrative = "\n".join(narrative_parts) if narrative_parts else "資料不足，無法產生具體分析。"

    # 前端用的精簡版 positive/negative factors：缺值型理由不回傳
    def _sort_reasons(reasons, limit):
        real = [r for r in reasons if not r.get("is_missing_data")]
        sorted_r = sorted(real, key=lambda x: abs(x.get("market_dir", 0)), reverse=True)
        return [
            {"text": r["text"], "group": r["group"], "contribution": r["contribution"]}
            for r in sorted_r[:limit]
        ]
    positive_factors = _sort_reasons(bullish_reasons, 8)
    negative_factors = _sort_reasons(bearish_reasons, 6)

    result = {
        "ticker": ticker,
        "signal": signal,
        "narrative": narrative,
        "positive_factors": positive_factors,
        "negative_factors": negative_factors,
        "group_contributions": {k: round(v, 4) for k, v in sorted_groups},
        "is_v2": is_regression,
    }
    if domain_warnings:
        result["domain_warnings"] = domain_warnings
    if is_regression:
        ret_pct = round(float(pred_return) * 100, 2)
        result["pred_return_20d"] = ret_pct

        # === V2.1 風險標籤：寬進嚴選，風險以警示為主 ===
        _risk_tags = []

        if _volume_raw is not None and float(_volume_raw) < 500_000:
            _risk_tags.append("⚠️流動性不足")
        if _ret_20d is not None and _ret_20d < -0.40:
            _risk_tags.append("⚠️異常暴跌")
        if (
            _price_vs_ma20 is not None
            and abs(_price_vs_ma20) > RECOMMENDATION_OVERHEAT_THRESHOLD
        ):
            _risk_tags.append("⚠️短線偏熱，追價風險高" if _price_vs_ma20 > 0 else "⚠️乖離過大(超跌)")
        if has_operating_loss:
            _risk_tags.append("⚠️本業仍虧損但營運修復中" if turnaround_active else "⚠️本業虧損")
        if (
            (_prediction_institutional_sell_pressure(_feat_lookup) or 0.0)
            <= -(STRONG_BUY_MAX_INST_SELL_PCT / 100.0)
        ):
            _risk_tags.append(f"⚠️法人10日賣壓占量偏高（>{STRONG_BUY_MAX_INST_SELL_PCT:.0f}%）")
        if (
            (_prediction_institutional_sell_pressure(_feat_lookup) or 0.0)
            <= -(TOP30_EXCLUDE_INST_SELL_PCT / 100.0)
        ):
            _risk_tags.append(f"⚠️法人10日賣壓占量過高（>{TOP30_EXCLUDE_INST_SELL_PCT:.0f}%），不列入Top30")
        if _chip_bear is not None and _chip_bear >= 1.0:
            _risk_tags.append("⚠️籌碼頂部背離")

        if _risk_tags:
            result["risk_tags"] = " ".join(_risk_tags)

        if str(recommendation) == "強力買進":
            _strong_buy_invalid = False
            if _price_vs_ma20 is None or _price_vs_ma20 > RECOMMENDATION_OVERHEAT_THRESHOLD:
                _strong_buy_invalid = True
            if (
                _prediction_institutional_sell_pressure(_feat_lookup) is None
                or _prediction_institutional_sell_pressure(_feat_lookup)
                <= -(STRONG_BUY_MAX_INST_SELL_PCT / 100.0)
            ):
                _strong_buy_invalid = True
            if _volume_raw is not None and float(_volume_raw) < 500_000:
                _strong_buy_invalid = True
            if _ret_20d is not None and _ret_20d < -0.40:
                _strong_buy_invalid = True
            if has_operating_loss:
                _strong_buy_invalid = True
            if _strong_buy_invalid:
                result["recommendation_downgraded"] = True
                result["original_recommendation"] = str(recommendation)
                recommendation = "建議買進"

        result["recommendation"] = recommendation

        # 底部籌碼背離提示（非硬擋，純提示）
        if _chip_bull_tag:
            result["chip_diverge_bull_tag"] = _chip_bull_tag

        # 合理性檢查：20天報酬超過 ±50% 幾乎不可能，標記異常
        if abs(ret_pct) > 50:
            result["pred_warning"] = (
                f"預測報酬 {ret_pct:+.1f}% 超出合理範圍，"
                "可能因模型版本與資料不一致，建議重新執行預測。"
            )
    else:
        result["up_prob"] = round(float(proba[2]), 4)
        result["flat_prob"] = round(float(proba[1]), 4)
        result["down_prob"] = round(float(proba[0]), 4)
    return result


# ==============================================================================
# 模型信任度 API
# ==============================================================================
@app.get("/api/model/accuracy", tags=["model"])
def model_accuracy():
    """模型歷史預測驗證結果 — 建立信任的唯一方式是看盲測成績."""
    from ml.config import REPORT_DIR
    path = os.path.join(REPORT_DIR, "prediction_tracking.json")
    if not os.path.exists(path):
        return {
            "status": "no_data",
            "message": "尚無歷史驗證資料。預測需滿 20 個交易日後才能驗證。",
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "status": "ok",
        "summary": data.get("summary", {}),
        "verified_periods": len(data.get("verified", [])),
        "details": data.get("verified", []),
    }


# ==============================================================================
# 大盤紅綠燈 (Market Regime Filter)
# ==============================================================================
@app.get("/api/market/regime", tags=["market"])
def market_regime():
    """大盤多空燈號 — ML 預測的保護傘。

    綠燈：正常進場
    黃燈：減碼操作（建議資金部位砍半）
    紅燈：禁止進場（大盤系統性風險過高）
    """
    import pandas as pd
    import numpy as np
    from ml.config import INDEX_DIR

    indicators = {}
    reasons = []

    # --- 載入大盤指數 ---
    twii_path = os.path.join(INDEX_DIR, "index_TWII.csv")
    vix_path = os.path.join(INDEX_DIR, "index_VIX.csv")
    sox_path = os.path.join(INDEX_DIR, "index_SOX.csv")

    try:
        twii = pd.read_csv(twii_path, dtype={"Date": str})
        twii["Date"] = pd.to_datetime(twii["Date"])
        twii = twii.sort_values("Date").tail(60)
        twii_close = twii["Close"].values
        twii_latest = float(twii_close[-1])
        twii_ma20 = float(np.mean(twii_close[-20:]))
        twii_ma5 = float(np.mean(twii_close[-5:]))
        twii_5d_ret = (twii_close[-1] / twii_close[-6] - 1) * 100 if len(twii_close) >= 6 else 0
        twii_1d_ret = (twii_close[-1] / twii_close[-2] - 1) * 100 if len(twii_close) >= 2 else 0
        twii_vs_ma20 = (twii_latest / twii_ma20 - 1) * 100

        indicators["twii"] = round(twii_latest, 0)
        indicators["twii_ma20"] = round(twii_ma20, 0)
        indicators["twii_vs_ma20"] = round(twii_vs_ma20, 2)
        indicators["twii_5d_ret"] = round(twii_5d_ret, 2)
        indicators["twii_1d_ret"] = round(twii_1d_ret, 2)
        indicators["twii_date"] = twii["Date"].iloc[-1].strftime("%Y-%m-%d")
    except Exception:
        twii_vs_ma20 = 0
        twii_5d_ret = 0
        twii_1d_ret = 0

    try:
        vix = pd.read_csv(vix_path, dtype={"Date": str})
        vix["Date"] = pd.to_datetime(vix["Date"])
        vix = vix.sort_values("Date").tail(10)
        vix_latest = float(vix["Close"].iloc[-1])
        vix_prev = float(vix["Close"].iloc[-2]) if len(vix) >= 2 else vix_latest
        vix_5d_ago = float(vix["Close"].iloc[-6]) if len(vix) >= 6 else vix_latest
        vix_1d_chg = vix_latest - vix_prev
        vix_5d_chg = vix_latest - vix_5d_ago

        indicators["vix"] = round(vix_latest, 1)
        indicators["vix_1d_chg"] = round(vix_1d_chg, 1)
        indicators["vix_5d_chg"] = round(vix_5d_chg, 1)
    except Exception:
        vix_latest = 15
        vix_1d_chg = 0
        vix_5d_chg = 0

    try:
        sox = pd.read_csv(sox_path, dtype={"Date": str})
        sox["Date"] = pd.to_datetime(sox["Date"])
        sox = sox.sort_values("Date").tail(10)
        sox_latest = float(sox["Close"].iloc[-1])
        sox_5d_ago = float(sox["Close"].iloc[-6]) if len(sox) >= 6 else sox_latest
        sox_5d_ret = (sox_latest / sox_5d_ago - 1) * 100

        indicators["sox_5d_ret"] = round(sox_5d_ret, 2)
    except Exception:
        sox_5d_ret = 0

    # --- 紅綠燈判定邏輯 ---
    red_flags = 0
    yellow_flags = 0

    # 規則 1: VIX 恐慌指標
    if vix_latest >= 30:
        red_flags += 2
        reasons.append(f"VIX={vix_latest:.1f} 極度恐慌（>30），市場處於恐慌拋售狀態")
    elif vix_latest >= 25:
        red_flags += 1
        reasons.append(f"VIX={vix_latest:.1f} 偏高（>25），市場恐慌升溫")
    elif vix_latest >= 20:
        yellow_flags += 1
        reasons.append(f"VIX={vix_latest:.1f} 警戒區（>20），市場不安情緒升高")

    # 規則 2: VIX 單日暴漲（突發事件偵測）
    if vix_1d_chg >= 5:
        red_flags += 1
        reasons.append(f"VIX 單日暴漲 {vix_1d_chg:+.1f} 點，可能有突發利空事件")
    elif vix_1d_chg >= 3:
        yellow_flags += 1
        reasons.append(f"VIX 單日上升 {vix_1d_chg:+.1f} 點，恐慌情緒升溫中")

    # 規則 3: 大盤跌破月線
    if twii_vs_ma20 < -3:
        red_flags += 1
        reasons.append(f"加權指數跌破月線 {twii_vs_ma20:+.1f}%，中期趨勢轉空")
    elif twii_vs_ma20 < 0:
        yellow_flags += 1
        reasons.append(f"加權指數低於月線 {twii_vs_ma20:+.1f}%，注意趨勢轉弱")

    # 規則 4: 大盤短線暴跌
    if twii_5d_ret < -5:
        red_flags += 1
        reasons.append(f"大盤近5日跌 {twii_5d_ret:+.1f}%，短線急殺")
    elif twii_5d_ret < -3:
        yellow_flags += 1
        reasons.append(f"大盤近5日跌 {twii_5d_ret:+.1f}%，下行壓力增加")

    # 規則 5: 費半（半導體風向球）
    if sox_5d_ret < -7:
        red_flags += 1
        reasons.append(f"費半近5日跌 {sox_5d_ret:+.1f}%，半導體系統性風險")
    elif sox_5d_ret < -4:
        yellow_flags += 1
        reasons.append(f"費半近5日跌 {sox_5d_ret:+.1f}%，半導體轉弱")

    # --- 綜合判定 ---
    if red_flags >= 2:
        signal = "red"
        label = "紅燈 — 禁止進場"
        advice = "系統性風險過高，建議暫停所有新買進操作，持股考慮減碼"
    elif red_flags >= 1:
        signal = "yellow"
        label = "黃燈 — 減碼操作"
        advice = "市場出現風險訊號，建議新進場資金減半，嚴格執行停損"
    elif yellow_flags >= 2:
        signal = "yellow"
        label = "黃燈 — 減碼操作"
        advice = "多項指標轉弱，建議縮減部位、謹慎操作"
    elif yellow_flags >= 1:
        signal = "light_green"
        label = "淺綠燈 — 正常但留意"
        advice = "大盤大致正常，但有輕微警訊，正常操作但注意風控"
    else:
        signal = "green"
        label = "綠燈 — 正常進場"
        advice = "大盤環境穩定，ML 預測可信度較高，正常執行策略"
        if not reasons:
            reasons.append("大盤站穩月線，VIX 平穩，無系統性風險訊號")

    return {
        "signal": signal,
        "label": label,
        "advice": advice,
        "reasons": reasons,
        "indicators": indicators,
    }


# ==============================================================================
# 回測 API
# ==============================================================================
@app.get("/api/backtest/latest", tags=["backtest"])
def backtest_latest():
    """取得最新回測結果."""
    import json
    from ml.config import REPORT_DIR
    path = os.path.join(REPORT_DIR, "backtest_latest.json")
    if not os.path.exists(path):
        return {"error": "尚無回測結果，請先執行 python -m ml.backtest"}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/pipeline/backtest", tags=["pipeline"])
def trigger_backtest(top_n: int = 10):
    """觸發回測 (背景執行)."""
    import subprocess, sys
    pipeline_log.info(f"=== 手動觸發回測 (Top {top_n}) ===")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "ml.backtest", "--top", str(top_n)],
            cwd=os.path.dirname(__file__),
        )
        return {"status": "started", "top_n": top_n}
    except Exception as e:
        pipeline_log.error(f"回測啟動失敗: {e}")
        return {"status": "error", "detail": str(e)}


# ==============================================================================
# Pipeline API (觸發更新 / 重訓 / 預測)
# ==============================================================================
@app.post("/api/pipeline/update", tags=["pipeline"])
def trigger_update():
    """觸發資料更新 (twstock + valuation + news)"""
    import subprocess, sys
    pipeline_log.info("=== 手動觸發資料更新 ===")

    results = {}
    for name, cmd in [
        ("twstock", [sys.executable, "twstock.py"]),
        ("valuation", [sys.executable, "scripts/backfill_valuation.py",
                        "--start-date", datetime.now().strftime("%Y-%m-%d")]),
        ("news", [sys.executable, "scripts/fetch_daily_news.py"]),
    ]:
        try:
            t0 = time.time()
            r = subprocess.run(cmd, cwd=os.path.dirname(__file__),
                               capture_output=True, text=True, timeout=600)
            elapsed = time.time() - t0
            ok = r.returncode == 0
            results[name] = {"success": ok, "elapsed_sec": round(elapsed, 1)}
            pipeline_log.info(f"  {name}: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
            if not ok:
                pipeline_log.error(f"  {name} stderr: {r.stderr[:500]}")
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
            pipeline_log.error(f"  {name} exception: {e}")

    pipeline_log.info(f"=== 資料更新完成: {results} ===")
    return {"status": "done", "results": results}


@app.post("/api/pipeline/ingest", tags=["pipeline"])
def trigger_ingest():
    """觸發 CSV → DuckDB 匯入 (在 server process 內執行，不需另開 process)"""
    pipeline_log.info("=== 手動觸發 ingest ===")
    try:
        t0 = time.time()
        from backend.db.ingest import ingest_all
        results = ingest_all()
        elapsed = time.time() - t0
        pipeline_log.info(f"  ingest 完成: {results} ({elapsed:.1f}s)")
        return {"success": True, "elapsed_sec": round(elapsed, 1), "results": results}
    except Exception as e:
        pipeline_log.error(f"  ingest exception: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/db/release", tags=["pipeline"])
def release_db():
    """釋放 DuckDB 連線，讓外部 process 可以寫入。"""
    from backend.db.engine import close_conn
    close_conn()
    pipeline_log.info("DuckDB 連線已釋放 (via /api/db/release)")
    return {"success": True}


@app.post("/api/db/reconnect", tags=["pipeline"])
def reconnect_db():
    """重新建立 DuckDB 連線 (ingest 完成後呼叫)。"""
    from backend.db.engine import reconnect
    reconnect()
    pipeline_log.info("DuckDB 連線已重建 (via /api/db/reconnect)")
    return {"success": True}


@app.post("/api/pipeline/retrain", tags=["pipeline"])
def trigger_retrain():
    """觸發模型重新訓練"""
    import subprocess, sys
    pipeline_log.info("=== 手動觸發模型重訓 ===")

    try:
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "ml.train"],
            cwd=os.path.dirname(__file__),
            capture_output=True, text=True, timeout=1800,
        )
        elapsed = time.time() - t0
        ok = r.returncode == 0
        pipeline_log.info(f"  重訓: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
        if not ok:
            pipeline_log.error(f"  重訓 stderr: {r.stderr[:1000]}")
        return {"success": ok, "elapsed_sec": round(elapsed, 1)}
    except Exception as e:
        pipeline_log.error(f"  重訓 exception: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/pipeline/predict", tags=["pipeline"])
def trigger_predict():
    """觸發預測"""
    import subprocess, sys
    pipeline_log.info("=== 手動觸發預測 ===")

    try:
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "ml.predict"],
            cwd=os.path.dirname(__file__),
            capture_output=True, text=True, timeout=600,
        )
        elapsed = time.time() - t0
        ok = r.returncode == 0
        pipeline_log.info(f"  預測: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
        return {"success": ok, "elapsed_sec": round(elapsed, 1)}
    except Exception as e:
        pipeline_log.error(f"  預測 exception: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/pipeline/logs", tags=["pipeline"])
def get_logs(type: str = "access", lines: int = 100):
    """查看 log 檔案最後 N 行

    type: access | error | pipeline
    """
    file_map = {
        "access": "web_access.log",
        "error": "web_error.log",
        "pipeline": "pipeline.log",
    }
    if type not in file_map:
        return {"error": f"不支援的 log 類型: {type}", "valid": list(file_map.keys())}

    fpath = os.path.join(LOG_DIR, file_map[type])
    if not os.path.exists(fpath):
        return {"type": type, "lines": [], "total": 0}

    with open(fpath, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {
        "type": type,
        "file": file_map[type],
        "total_lines": len(all_lines),
        "showing": len(tail),
        "lines": [l.rstrip() for l in tail],
    }


# ==============================================================================
# 靜態檔案 & 前端
# ==============================================================================
STATIC_DIR = os.path.join(os.path.dirname(__file__), "frontend", "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", tags=["frontend"])
def index():
    """首頁 Dashboard"""
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "frontend", "index.html")
    )


@app.get("/sw.js", tags=["frontend"], include_in_schema=False)
def service_worker():
    """Service Worker 需從根路徑提供以控制全站快取範圍"""
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ==============================================================================
# 啟動事件
# ==============================================================================
@app.on_event("startup")
async def startup():
    from backend.db.engine import get_conn

    pipeline_log.info("=== Web Server 啟動 ===")

    conn = get_conn()
    try:
        tables = [t[0] for t in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()]

        if "daily_k" not in tables:
            pipeline_log.info("首次啟動，匯入 CSV 到 DuckDB...")
            from backend.db.ingest import ingest_all
            ingest_all()
        else:
            db_max = conn.execute("SELECT MAX(date) FROM daily_k").fetchone()[0]
            row_count = conn.execute("SELECT COUNT(*) FROM daily_k").fetchone()[0]
            pipeline_log.info(f"DuckDB 已有 {row_count:,} 筆日K資料 (最新日期: {db_max})")

            import glob as _g
            from backend.config import DAILY_K_DIR
            sample_csv = sorted(_g.glob(os.path.join(DAILY_K_DIR, "2330.csv")))
            if sample_csv:
                import pandas as _pd
                try:
                    tail = _pd.read_csv(sample_csv[0], usecols=[0], nrows=0)
                    date_col = tail.columns[0]
                    last_rows = _pd.read_csv(sample_csv[0], usecols=[date_col]).iloc[-1:]
                    csv_max = str(last_rows.iloc[0, 0])[:10]
                    db_max_str = str(db_max)[:10]
                    if csv_max > db_max_str:
                        pipeline_log.info(f"CSV 最新 {csv_max} > DB {db_max_str}，自動 ingest...")
                        from backend.db.ingest import ingest_all
                        ingest_all()
                except Exception as e:
                    pipeline_log.warning(f"自動 ingest 檢查失敗: {e}")

        if "stock_list" not in tables:
            pipeline_log.info("建立 stock_list...")
            _build_stock_list_safe(conn)

    except Exception as e:
        pipeline_log.error(f"啟動錯誤: {e}\n{traceback.format_exc()}")

    _warm_prediction_context_async()


@app.on_event("shutdown")
async def shutdown():
    pipeline_log.info("=== Web Server 關閉 ===")


def _build_stock_list_safe(conn=None):
    """建立 stock_list 表 (相容無 Change_Pct 欄位的情況)"""
    if conn is None:
        from backend.db.engine import get_conn
        conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS stock_list")
    conn.execute("""
        CREATE TABLE stock_list AS
        WITH latest_price AS (
            SELECT
                Ticker,
                LAST(Close ORDER BY Date) AS Last_Close,
                LAST(Volume ORDER BY Date) AS Last_Volume,
                LAST(Date ORDER BY Date) AS Last_Date
            FROM daily_k
            GROUP BY Ticker
        ),
        prev_price AS (
            SELECT Ticker,
                   Close AS Prev_Close,
                   ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Date DESC) AS rn
            FROM daily_k
        ),
        names AS (
            SELECT DISTINCT
                CAST(Ticker AS VARCHAR) AS Ticker,
                Name
            FROM financials
            WHERE Name IS NOT NULL
        )
        SELECT
            lp.Ticker,
            COALESCE(n.Name, '') AS Name,
            lp.Last_Close,
            lp.Last_Volume,
            lp.Last_Date,
            CASE WHEN pp.Prev_Close > 0
                 THEN ROUND((lp.Last_Close - pp.Prev_Close) / pp.Prev_Close * 100, 2)
                 ELSE NULL END AS Last_Change_Pct
        FROM latest_price lp
        LEFT JOIN names n ON lp.Ticker = n.Ticker
        LEFT JOIN prev_price pp ON lp.Ticker = pp.Ticker AND pp.rn = 2
    """)
    try:
        conn.execute("CREATE INDEX idx_sl_ticker ON stock_list(Ticker)")
    except Exception:
        pass
    cnt = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
    pipeline_log.info(f"stock_list: {cnt} 筆")


# ==============================================================================
# 主程式
# ==============================================================================
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="台股分析平台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    print(f"\n  台股分析平台啟動中...")
    print(f"  http://{args.host}:{args.port}")
    print(f"  Log 目錄: {LOG_DIR}")
    print()

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
