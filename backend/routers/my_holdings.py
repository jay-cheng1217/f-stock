"""Personal holdings CRUD and daily review API."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import traceback
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.config import BASE_DIR, DAILY_K_DIR
from backend.logging_config import error_log
from backend.routers.t1 import _NAME_LOOKUP, _SECTOR_LOOKUP
from backend.services.news_service import get_stock_news_alert
from ml.thresholds import CRASH_RETURN_20D_MIN, MIN_LIQUIDITY_SHARES, RECOMMENDATION_OVERHEAT_THRESHOLD
from ml.universe import ETF_TICKERS, ETF_TYPES, classify_ticker, get_etf_metadata, normalize_ticker
from scripts.exdiv_utils import cum_dividend
from scripts.sector_beta_audit import _load_twii_return as _load_twii_return_for_attribution

router = APIRouter(tags=["my_holdings"])

LOT_SIZE = 1000.0
STOP_LOSS_PCT = -10.0
SECTOR_YELLOW_THRESHOLD_PCT = 30.0
SECTOR_RED_THRESHOLD_PCT = 50.0
TOP3_YELLOW_THRESHOLD_PCT = 50.0
LEVERAGED_ETF_RED_THRESHOLD_PCT = 20.0
MY_HOLDINGS_DB_PATH = os.environ.get("MY_HOLDINGS_DB_PATH") or os.path.join(
    BASE_DIR,
    "my_holdings.db",
)


class HoldingCreate(BaseModel):
    ticker: str
    asset_type: Optional[str] = None
    shares: float = Field(ge=0)
    avg_cost: float = Field(ge=0)
    breakeven_price: Optional[float] = Field(default=None, ge=0)
    entry_date: Optional[str] = None
    note: Optional[str] = None


class HoldingPatch(BaseModel):
    shares: Optional[float] = Field(default=None, ge=0)
    avg_cost: Optional[float] = Field(default=None, ge=0)
    breakeven_price: Optional[float] = Field(default=None, ge=0)
    entry_date: Optional[str] = None
    note: Optional[str] = None


class HoldingTransactionCreate(BaseModel):
    action: str
    shares: float = Field(gt=0)
    price: float = Field(ge=0)
    fee: float = Field(default=0, ge=0)
    tax: float = Field(default=0, ge=0)
    transaction_date: Optional[str] = None
    note: Optional[str] = None


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _round(value: Any, digits: int = 2) -> float | None:
    numeric = _safe_float(value)
    return round(numeric, digits) if numeric is not None else None


def _normalize_holding_ticker(ticker: object) -> str:
    ticker_norm = normalize_ticker(ticker)
    if not ticker_norm:
        raise HTTPException(status_code=400, detail="ticker is required")
    if ticker_norm.isdigit() and len(ticker_norm) < 4:
        ticker_norm = ticker_norm.zfill(4)
    return ticker_norm


def _classify_asset(ticker: str, requested_asset_type: str | None = None) -> tuple[str, str]:
    universe_type = classify_ticker(ticker)
    inferred = "etf" if universe_type in ETF_TYPES else "stock"
    if requested_asset_type is None:
        return inferred, universe_type

    requested = requested_asset_type.strip().lower()
    if requested not in {"stock", "etf"}:
        raise HTTPException(status_code=400, detail="asset_type must be stock or etf")
    if requested != inferred:
        raise HTTPException(
            status_code=400,
            detail=f"asset_type {requested} conflicts with universe classification {universe_type}",
        )
    return requested, universe_type


def _validate_create_ticker(ticker: str, asset_type: str) -> None:
    if asset_type == "etf":
        return
    if ticker in _NAME_LOOKUP or _daily_k_path(ticker).exists():
        return
    raise HTTPException(
        status_code=400,
        detail="ticker not found in stock universe or daily K data",
    )


def _connect() -> sqlite3.Connection:
    Path(MY_HOLDINGS_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MY_HOLDINGS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            asset_type TEXT NOT NULL CHECK(asset_type IN ('stock', 'etf')),
            shares REAL NOT NULL CHECK(shares >= 0),
            avg_cost REAL NOT NULL CHECK(avg_cost >= 0),
            breakeven_price REAL,
            entry_date TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holding_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
            action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
            shares REAL NOT NULL CHECK(shares > 0),
            price REAL NOT NULL CHECK(price >= 0),
            fee REAL NOT NULL DEFAULT 0,
            tax REAL NOT NULL DEFAULT 0,
            transaction_date TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        )
        """
    )


def _daily_k_path(ticker: str) -> Path:
    return Path(DAILY_K_DIR) / f"{ticker}.csv"


def _load_daily_k_metrics(ticker: str) -> dict[str, Any]:
    path = _daily_k_path(ticker)
    base = {
        "daily_k_status": "missing",
        "daily_k_path": str(path),
        "latest_date": None,
        "latest_close": None,
        "latest_volume": None,
        "ma5": None,
        "ma20": None,
        "ma60": None,
        "ret_20d_pct": None,
        "ret_20d_cash_dividend_per_share": 0.0,
        "price_vs_ma20_pct": None,
        "price_vs_ma60_pct": None,
        "avg_volume_5d": None,
    }
    if not path.exists():
        return base

    try:
        df = pd.read_csv(path, dtype={"Date": str})
    except Exception as exc:
        base["daily_k_status"] = "error"
        base["error"] = str(exc)
        return base

    required = {"Date", "Close"}
    if not required.issubset(df.columns):
        base["daily_k_status"] = "error"
        base["error"] = f"daily K missing columns: {sorted(required - set(df.columns))}"
        return base

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    else:
        df["Volume"] = None
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    if df.empty:
        base["daily_k_status"] = "empty"
        return base

    latest = df.iloc[-1]
    close = float(latest["Close"])

    def rolling_mean(window: int) -> float | None:
        if len(df) < window:
            return None
        return float(df["Close"].tail(window).mean())

    ma5 = rolling_mean(5)
    ma20 = rolling_mean(20)
    ma60 = rolling_mean(60)
    ret_20d = None
    ret_20d_cash_dividend = 0.0
    if len(df) >= 21:
        prev = _safe_float(df["Close"].iloc[-21])
        if prev and prev > 0:
            previous_date = df["Date"].iloc[-21].date().isoformat()
            latest_date = latest["Date"].date().isoformat()
            ret_20d_cash_dividend = cum_dividend(ticker, previous_date, latest_date)
            ret_20d = ((close + ret_20d_cash_dividend) / prev - 1.0) * 100.0
    avg_volume_5d = None
    if len(df) >= 5 and "Volume" in df.columns:
        avg_volume_5d = _safe_float(df["Volume"].tail(5).mean())

    return {
        **base,
        "daily_k_status": "ok",
        "latest_date": latest["Date"].date().isoformat(),
        "latest_close": _round(close, 2),
        "latest_volume": _round(latest.get("Volume"), 0),
        "ma5": _round(ma5, 2),
        "ma20": _round(ma20, 2),
        "ma60": _round(ma60, 2),
        "ret_20d_pct": _round(ret_20d, 2),
        "ret_20d_cash_dividend_per_share": _round(ret_20d_cash_dividend, 4),
        "price_vs_ma20_pct": _round((close / ma20 - 1.0) * 100.0 if ma20 and ma20 > 0 else None, 2),
        "price_vs_ma60_pct": _round((close / ma60 - 1.0) * 100.0 if ma60 and ma60 > 0 else None, 2),
        "avg_volume_5d": _round(avg_volume_5d, 0),
    }


def _holding_from_row(row: sqlite3.Row) -> dict[str, Any]:
    ticker = row["ticker"]
    universe_type = classify_ticker(ticker)
    metrics = _load_daily_k_metrics(ticker)
    shares = float(row["shares"] or 0)
    avg_cost = float(row["avg_cost"] or 0)
    close = _safe_float(metrics.get("latest_close"))
    latest_date = metrics.get("latest_date")
    entry_date = row["entry_date"]
    cash_dividend_per_share = 0.0
    distribution_adjustment_status = "entry_date_missing"
    if close is not None and entry_date and latest_date:
        cash_dividend_per_share = cum_dividend(ticker, entry_date, latest_date)
        distribution_adjustment_status = "ok"
    effective_close = close + cash_dividend_per_share if close is not None else None
    cost_value = shares * LOT_SIZE * avg_cost
    current_value = shares * LOT_SIZE * close if close is not None else None
    unrealized_pnl = current_value - cost_value if current_value is not None else None
    unrealized_pct = (close / avg_cost - 1.0) * 100.0 if close is not None and avg_cost > 0 else None
    distribution_cash_adjustment = shares * LOT_SIZE * cash_dividend_per_share
    total_pnl_with_distributions = (
        unrealized_pnl + distribution_cash_adjustment if unrealized_pnl is not None else None
    )
    adjusted_return_pct = (
        (effective_close / avg_cost - 1.0) * 100.0
        if effective_close is not None and avg_cost > 0
        else None
    )
    breakeven = _safe_float(row["breakeven_price"])
    breakeven_gap_pct = (
        (effective_close / breakeven - 1.0) * 100.0
        if effective_close is not None and breakeven and breakeven > 0
        else None
    )
    news_alert = _load_news_alert(ticker)

    etf_metadata = get_etf_metadata(ticker) if universe_type in ETF_TYPES else None
    return {
        "id": int(row["id"]),
        "ticker": ticker,
        "name": _NAME_LOOKUP.get(ticker) or (etf_metadata or {}).get("name") or "",
        "sector": _SECTOR_LOOKUP.get(ticker) or (etf_metadata or {}).get("asset_class") or "",
        "asset_type": row["asset_type"],
        "universe_type": universe_type,
        "shares": _round(shares, 4),
        "avg_cost": _round(avg_cost, 2),
        "breakeven_price": _round(breakeven, 2),
        "entry_date": entry_date,
        "note": row["note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest_date": metrics.get("latest_date"),
        "latest_close": metrics.get("latest_close"),
        "effective_close_with_distributions": _round(effective_close, 4),
        "cum_distribution_per_share": _round(cash_dividend_per_share, 4),
        "distribution_cash_adjustment": _round(distribution_cash_adjustment, 0),
        "distribution_adjustment_status": distribution_adjustment_status,
        "current_value": _round(current_value, 0),
        "cost_value": _round(cost_value, 0),
        "unrealized_pnl": _round(unrealized_pnl, 0),
        "unrealized_pnl_pct": _round(unrealized_pct, 2),
        "total_pnl_with_distributions": _round(total_pnl_with_distributions, 0),
        "adjusted_return_pct": _round(adjusted_return_pct, 2),
        "breakeven_gap_pct": _round(breakeven_gap_pct, 2),
        "daily_k_status": metrics.get("daily_k_status"),
        "news_alert": news_alert,
    }


def _fetch_holding(conn: sqlite3.Connection, holding_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM holdings WHERE id = ?", (holding_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="holding not found")
    return row


def _summarize_holdings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current_value = sum(float(row["current_value"] or 0) for row in rows)
    cost_value = sum(float(row["cost_value"] or 0) for row in rows)
    unrealized = current_value - cost_value if current_value or cost_value else None
    unrealized_pct = (current_value / cost_value - 1.0) * 100.0 if cost_value > 0 else None
    distribution_cash_adjustment = sum(float(row.get("distribution_cash_adjustment") or 0) for row in rows)
    total_pnl_with_distributions = (
        unrealized + distribution_cash_adjustment if unrealized is not None else None
    )
    adjusted_return_pct = (
        total_pnl_with_distributions / cost_value * 100.0
        if total_pnl_with_distributions is not None and cost_value > 0
        else None
    )
    return {
        "holding_count": len(rows),
        "stock_count": sum(1 for row in rows if row["asset_type"] == "stock"),
        "etf_count": sum(1 for row in rows if row["asset_type"] == "etf"),
        "current_value": _round(current_value, 0),
        "cost_value": _round(cost_value, 0),
        "unrealized_pnl": _round(unrealized, 0),
        "unrealized_pnl_pct": _round(unrealized_pct, 2),
        "distribution_cash_adjustment": _round(distribution_cash_adjustment, 0),
        "total_pnl_with_distributions": _round(total_pnl_with_distributions, 0),
        "adjusted_return_pct": _round(adjusted_return_pct, 2),
        "latest_date": max([row["latest_date"] for row in rows if row.get("latest_date")] or [None]),
    }


def _latest_prediction_csv_path() -> Path | None:
    paths = sorted((Path(BASE_DIR) / "ml" / "models").glob("predictions_2026-*.csv"))
    return paths[-1] if paths else None


@lru_cache(maxsize=1)
def _load_latest_beta_lookup() -> dict[str, float]:
    path = _latest_prediction_csv_path()
    if path is None or not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str}, usecols=["ticker", "beta_60"])
    except Exception:
        return {}
    df["ticker"] = df["ticker"].map(_normalize_holding_ticker)
    df["beta_60"] = pd.to_numeric(df["beta_60"], errors="coerce")
    df = df.dropna(subset=["ticker", "beta_60"])
    return dict(zip(df["ticker"], df["beta_60"].astype(float)))


def _load_news_alert(ticker: str) -> dict[str, Any]:
    try:
        return get_stock_news_alert(ticker, days=7, limit=5)
    except Exception as exc:
        return {
            "status": "error",
            "alert_level": "gray",
            "score": None,
            "summary": f"news unavailable: {exc}",
            "negative_count": 0,
            "positive_count": 0,
            "neutral_count": 0,
            "latest_date": None,
            "latest_title": None,
            "rows": [],
        }


def _empty_portfolio_review(cash: float) -> dict[str, Any]:
    total_assets = max(float(cash), 0.0)
    return {
        "status": "success",
        "risk_level": "green",
        "risk_label": "綠燈",
        "generated_at": _now_iso(),
        "summary": {
            "holding_count": 0,
            "holding_value": 0.0,
            "cash": _round(cash, 0),
            "total_assets": _round(total_assets, 0),
            "invested_ratio_pct": 0.0,
            "cash_ratio_pct": 100.0 if total_assets > 0 else 0.0,
            "top3_weight_pct": 0.0,
            "stock_weight_pct": 0.0,
            "etf_weight_pct": 0.0,
            "leveraged_inverse_etf_weight_pct": 0.0,
            "weighted_beta_60": None,
            "beta_coverage_pct": 0.0,
        },
        "sector_weights": [],
        "asset_type_weights": [],
        "top_holdings": [],
        "beta": {"weighted_beta_60": None, "coverage_pct": 0.0, "missing_tickers": []},
        "alerts": [],
    }


def _weight_rows(
    rows: list[dict[str, Any]],
    *,
    key: str,
    holding_value: float,
    total_assets: float,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = float(row.get("current_value") or 0)
        if value <= 0:
            continue
        label = str(row.get(key) or "未分類")
        bucket = buckets.setdefault(
            label,
            {"name": label, "market_value": 0.0, "holding_count": 0, "tickers": []},
        )
        bucket["market_value"] += value
        bucket["holding_count"] += 1
        bucket["tickers"].append(row["ticker"])

    out = []
    for bucket in buckets.values():
        weight = bucket["market_value"] / holding_value * 100.0 if holding_value > 0 else 0.0
        portfolio_weight = bucket["market_value"] / total_assets * 100.0 if total_assets > 0 else 0.0
        level = "green"
        if weight > SECTOR_RED_THRESHOLD_PCT:
            level = "red"
        elif weight > SECTOR_YELLOW_THRESHOLD_PCT:
            level = "yellow"
        out.append(
            {
                **bucket,
                "market_value": _round(bucket["market_value"], 0),
                "weight_pct": _round(weight, 2),
                "portfolio_weight_pct": _round(portfolio_weight, 2),
                "level": level,
            }
        )
    return sorted(out, key=lambda item: item["weight_pct"], reverse=True)


def _alert(level: str, code: str, message: str, value: Any = None) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "value": value}


def _risk_from_alerts(alerts: list[dict[str, Any]]) -> tuple[str, str]:
    levels = {alert["level"] for alert in alerts}
    if "red" in levels:
        return "red", "紅燈"
    if "yellow" in levels:
        return "yellow", "黃燈"
    return "green", "綠燈"


def _etf_group_name(universe_type: str) -> str:
    return {
        "etf_passive": "ETF (passive)",
        "etf_active": "ETF (active)",
        "etf_bond": "ETF (bond)",
        "etf_futures": "ETF (futures)",
        "etf_leveraged": "ETF (leveraged)",
        "etf_inverse": "ETF (inverse)",
    }.get(universe_type, "ETF (other)")


def _attribution_group(row: dict[str, Any]) -> str:
    if row.get("asset_type") == "etf":
        return _etf_group_name(str(row.get("universe_type") or "etf"))
    return str(row.get("sector") or "未分類")


def _pct_out(value: Any) -> float | None:
    numeric = _safe_float(value)
    return _round(numeric * 100.0, 4) if numeric is not None else None


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _latest_available_holding_date(rows: list[dict[str, Any]]) -> str:
    dates = [row.get("latest_date") for row in rows if row.get("latest_date")]
    if dates:
        return max(dates)
    return datetime.now().date().isoformat()


def _resolve_attribution_window(
    rows: list[dict[str, Any]],
    start: str | None,
    end: str | None,
    days: int,
) -> tuple[str, str, int]:
    end_date = _parse_date(end) or _parse_date(_latest_available_holding_date(rows)) or datetime.now().date()
    if start:
        start_date = _parse_date(start)
        if start_date is None:
            raise HTTPException(status_code=400, detail="start must be YYYY-MM-DD")
    else:
        start_date = end_date - timedelta(days=max(int(days or 7), 1))
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be <= end")
    resolved_days = max((end_date - start_date).days, 0)
    return start_date.isoformat(), end_date.isoformat(), resolved_days


def _load_price_return(
    ticker: str,
    start: str,
    end: str,
    *,
    min_rows: int = 1,
) -> dict[str, Any] | None:
    path = _daily_k_path(ticker)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype={"Date": str})
    except Exception:
        return None
    if "Date" not in df.columns or "Close" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    else:
        df["Volume"] = 0.0
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    window = df[(df["Date"] >= pd.Timestamp(start)) & (df["Date"] <= pd.Timestamp(end))].copy()
    if len(window) < min_rows:
        return None
    start_row = window.iloc[0]
    end_row = window.iloc[-1]
    start_close = _safe_float(start_row["Close"])
    end_close = _safe_float(end_row["Close"])
    if start_close is None or end_close is None or start_close <= 0:
        return None
    traded_value = (window["Close"] * window["Volume"]).replace([float("inf"), float("-inf")], pd.NA).dropna()
    avg_traded_value = _safe_float(traded_value.mean()) or start_close
    return {
        "start_date": start_row["Date"].date().isoformat(),
        "end_date": end_row["Date"].date().isoformat(),
        "start_close": start_close,
        "end_close": end_close,
        "return": end_close / start_close - 1.0,
        "avg_traded_value": avg_traded_value,
        "rows": int(len(window)),
    }


def _build_holding_attribution_rows(
    rows: list[dict[str, Any]],
    start: str,
    end: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    end_date = _parse_date(end)
    for row in rows:
        entry_date = _parse_date(row.get("entry_date"))
        if entry_date is not None and end_date is not None and entry_date > end_date:
            skipped.append({"ticker": row["ticker"], "reason": "entry_date_after_window", "entry_date": row.get("entry_date")})
            continue
        effective_start = max(start, entry_date.isoformat()) if entry_date is not None else start
        stats = _load_price_return(row["ticker"], effective_start, end, min_rows=1)
        if stats is None:
            skipped.append({"ticker": row["ticker"], "reason": "insufficient_daily_k", "effective_start": effective_start})
            continue
        shares = float(row.get("shares") or 0.0)
        start_value = shares * LOT_SIZE * float(stats["start_close"])
        if start_value <= 0:
            skipped.append({"ticker": row["ticker"], "reason": "zero_start_value", "effective_start": effective_start})
            continue
        included.append(
            {
                "ticker": row["ticker"],
                "name": row.get("name"),
                "asset_type": row.get("asset_type"),
                "universe_type": row.get("universe_type"),
                "sector": row.get("sector"),
                "group": _attribution_group(row),
                "shares": row.get("shares"),
                "entry_date": row.get("entry_date"),
                "effective_start": stats["start_date"],
                "effective_end": stats["end_date"],
                "start_close": _round(stats["start_close"], 2),
                "end_close": _round(stats["end_close"], 2),
                "start_value": start_value,
                "return": float(stats["return"]),
            }
        )
    total_start_value = sum(item["start_value"] for item in included)
    if total_start_value > 0:
        for item in included:
            item["weight"] = item["start_value"] / total_start_value
    return included, skipped


def _weighted_decimal(rows: list[dict[str, Any]], value_key: str, weight_key: str) -> float | None:
    total_weight = sum(float(row.get(weight_key) or 0.0) for row in rows if _safe_float(row.get(value_key)) is not None)
    if total_weight <= 0:
        return None
    return sum(float(row[value_key]) * float(row.get(weight_key) or 0.0) for row in rows if _safe_float(row.get(value_key)) is not None) / total_weight


def _build_portfolio_groups(included: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in included:
        group = groups.setdefault(row["group"], {"group": row["group"], "weight": 0.0, "start_value": 0.0, "holdings": []})
        group["weight"] += float(row.get("weight") or 0.0)
        group["start_value"] += float(row.get("start_value") or 0.0)
        group["holdings"].append(row)
    out: list[dict[str, Any]] = []
    for group in groups.values():
        group_return = _weighted_decimal(group["holdings"], "return", "weight")
        out.append({**group, "return": group_return})
    return sorted(out, key=lambda item: item["weight"], reverse=True)


def _benchmark_candidate_tickers() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for ticker, sector in _SECTOR_LOOKUP.items():
        rows.append({"ticker": _normalize_holding_ticker(ticker), "group": str(sector or "未分類"), "source": "stock_sector"})
    for ticker in ETF_TICKERS:
        ticker_norm = _normalize_holding_ticker(ticker)
        universe_type = classify_ticker(ticker_norm)
        rows.append({"ticker": ticker_norm, "group": _etf_group_name(universe_type), "source": "etf_type"})
    return rows


@lru_cache(maxsize=16)
def _build_benchmark_groups(start: str, end: str) -> list[dict[str, Any]]:
    by_group: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in _benchmark_candidate_tickers():
        ticker = item["ticker"]
        if ticker in seen:
            continue
        seen.add(ticker)
        stats = _load_price_return(ticker, start, end, min_rows=2)
        if stats is None:
            continue
        group = by_group.setdefault(
            item["group"],
            {"group": item["group"], "weight_basis": 0.0, "members": [], "source": item["source"]},
        )
        weight_basis = float(stats.get("avg_traded_value") or 0.0)
        group["weight_basis"] += weight_basis
        group["members"].append(
            {
                "ticker": ticker,
                "return": float(stats["return"]),
                "weight_basis": weight_basis,
            }
        )
    total_basis = sum(group["weight_basis"] for group in by_group.values())
    out: list[dict[str, Any]] = []
    for group in by_group.values():
        group_return = _weighted_decimal(group["members"], "return", "weight_basis")
        out.append(
            {
                "group": group["group"],
                "weight": group["weight_basis"] / total_basis if total_basis > 0 else 0.0,
                "return": group_return,
                "member_count": len(group["members"]),
                "source": group["source"],
            }
        )
    return sorted(out, key=lambda item: item["weight"], reverse=True)


def _build_attribution_rows(
    portfolio_groups: list[dict[str, Any]],
    benchmark_groups: list[dict[str, Any]],
    benchmark_return: float,
) -> list[dict[str, Any]]:
    portfolio_by_group = {item["group"]: item for item in portfolio_groups}
    benchmark_by_group = {item["group"]: item for item in benchmark_groups}
    groups = sorted(set(portfolio_by_group) | set(benchmark_by_group))
    rows: list[dict[str, Any]] = []
    for group_name in groups:
        portfolio = portfolio_by_group.get(group_name, {})
        benchmark = benchmark_by_group.get(group_name, {})
        portfolio_weight = float(portfolio.get("weight") or 0.0)
        benchmark_weight = float(benchmark.get("weight") or 0.0)
        portfolio_return = _safe_float(portfolio.get("return"))
        group_benchmark_return = _safe_float(benchmark.get("return"))
        if group_benchmark_return is None:
            group_benchmark_return = benchmark_return
        allocation = (portfolio_weight - benchmark_weight) * (group_benchmark_return - benchmark_return)
        selection = portfolio_weight * ((portfolio_return if portfolio_return is not None else group_benchmark_return) - group_benchmark_return)
        rows.append(
            {
                "group": group_name,
                "portfolio_weight_pct": _pct_out(portfolio_weight),
                "benchmark_weight_pct": _pct_out(benchmark_weight),
                "active_weight_pct": _pct_out(portfolio_weight - benchmark_weight),
                "portfolio_return_pct": _pct_out(portfolio_return),
                "benchmark_return_pct": _pct_out(group_benchmark_return),
                "allocation_effect_pct": _pct_out(allocation),
                "selection_effect_pct": _pct_out(selection),
                "total_effect_pct": _pct_out(allocation + selection),
                "holding_count": len(portfolio.get("holdings", [])),
                "benchmark_member_count": int(benchmark.get("member_count") or 0),
            }
        )
    return sorted(rows, key=lambda item: abs(float(item.get("total_effect_pct") or 0.0)), reverse=True)


def _build_portfolio_attribution(
    rows: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    days: int,
) -> dict[str, Any]:
    included, skipped = _build_holding_attribution_rows(rows, start, end)
    if not included:
        twii = _load_twii_return_for_attribution(start, end)
        twii_return = _safe_float(twii.get("return_pct"))
        return {
            "status": "success",
            "generated_at": _now_iso(),
            "start": start,
            "end": end,
            "days": days,
            "summary": {
                "holding_count": 0,
                "excluded_holding_count": len(skipped),
                "total_return_pct": None,
                "twii_return_pct": _pct_out(twii_return),
                "alpha_vs_twii_pct": None,
                "benchmark_return_pct": None,
                "alpha_vs_benchmark_pct": None,
                "allocation_effect_pct": None,
                "selection_effect_pct": None,
                "allocation_plus_selection_pct": None,
                "reconciliation_error_pct": None,
                "reconciliation_pass": False,
            },
            "sector_attribution": [],
            "holdings": [],
            "skipped_holdings": skipped,
            "benchmark": {"source": "local_traded_value_weighted_stock_sector_plus_etf_type_proxy", "groups": []},
        }

    portfolio_groups = _build_portfolio_groups(included)
    benchmark_groups = _build_benchmark_groups(start, end)
    benchmark_return = sum(float(item.get("weight") or 0.0) * float(item.get("return") or 0.0) for item in benchmark_groups)
    portfolio_return = sum(float(item.get("weight") or 0.0) * float(item.get("return") or 0.0) for item in portfolio_groups)
    twii = _load_twii_return_for_attribution(start, end)
    twii_return = _safe_float(twii.get("return_pct"))
    attribution_rows = _build_attribution_rows(portfolio_groups, benchmark_groups, benchmark_return)
    allocation = sum(float(row.get("allocation_effect_pct") or 0.0) for row in attribution_rows) / 100.0
    selection = sum(float(row.get("selection_effect_pct") or 0.0) for row in attribution_rows) / 100.0
    alpha_vs_benchmark = portfolio_return - benchmark_return
    reconciliation_error = allocation + selection - alpha_vs_benchmark

    return {
        "status": "success",
        "generated_at": _now_iso(),
        "start": start,
        "end": end,
        "days": days,
        "summary": {
            "holding_count": len(included),
            "excluded_holding_count": len(skipped),
            "total_return_pct": _pct_out(portfolio_return),
            "twii_return_pct": _pct_out(twii_return),
            "alpha_vs_twii_pct": _pct_out(portfolio_return - twii_return) if twii_return is not None else None,
            "benchmark_return_pct": _pct_out(benchmark_return),
            "alpha_vs_benchmark_pct": _pct_out(alpha_vs_benchmark),
            "allocation_effect_pct": _pct_out(allocation),
            "selection_effect_pct": _pct_out(selection),
            "allocation_plus_selection_pct": _pct_out(allocation + selection),
            "reconciliation_error_pct": _pct_out(reconciliation_error),
            "reconciliation_pass": abs(reconciliation_error) <= 0.005,
        },
        "sector_attribution": attribution_rows,
        "holdings": [
            {
                **item,
                "weight_pct": _pct_out(item.get("weight")),
                "return_pct": _pct_out(item.get("return")),
                "start_value": _round(item.get("start_value"), 0),
            }
            for item in included
        ],
        "skipped_holdings": skipped,
        "benchmark": {
            "source": "local_traded_value_weighted_stock_sector_plus_etf_type_proxy",
            "twii": twii,
            "groups": [
                {
                    "group": item["group"],
                    "weight_pct": _pct_out(item.get("weight")),
                    "return_pct": _pct_out(item.get("return")),
                    "member_count": item.get("member_count"),
                    "source": item.get("source"),
                }
                for item in benchmark_groups
            ],
        },
        "methodology": {
            "portfolio_weight_basis": "holding shares × first available close in window; entry_date after start uses entry_date as effective start",
            "brinson_model": "allocation=(Wp-Wb)*(Rb_i-Rb_total), selection=Wp*(Rp_i-Rb_i)",
            "etf_benchmark_grouping": "ETF tickers grouped by universe_type, not TWSE stock sector",
        },
    }


def _build_portfolio_review(rows: list[dict[str, Any]], cash: float = 0.0) -> dict[str, Any]:
    cash = max(float(cash or 0), 0.0)
    holding_value = sum(float(row.get("current_value") or 0) for row in rows)
    total_assets = holding_value + cash
    if holding_value <= 0:
        return _empty_portfolio_review(cash)

    weighted_rows = []
    for row in rows:
        value = float(row.get("current_value") or 0)
        holding_weight = value / holding_value * 100.0 if holding_value > 0 else 0.0
        portfolio_weight = value / total_assets * 100.0 if total_assets > 0 else 0.0
        weighted_rows.append(
            {
                **row,
                "holding_weight_pct": _round(holding_weight, 2),
                "portfolio_weight_pct": _round(portfolio_weight, 2),
            }
        )

    sector_weights = _weight_rows(
        weighted_rows,
        key="sector",
        holding_value=holding_value,
        total_assets=total_assets,
    )
    asset_type_weights = _weight_rows(
        weighted_rows,
        key="asset_type",
        holding_value=holding_value,
        total_assets=total_assets,
    )
    top_holdings = sorted(weighted_rows, key=lambda item: float(item.get("current_value") or 0), reverse=True)[:3]
    top3_weight = sum(float(row.get("holding_weight_pct") or 0) for row in top_holdings)
    stock_weight = sum(float(row.get("holding_weight_pct") or 0) for row in weighted_rows if row["asset_type"] == "stock")
    etf_weight = sum(float(row.get("holding_weight_pct") or 0) for row in weighted_rows if row["asset_type"] == "etf")
    leveraged_inverse_weight = sum(
        float(row.get("holding_weight_pct") or 0)
        for row in weighted_rows
        if row.get("universe_type") in {"etf_leveraged", "etf_inverse"}
    )

    beta_lookup = _load_latest_beta_lookup()
    beta_weighted_sum = 0.0
    beta_covered_value = 0.0
    missing_beta: list[str] = []
    for row in weighted_rows:
        value = float(row.get("current_value") or 0)
        beta = _safe_float(beta_lookup.get(row["ticker"]))
        if beta is None:
            missing_beta.append(row["ticker"])
            continue
        beta_weighted_sum += beta * value
        beta_covered_value += value
    weighted_beta = beta_weighted_sum / holding_value if holding_value > 0 and beta_covered_value > 0 else None
    beta_coverage = beta_covered_value / holding_value * 100.0 if holding_value > 0 else 0.0

    alerts: list[dict[str, Any]] = []
    if sector_weights:
        max_sector = sector_weights[0]
        if max_sector["weight_pct"] > SECTOR_RED_THRESHOLD_PCT:
            alerts.append(
                _alert(
                    "red",
                    "SECTOR_CONCENTRATION_RED",
                    f"{max_sector['name']} 持股權重超過 50%",
                    max_sector["weight_pct"],
                )
            )
        elif max_sector["weight_pct"] > SECTOR_YELLOW_THRESHOLD_PCT:
            alerts.append(
                _alert(
                    "yellow",
                    "SECTOR_CONCENTRATION_YELLOW",
                    f"{max_sector['name']} 持股權重超過 30%",
                    max_sector["weight_pct"],
                )
            )
    if leveraged_inverse_weight > LEVERAGED_ETF_RED_THRESHOLD_PCT:
        alerts.append(
            _alert(
                "red",
                "LEVERAGED_INVERSE_ETF_EXPOSURE_RED",
                "槓桿/反向 ETF 暴露超過 20%",
                _round(leveraged_inverse_weight, 2),
            )
        )
    elif leveraged_inverse_weight > 0:
        alerts.append(
            _alert(
                "info",
                "LEVERAGED_INVERSE_ETF_EXPOSURE",
                "持有槓桿/反向 ETF，需留意單日校準特性",
                _round(leveraged_inverse_weight, 2),
            )
        )
    if top3_weight > TOP3_YELLOW_THRESHOLD_PCT:
        alerts.append(
            _alert(
                "yellow",
                "TOP3_CONCENTRATION_YELLOW",
                "前三大持股權重超過 50%",
                _round(top3_weight, 2),
            )
        )
    if weighted_beta is not None:
        alerts.append(
            _alert(
                "info",
                "BETA_60_EXPOSURE",
                "beta_60 為持股市值加權估計，缺資料標的不納入分子",
                {"weighted_beta_60": _round(weighted_beta, 3), "coverage_pct": _round(beta_coverage, 2)},
            )
        )

    risk_level, risk_label = _risk_from_alerts(alerts)
    return {
        "status": "success",
        "risk_level": risk_level,
        "risk_label": risk_label,
        "generated_at": _now_iso(),
        "summary": {
            "holding_count": len(rows),
            "holding_value": _round(holding_value, 0),
            "cash": _round(cash, 0),
            "total_assets": _round(total_assets, 0),
            "invested_ratio_pct": _round(holding_value / total_assets * 100.0 if total_assets > 0 else 0.0, 2),
            "cash_ratio_pct": _round(cash / total_assets * 100.0 if total_assets > 0 else 0.0, 2),
            "top3_weight_pct": _round(top3_weight, 2),
            "stock_weight_pct": _round(stock_weight, 2),
            "etf_weight_pct": _round(etf_weight, 2),
            "leveraged_inverse_etf_weight_pct": _round(leveraged_inverse_weight, 2),
            "weighted_beta_60": _round(weighted_beta, 3),
            "beta_coverage_pct": _round(beta_coverage, 2),
        },
        "sector_weights": sector_weights,
        "asset_type_weights": asset_type_weights,
        "top_holdings": [
            {
                "ticker": row["ticker"],
                "name": row.get("name"),
                "sector": row.get("sector"),
                "asset_type": row.get("asset_type"),
                "universe_type": row.get("universe_type"),
                "current_value": row.get("current_value"),
                "holding_weight_pct": row.get("holding_weight_pct"),
                "portfolio_weight_pct": row.get("portfolio_weight_pct"),
            }
            for row in top_holdings
        ],
        "beta": {
            "weighted_beta_60": _round(weighted_beta, 3),
            "coverage_pct": _round(beta_coverage, 2),
            "missing_tickers": missing_beta,
        },
        "alerts": alerts,
    }


def _market_regime_payload() -> dict[str, Any] | None:
    path = Path(BASE_DIR) / "ml" / "reports" / "market_regime_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_response_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    body = getattr(result, "body", None)
    if body is None:
        return {}
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        payload = json.loads(body)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_stock_prediction_payload(ticker: str) -> dict[str, Any]:
    try:
        from app import stock_prediction

        return _json_response_payload(stock_prediction(ticker))
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _load_prediction_explain_payload(ticker: str) -> dict[str, Any]:
    try:
        from app import prediction_explain

        return _json_response_payload(prediction_explain(ticker))
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _reason(code: str, severity: str, message: str, value: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "value": value,
    }


def _base_rule_reasons(holding: dict[str, Any], metrics: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exit_reasons: list[dict[str, Any]] = []
    trim_reasons: list[dict[str, Any]] = []

    if metrics.get("daily_k_status") != "ok":
        exit_reasons.append(
            _reason("DAILY_K_DATA_MISSING", "exit", "日 K 資料不足，不能產生可靠 review", metrics.get("daily_k_status"))
        )
        return exit_reasons, trim_reasons

    adjusted_return_pct = _safe_float(holding.get("adjusted_return_pct"))
    if adjusted_return_pct is not None and adjusted_return_pct <= STOP_LOSS_PCT:
        exit_reasons.append(_reason("STOP_LOSS_10PCT", "exit", "含配息總報酬低於 -10% 停損線", adjusted_return_pct))

    breakeven = _safe_float(holding.get("breakeven_price"))
    effective_close = _safe_float(holding.get("effective_close_with_distributions"))
    if effective_close is not None and breakeven and effective_close < breakeven:
        exit_reasons.append(_reason("BELOW_BREAKEVEN", "exit", "含配息有效價格跌破損益兩平價", effective_close))

    ret_20d = _safe_float(metrics.get("ret_20d_pct"))
    if ret_20d is not None and ret_20d <= CRASH_RETURN_20D_MIN * 100.0:
        exit_reasons.append(_reason("CRASH_20D", "exit", "20 日跌幅觸發暴跌硬擋", ret_20d))

    avg_volume = _safe_float(metrics.get("avg_volume_5d"))
    if avg_volume is not None and avg_volume < MIN_LIQUIDITY_SHARES:
        exit_reasons.append(_reason("LOW_LIQUIDITY", "exit", "5 日均量低於流動性門檻", avg_volume))

    price_vs_ma20 = _safe_float(metrics.get("price_vs_ma20_pct"))
    if price_vs_ma20 is not None and price_vs_ma20 >= RECOMMENDATION_OVERHEAT_THRESHOLD * 100.0:
        trim_reasons.append(_reason("OVERHEAT_MA20", "trim", "短期乖離偏高，優先檢查是否減碼", price_vs_ma20))

    return exit_reasons, trim_reasons


def _signal_from_reasons(
    exit_reasons: list[dict[str, Any]],
    trim_reasons: list[dict[str, Any]],
    add_ok: bool,
) -> str:
    if exit_reasons:
        return "EXIT"
    if trim_reasons:
        return "TRIM"
    if add_ok:
        return "ADD"
    return "HOLD"


def _build_review(row: sqlite3.Row) -> dict[str, Any]:
    holding = _holding_from_row(row)
    ticker = holding["ticker"]
    metrics = _load_daily_k_metrics(ticker)
    regime = _market_regime_payload()
    exit_reasons, trim_reasons = _base_rule_reasons(holding, metrics)
    news_alert = holding.get("news_alert") or _load_news_alert(ticker)
    warnings: list[dict[str, Any]] = []
    prediction: dict[str, Any] | None = None
    explain: dict[str, Any] | None = None
    add_ok = False
    review_status = "STOCK_V2_ALPHA_MODEL_ENABLED"
    v2_status = "STOCK_V2_ALPHA_MODEL_ENABLED"
    rule_pool = "prediction_explain_plus_guardrails"

    if holding["asset_type"] == "etf":
        review_status = "ETF_RULE_SUBSET_ONLY" if metrics.get("daily_k_status") == "ok" else "ETF_DATA_MISSING_BACKFILL_REQUIRED"
        v2_status = "ETF_V2_ALPHA_MODEL_UNSUPPORTED"
        rule_pool = "technical_liquidity_price_event_subset"
        if holding["universe_type"] in {"etf_leveraged", "etf_inverse"}:
            warnings.append(
                _reason(
                    "LEVERAGED_INVERSE_ETF_HOLDING_RISK",
                    "warning",
                    "槓桿/反向 ETF 具單日校準特性，長持風險需另行確認",
                    holding["universe_type"],
                )
            )
    else:
        prediction = _load_stock_prediction_payload(ticker)
        explain = _load_prediction_explain_payload(ticker)
        domain_warnings = []
        if isinstance(explain, dict):
            domain_warnings = explain.get("domain_warnings") or explain.get("warnings") or []
        if domain_warnings:
            exit_reasons.append(
                _reason("PREDICTION_EXPLAIN_HARD_GUARD", "exit", "prediction_explain 回傳防呆警示", domain_warnings)
            )

        recommendation_text = " ".join(
            str(value or "")
            for value in [
                (prediction or {}).get("recommendation"),
                (explain or {}).get("recommendation"),
                (prediction or {}).get("signal"),
                (explain or {}).get("signal"),
            ]
        )
        pred_return = _safe_float((prediction or {}).get("pred_return_20d"))
        if pred_return is None:
            pred_return = _safe_float((explain or {}).get("pred_return_20d"))
        pnl_pct = _safe_float(holding.get("unrealized_pnl_pct"))
        add_ok = (
            not exit_reasons
            and not trim_reasons
            and pred_return is not None
            and pred_return > 0
            and pnl_pct is not None
            and pnl_pct > 0
            and ("買進" in recommendation_text or "buy" in recommendation_text.lower())
        )

    if news_alert.get("alert_level") == "yellow" and not exit_reasons:
        trim_reasons.append(
            _reason(
                "NEWS_NEGATIVE_ALERT",
                "trim",
                "近 7 日負面重大公告，逐檔 review 升級為 TRIM",
                {
                    "summary": news_alert.get("summary"),
                    "negative_count": news_alert.get("negative_count"),
                    "latest_title": news_alert.get("latest_title"),
                },
            )
        )
        add_ok = False

    signal = _signal_from_reasons(exit_reasons, trim_reasons, add_ok)
    label = {
        "EXIT": "🔴 EXIT",
        "TRIM": "🟡 TRIM",
        "ADD": "🟢 ADD",
        "HOLD": "⚪ HOLD",
    }[signal]
    return {
        "status": "success",
        "holding": holding,
        "signal": signal,
        "label": label,
        "review_status": review_status,
        "v2_alpha_model_status": v2_status,
        "rule_pool": rule_pool,
        "reasons": exit_reasons + trim_reasons,
        "warnings": warnings,
        "news_alert": news_alert,
        "metrics": metrics,
        "prediction": prediction,
        "prediction_explain": explain,
        "market_regime": regime,
        "generated_at": _now_iso(),
    }


@router.post("/api/my_holdings")
def create_holding(payload: HoldingCreate):
    try:
        ticker = _normalize_holding_ticker(payload.ticker)
        asset_type, _ = _classify_asset(ticker, payload.asset_type)
        _validate_create_ticker(ticker, asset_type)
        now = _now_iso()
        breakeven = payload.breakeven_price if payload.breakeven_price is not None else payload.avg_cost
        with _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO holdings (
                    ticker, asset_type, shares, avg_cost, breakeven_price,
                    entry_date, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    asset_type,
                    float(payload.shares),
                    float(payload.avg_cost),
                    float(breakeven),
                    payload.entry_date,
                    payload.note,
                    now,
                    now,
                ),
            )
            holding = _holding_from_row(_fetch_holding(conn, int(cursor.lastrowid)))
        return JSONResponse(content={"status": "success", "data": holding})
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Create my holding failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.get("/api/my_holdings")
def list_holdings():
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM holdings ORDER BY ticker, id").fetchall()
            data = [_holding_from_row(row) for row in rows]
        return JSONResponse(content={"status": "success", "data": data, "summary": _summarize_holdings(data)})
    except Exception as exc:
        error_log.error("List my holdings failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.get("/api/my_holdings/portfolio_review")
def portfolio_review(cash: float = Query(default=0, ge=0)):
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM holdings ORDER BY ticker, id").fetchall()
            data = [_holding_from_row(row) for row in rows]
        return JSONResponse(content=_build_portfolio_review(data, cash=cash))
    except Exception as exc:
        error_log.error("Portfolio review failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.get("/api/my_holdings/portfolio_attribution")
def portfolio_attribution(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=365),
):
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM holdings ORDER BY ticker, id").fetchall()
            data = [_holding_from_row(row) for row in rows]
        resolved_start, resolved_end, resolved_days = _resolve_attribution_window(data, start, end, days)
        payload = _build_portfolio_attribution(
            data,
            start=resolved_start,
            end=resolved_end,
            days=resolved_days,
        )
        return JSONResponse(content=payload)
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Portfolio attribution failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.patch("/api/my_holdings/{holding_id}")
def update_holding(holding_id: int, payload: HoldingPatch):
    if hasattr(payload, "model_dump"):
        changes = payload.model_dump(exclude_unset=True)
    else:
        changes = payload.dict(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="no changes provided")
    allowed = {"shares", "avg_cost", "breakeven_price", "entry_date", "note"}
    columns = [key for key in changes if key in allowed]
    if not columns:
        raise HTTPException(status_code=400, detail="no supported changes provided")
    try:
        with _connect() as conn:
            _fetch_holding(conn, holding_id)
            set_clause = ", ".join(f"{column} = ?" for column in columns) + ", updated_at = ?"
            values = [changes[column] for column in columns] + [_now_iso(), holding_id]
            conn.execute(f"UPDATE holdings SET {set_clause} WHERE id = ?", values)
            holding = _holding_from_row(_fetch_holding(conn, holding_id))
        return JSONResponse(content={"status": "success", "data": holding})
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Update my holding failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.delete("/api/my_holdings/{holding_id}")
def delete_holding(holding_id: int):
    try:
        with _connect() as conn:
            _fetch_holding(conn, holding_id)
            conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
        return JSONResponse(content={"status": "success", "deleted_id": holding_id})
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Delete my holding failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.post("/api/my_holdings/{holding_id}/transaction")
def add_transaction(holding_id: int, payload: HoldingTransactionCreate):
    action = payload.action.strip().lower()
    if action not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail="action must be buy or sell")
    try:
        with _connect() as conn:
            row = _fetch_holding(conn, holding_id)
            old_shares = float(row["shares"] or 0)
            old_avg_cost = float(row["avg_cost"] or 0)
            trade_shares = float(payload.shares)
            price = float(payload.price)
            if action == "sell" and trade_shares > old_shares:
                raise HTTPException(status_code=400, detail="sell shares exceed current holding")

            if action == "buy":
                new_shares = old_shares + trade_shares
                new_avg_cost = ((old_shares * old_avg_cost) + (trade_shares * price)) / new_shares if new_shares else 0
            else:
                new_shares = old_shares - trade_shares
                new_avg_cost = old_avg_cost

            now = _now_iso()
            conn.execute(
                """
                INSERT INTO holding_transactions (
                    holding_id, action, shares, price, fee, tax, transaction_date, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    holding_id,
                    action,
                    trade_shares,
                    price,
                    float(payload.fee),
                    float(payload.tax),
                    payload.transaction_date or now[:10],
                    payload.note,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE holdings
                SET shares = ?, avg_cost = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_shares, new_avg_cost, now, holding_id),
            )
            holding = _holding_from_row(_fetch_holding(conn, holding_id))
        return JSONResponse(content={"status": "success", "data": holding})
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Add my holding transaction failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)


@router.get("/api/my_holdings/{holding_id}/review")
def review_holding(holding_id: int):
    try:
        with _connect() as conn:
            row = _fetch_holding(conn, holding_id)
            payload = _build_review(row)
        return JSONResponse(content=payload)
    except HTTPException:
        raise
    except Exception as exc:
        error_log.error("Review my holding failed\n" + traceback.format_exc())
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)
