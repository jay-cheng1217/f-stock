"""Build NAV-based Champion attribution artifacts.

This report is intentionally ledger-first: it reads unified_positions and
unified_marks, reconstructs a daily additive NAV curve from position marks,
and then builds a sector-level Brinson-style attribution view. It does not
change selection, allocation, exit, or paper-book state.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.universe import filter_out_etfs_df  # noqa: E402
from scripts.send_weekly_summary import compute_strategy_fit_benchmark, compute_twii_benchmark  # noqa: E402

DEFAULT_CHAMPION_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_STOCK_DB_PATH = BASE_DIR / "stock.duckdb"
DEFAULT_INDEX_PATH = Path(INDEX_DIR) / "index_TWII.csv"
WEEK1_REFERENCE_JSON = Path(REPORT_DIR) / "sector_beta_audit_20260423_20260429.json"
OPEN_WINNER_TICKERS = ("8027", "3450", "6426", "5228")


@dataclass(frozen=True)
class NavArtifacts:
    nav: pd.DataFrame
    attribution: pd.DataFrame
    summary: dict[str, Any]


def _coerce_date(value: str | pd.Timestamp) -> pd.Timestamp:
    date = pd.Timestamp(value).normalize()
    if pd.isna(date):
        raise ValueError(f"Invalid date: {value}")
    return date


def _date_str(value: str | pd.Timestamp) -> str:
    return _coerce_date(value).strftime("%Y-%m-%d")


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _load_trading_calendar(
    start_date: str,
    end_date: str,
    *,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> list[pd.Timestamp]:
    path = Path(index_path)
    if not path.exists():
        return []
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    df = pd.read_csv(path, encoding="utf-8-sig")
    date_col = "Date" if "Date" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    mask = (df[date_col] >= start) & (df[date_col] <= end)
    return [pd.Timestamp(value).normalize() for value in df.loc[mask, date_col]]


def _load_positions_and_marks(db_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Champion DB not found: {path}")
    with sqlite3.connect(path) as conn:
        positions = pd.read_sql_query(
            """
            SELECT *
            FROM unified_positions
            ORDER BY id
            """,
            conn,
        )
        marks = pd.read_sql_query(
            """
            SELECT *
            FROM unified_marks
            ORDER BY position_id, mark_date
            """,
            conn,
        )
    for col in ("prediction_date", "entry_date", "exit_date", "last_mark_date"):
        if col in positions.columns:
            positions[col] = pd.to_datetime(positions[col], errors="coerce")
    if not marks.empty:
        marks["mark_date"] = pd.to_datetime(marks["mark_date"], errors="coerce")
    return positions, marks


def build_daily_nav(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> pd.DataFrame:
    """Return daily additive NAV and contribution series.

    unified_marks.close_return_pct stores cumulative return from entry. The
    daily contribution is therefore the per-position cumulative delta times the
    target weight. Summing those deltas gives a ledger-reconcilable additive NAV
    curve while cash contributes 0%.
    """

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    calendar = _load_trading_calendar(start_date, end_date, index_path=index_path)
    if not calendar:
        calendar = [start + pd.Timedelta(days=i) for i in range((end - start).days + 1)]
    nav = pd.DataFrame({"date": pd.to_datetime(calendar)})
    nav["date"] = nav["date"].dt.normalize()
    nav = nav[(nav["date"] >= start) & (nav["date"] <= end)].copy()

    base_cols = {
        "daily_return_pct": 0.0,
        "realized_pnl_pct": 0.0,
        "unrealized_pnl_pct": 0.0,
        "exit_pnl_pct": 0.0,
        "execution_pnl_pct": 0.0,
        "open_mtm_pct": 0.0,
        "gross_exposure": 0.0,
        "cash_weight": 1.0,
        "cash_drag_vs_twii_pct": 0.0,
        "active_positions": 0,
        "exit_positions": 0,
        "marked_positions": 0,
    }
    for col, default in base_cols.items():
        nav[col] = default

    if positions.empty or marks.empty:
        nav["nav"] = 1.0
        nav["nav_return_pct"] = 0.0
        return nav

    pos = positions.copy()
    mk = marks.copy()
    for col in ("entry_date", "exit_date", "last_mark_date"):
        if col in pos.columns:
            pos[col] = pd.to_datetime(pos[col], errors="coerce")
    if "mark_date" in mk.columns:
        mk["mark_date"] = pd.to_datetime(mk["mark_date"], errors="coerce")
    pos["id"] = pd.to_numeric(pos["id"], errors="coerce").astype("Int64")
    mk["position_id"] = pd.to_numeric(mk["position_id"], errors="coerce").astype("Int64")
    mk = mk.dropna(subset=["position_id", "mark_date"])
    merged = mk.merge(
        pos,
        left_on="position_id",
        right_on="id",
        how="left",
        suffixes=("_mark", "_position"),
    )
    if merged.empty:
        nav["nav"] = 1.0
        nav["nav_return_pct"] = 0.0
        return nav

    merged["target_weight"] = pd.to_numeric(merged["target_weight"], errors="coerce").fillna(0.0)
    merged["close_return_pct"] = pd.to_numeric(merged["close_return_pct"], errors="coerce").fillna(0.0)
    merged["realized_return_pct"] = pd.to_numeric(merged.get("realized_return_pct"), errors="coerce")
    exit_date = pd.to_datetime(merged.get("exit_date"), errors="coerce")
    merged["is_exit_row"] = (
        pd.to_numeric(merged.get("is_exit_day"), errors="coerce").fillna(0).astype(int).eq(1)
        | (exit_date.dt.normalize() == merged["mark_date"].dt.normalize())
    )
    merged["effective_return_pct"] = np.where(
        merged["is_exit_row"] & merged["realized_return_pct"].notna(),
        merged["realized_return_pct"],
        merged["close_return_pct"],
    )
    merged = merged.sort_values(["position_id", "mark_date"])
    merged["prev_return_pct"] = merged.groupby("position_id")["effective_return_pct"].shift(1).fillna(0.0)
    merged["daily_position_return_pct"] = merged["effective_return_pct"] - merged["prev_return_pct"]
    merged["weighted_daily_return_pct"] = merged["target_weight"] * merged["daily_position_return_pct"]
    in_window = merged[(merged["mark_date"] >= start) & (merged["mark_date"] <= end)].copy()
    if in_window.empty:
        nav["nav"] = 1.0
        nav["nav_return_pct"] = 0.0
        return nav

    in_window["open_mtm_component"] = np.where(
        in_window["is_exit_row"],
        0.0,
        in_window["target_weight"] * in_window["close_return_pct"],
    )
    in_window["unrealized_component"] = np.where(
        in_window["is_exit_row"],
        0.0,
        in_window["weighted_daily_return_pct"],
    )
    in_window["realized_component"] = np.where(
        in_window["is_exit_row"],
        in_window["weighted_daily_return_pct"],
        0.0,
    )
    grouped = in_window.groupby(in_window["mark_date"].dt.normalize()).agg(
        daily_return_pct=("weighted_daily_return_pct", "sum"),
        realized_pnl_pct=("realized_component", "sum"),
        unrealized_pnl_pct=("unrealized_component", "sum"),
        exit_pnl_pct=("realized_component", "sum"),
        open_mtm_pct=("open_mtm_component", "sum"),
        gross_exposure=("target_weight", "sum"),
        active_positions=("position_id", "nunique"),
        exit_positions=("is_exit_row", "sum"),
        marked_positions=("position_id", "nunique"),
    )
    nav = nav.merge(grouped.reset_index().rename(columns={"mark_date": "date"}), on="date", how="left", suffixes=("", "_calc"))
    for col in grouped.columns:
        calc_col = f"{col}_calc"
        if calc_col in nav.columns:
            nav[col] = pd.to_numeric(nav[calc_col], errors="coerce").fillna(nav[col])
            nav = nav.drop(columns=[calc_col])
    nav["gross_exposure"] = pd.to_numeric(nav["gross_exposure"], errors="coerce").fillna(0.0).clip(lower=0.0)
    nav["cash_weight"] = (1.0 - nav["gross_exposure"]).clip(lower=0.0)

    twii = _load_twii_daily_returns(start_date, end_date, index_path=index_path)
    nav = nav.merge(twii, on="date", how="left")
    nav["twii_daily_return_pct"] = pd.to_numeric(nav["twii_daily_return_pct"], errors="coerce").fillna(0.0)
    nav["cash_drag_vs_twii_pct"] = (nav["gross_exposure"].clip(upper=1.0) - 1.0) * nav["twii_daily_return_pct"]

    if "entry_date" in pos.columns and "entry_slippage_pct" in pos.columns:
        entries = pos.dropna(subset=["entry_date"]).copy()
        entries["date"] = pd.to_datetime(entries["entry_date"], errors="coerce").dt.normalize()
        entries["entry_slippage_pct"] = pd.to_numeric(entries["entry_slippage_pct"], errors="coerce").fillna(0.0)
        entries["target_weight"] = pd.to_numeric(entries["target_weight"], errors="coerce").fillna(0.0)
        entries["execution_component"] = entries["target_weight"] * entries["entry_slippage_pct"]
        exec_by_day = entries.groupby("date")["execution_component"].sum().rename("execution_pnl_pct").reset_index()
        nav = nav.drop(columns=["execution_pnl_pct"]).merge(exec_by_day, on="date", how="left")
        nav["execution_pnl_pct"] = pd.to_numeric(nav["execution_pnl_pct"], errors="coerce").fillna(0.0)

    nav["daily_return_pct"] = pd.to_numeric(nav["daily_return_pct"], errors="coerce").fillna(0.0)
    nav["nav_return_pct"] = nav["daily_return_pct"].cumsum()
    nav["nav"] = 1.0 + nav["nav_return_pct"]
    return nav


def _load_twii_daily_returns(
    start_date: str,
    end_date: str,
    *,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> pd.DataFrame:
    path = Path(index_path)
    if not path.exists():
        return pd.DataFrame({"date": [], "twii_daily_return_pct": []})
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    lookback = start - pd.Timedelta(days=14)
    df = pd.read_csv(path, encoding="utf-8-sig")
    date_col = "Date" if "Date" in df.columns else "date"
    close_col = "Close" if "Close" in df.columns else "close"
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
    df = df.dropna(subset=[date_col, close_col]).sort_values(date_col)
    df = df[(df[date_col] >= lookback) & (df[date_col] <= end)].copy()
    df["twii_daily_return_pct"] = df[close_col].pct_change()
    out = df[(df[date_col] >= start) & (df[date_col] <= end)][[date_col, "twii_daily_return_pct"]].copy()
    return out.rename(columns={date_col: "date"})


def _prediction_paths_between(start_date: str, end_date: str, *, model_dir: str | Path = MODEL_DIR) -> list[Path]:
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    paths: list[tuple[pd.Timestamp, Path]] = []
    for path in Path(model_dir).glob("predictions_*.csv"):
        raw_date = path.stem.replace("predictions_", "")
        try:
            date = _coerce_date(raw_date)
        except Exception:
            continue
        if start <= date <= end:
            paths.append((date, path))
    return [path for _, path in sorted(paths)]


def _load_benchmark_sector_returns(
    start_date: str,
    end_date: str,
    *,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
) -> pd.DataFrame:
    paths = _prediction_paths_between(start_date, end_date, model_dir=model_dir)
    frames: list[pd.DataFrame] = []
    for path in paths:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
        if "ticker" not in df.columns:
            continue
        df = filter_out_etfs_df(df)
        sector_col = "sector" if "sector" in df.columns else None
        if not sector_col:
            continue
        frames.append(df[["ticker", sector_col]].rename(columns={sector_col: "sector"}))
    if not frames:
        return pd.DataFrame(columns=["sector", "benchmark_weight", "sector_return_pct"])
    universe = pd.concat(frames, ignore_index=True).dropna(subset=["ticker"]).drop_duplicates("ticker")
    universe["ticker"] = universe["ticker"].astype(str)
    universe["sector"] = universe["sector"].fillna("UNKNOWN").astype(str)

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    lookback = start - pd.Timedelta(days=14)
    stock_db = Path(stock_db_path)
    prices = pd.DataFrame()
    if stock_db.exists():
        try:
            import duckdb

            con = duckdb.connect(str(stock_db), read_only=True)
            try:
                con.register("ticker_filter", universe[["ticker"]].rename(columns={"ticker": "Ticker"}))
                prices = con.execute(
                    """
                    SELECT d.Ticker, d.Date, d.Close
                    FROM daily_k d
                    JOIN ticker_filter f ON d.Ticker = f.Ticker
                    WHERE d.Date BETWEEN ? AND ?
                      AND d.Close IS NOT NULL
                    ORDER BY d.Ticker, d.Date
                    """,
                    [lookback.date(), end.date()],
                ).fetchdf()
            finally:
                con.close()
        except Exception:
            prices = pd.DataFrame()
    if prices.empty:
        frames: list[pd.DataFrame] = []
        for ticker in universe["ticker"].astype(str).tolist():
            path = Path(DAILY_K_DIR) / f"{ticker}.csv"
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path, encoding="utf-8-sig", usecols=["Date", "Close"])
            except Exception:
                continue
            df["Ticker"] = ticker
            frames.append(df[["Ticker", "Date", "Close"]])
        prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if prices.empty:
        return pd.DataFrame(columns=["sector", "benchmark_weight", "sector_return_pct"])
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
    prices = prices.dropna(subset=["Date", "Close"]).sort_values(["Ticker", "Date"])
    prices["daily_return"] = prices.groupby("Ticker")["Close"].pct_change()
    prices = prices[(prices["Date"] > start) & (prices["Date"] <= end)].copy()
    prices = prices.merge(universe, left_on="Ticker", right_on="ticker", how="left")
    if prices.empty:
        return pd.DataFrame(columns=["sector", "benchmark_weight", "sector_return_pct"])
    sector_daily = prices.groupby(["sector", "Date"])["daily_return"].mean().dropna().reset_index()
    sector_return = sector_daily.groupby("sector")["daily_return"].apply(lambda s: float((1.0 + s).prod() - 1.0))
    counts = universe.groupby("sector")["ticker"].nunique()
    weights = counts / counts.sum()
    out = pd.DataFrame(
        {
            "sector": weights.index,
            "benchmark_weight": weights.values,
            "sector_return_pct": [sector_return.get(sector, np.nan) for sector in weights.index],
        }
    )
    return out


def build_sector_attribution(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    nav: pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
) -> pd.DataFrame:
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if positions.empty or marks.empty:
        return pd.DataFrame()
    pos = positions.copy()
    mk = marks.copy()
    mk["position_id"] = pd.to_numeric(mk["position_id"], errors="coerce").astype("Int64")
    mk["mark_date"] = pd.to_datetime(mk["mark_date"], errors="coerce")
    merged = mk.merge(pos, left_on="position_id", right_on="id", how="left", suffixes=("_mark", "_position"))
    merged = merged[(merged["mark_date"] >= start) & (merged["mark_date"] <= end)].copy()
    if merged.empty:
        return pd.DataFrame()
    merged["sector"] = merged["sector"].fillna("UNKNOWN").astype(str)
    merged["target_weight"] = pd.to_numeric(merged["target_weight"], errors="coerce").fillna(0.0)
    merged["close_return_pct"] = pd.to_numeric(merged["close_return_pct"], errors="coerce").fillna(0.0)
    merged["realized_return_pct"] = pd.to_numeric(merged.get("realized_return_pct"), errors="coerce")
    exit_date = pd.to_datetime(merged.get("exit_date"), errors="coerce")
    merged["is_exit_row"] = (
        pd.to_numeric(merged.get("is_exit_day"), errors="coerce").fillna(0).astype(int).eq(1)
        | (exit_date.dt.normalize() == merged["mark_date"].dt.normalize())
    )
    merged["effective_return_pct"] = np.where(
        merged["is_exit_row"] & merged["realized_return_pct"].notna(),
        merged["realized_return_pct"],
        merged["close_return_pct"],
    )
    merged = merged.sort_values(["position_id", "mark_date"])
    merged["prev_return_pct"] = merged.groupby("position_id")["effective_return_pct"].shift(1).fillna(0.0)
    merged["daily_return_delta"] = merged["effective_return_pct"] - merged["prev_return_pct"]
    merged["sector_pnl"] = merged["target_weight"] * merged["daily_return_delta"]

    daily_sector_weight = (
        merged.groupby(["sector", merged["mark_date"].dt.normalize()])["target_weight"].sum().reset_index()
    )
    avg_weights = daily_sector_weight.groupby("sector")["target_weight"].mean()
    sector_pnl = merged.groupby("sector")["sector_pnl"].sum()
    champion = pd.DataFrame({"sector": sorted(set(avg_weights.index) | set(sector_pnl.index))})
    champion["champion_weight"] = champion["sector"].map(avg_weights).fillna(0.0)
    champion["champion_contribution_pct"] = champion["sector"].map(sector_pnl).fillna(0.0)
    champion["champion_sector_return_pct"] = np.where(
        champion["champion_weight"] > 0,
        champion["champion_contribution_pct"] / champion["champion_weight"],
        np.nan,
    )
    champion["champion_positions"] = champion["sector"].map(merged.groupby("sector")["position_id"].nunique()).fillna(0).astype(int)

    benchmark = _load_benchmark_sector_returns(
        start_date,
        end_date,
        stock_db_path=stock_db_path,
        model_dir=model_dir,
    )
    sectors = sorted(set(champion["sector"]) | set(benchmark.get("sector", [])))
    out = pd.DataFrame({"sector": sectors})
    out = out.merge(champion, on="sector", how="left")
    if not benchmark.empty:
        out = out.merge(benchmark, on="sector", how="left")
    else:
        out["benchmark_weight"] = np.nan
        out["sector_return_pct"] = np.nan
    out["champion_weight"] = pd.to_numeric(out["champion_weight"], errors="coerce").fillna(0.0)
    out["benchmark_weight"] = pd.to_numeric(out["benchmark_weight"], errors="coerce").fillna(0.0)
    out["champion_sector_return_pct"] = pd.to_numeric(out["champion_sector_return_pct"], errors="coerce")
    out["sector_return_pct"] = pd.to_numeric(out["sector_return_pct"], errors="coerce")
    benchmark_return = float((out["benchmark_weight"] * out["sector_return_pct"].fillna(0.0)).sum())
    out["active_weight"] = out["champion_weight"] - out["benchmark_weight"]
    out["allocation_effect_pct"] = out["active_weight"] * (out["sector_return_pct"].fillna(benchmark_return) - benchmark_return)
    out["selection_effect_pct"] = out["champion_weight"] * (
        out["champion_sector_return_pct"].fillna(out["sector_return_pct"]) - out["sector_return_pct"].fillna(benchmark_return)
    )
    out["total_effect_pct"] = out["allocation_effect_pct"] + out["selection_effect_pct"]
    out["benchmark_contribution_pct"] = out["benchmark_weight"] * out["sector_return_pct"].fillna(0.0)
    champion_return = float(nav["nav_return_pct"].iloc[-1]) if not nav.empty else 0.0
    benchmark_total = float(out["benchmark_contribution_pct"].fillna(0.0).sum())
    effect_total = float(out["total_effect_pct"].fillna(0.0).sum())
    exposure_residual = (champion_return - benchmark_total) - effect_total
    if abs(exposure_residual) > 1e-12:
        out = pd.concat(
            [
                out,
                pd.DataFrame(
                    [
                        {
                            "sector": "CASH_AND_GROSS_EXPOSURE",
                            "champion_weight": 0.0,
                            "champion_contribution_pct": 0.0,
                            "champion_sector_return_pct": np.nan,
                            "champion_positions": 0,
                            "benchmark_weight": 0.0,
                            "sector_return_pct": benchmark_total,
                            "active_weight": 0.0,
                            "allocation_effect_pct": exposure_residual,
                            "selection_effect_pct": 0.0,
                            "total_effect_pct": exposure_residual,
                            "benchmark_contribution_pct": 0.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    out = out.sort_values(["champion_weight", "benchmark_weight"], ascending=[False, False]).reset_index(drop=True)
    return out


def _latest_open_winners(positions: pd.DataFrame, marks: pd.DataFrame, end_date: str) -> list[dict[str, Any]]:
    if positions.empty:
        return [
            {
                "ticker": ticker,
                "status": "not_in_ledger",
                "mark_date": None,
                "close_return_pct": None,
                "target_weight": None,
                "weighted_open_mtm_pct": None,
            }
            for ticker in OPEN_WINNER_TICKERS
        ]
    end = _coerce_date(end_date)
    pos = positions[positions["ticker"].astype(str).isin(OPEN_WINNER_TICKERS)].copy()
    if marks.empty:
        latest = pd.DataFrame()
    else:
        mk = marks.copy()
        mk["mark_date"] = pd.to_datetime(mk["mark_date"], errors="coerce")
        mk = mk[mk["mark_date"] <= end].sort_values(["position_id", "mark_date"])
        latest = mk.groupby("position_id").tail(1)
    merged = (
        latest.merge(pos, left_on="position_id", right_on="id", how="inner", suffixes=("_mark", "_position"))
        if not latest.empty and not pos.empty
        else pd.DataFrame()
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in merged.to_dict("records"):
        seen.add(str(row.get("ticker")))
        rows.append(
            {
                "ticker": str(row.get("ticker")),
                "status": row.get("status"),
                "mark_date": _date_str(row.get("mark_date")),
                "close_return_pct": float(row.get("close_return_pct") or 0.0),
                "target_weight": float(row.get("target_weight") or 0.0),
                "weighted_open_mtm_pct": float(row.get("target_weight") or 0.0) * float(row.get("close_return_pct") or 0.0),
            }
        )
    for ticker in OPEN_WINNER_TICKERS:
        if ticker in seen:
            continue
        ticker_positions = pos[pos["ticker"].astype(str) == ticker].sort_values("prediction_date")
        if ticker_positions.empty:
            rows.append(
                {
                    "ticker": ticker,
                    "status": "not_in_ledger",
                    "mark_date": None,
                    "close_return_pct": None,
                    "target_weight": None,
                    "weighted_open_mtm_pct": None,
                }
            )
        else:
            latest_position = ticker_positions.tail(1).iloc[0].to_dict()
            rows.append(
                {
                    "ticker": ticker,
                    "status": latest_position.get("status"),
                    "mark_date": None,
                    "close_return_pct": None,
                    "target_weight": float(latest_position.get("target_weight") or 0.0),
                    "weighted_open_mtm_pct": None,
                }
            )
    return rows


def _load_week1_reference(start_date: str, end_date: str) -> dict[str, Any] | None:
    if start_date != "2026-04-23" or end_date != "2026-04-29" or not WEEK1_REFERENCE_JSON.exists():
        return None
    data = json.loads(WEEK1_REFERENCE_JSON.read_text(encoding="utf-8"))
    return data.get("summary", {}).get("reconciliation")


def _ledger_snapshot(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    as_of_date: str | pd.Timestamp,
) -> dict[str, Any]:
    end = _coerce_date(as_of_date)
    if positions.empty:
        return {
            "ledger_realized_pnl_pct": 0.0,
            "ledger_open_mtm_pct": 0.0,
            "ledger_total_pnl_pct": 0.0,
            "realized_positions": 0,
            "active_positions": 0,
            "marked_active_positions": 0,
        }
    pos = positions.copy()
    pos["target_weight"] = pd.to_numeric(pos["target_weight"], errors="coerce").fillna(0.0)
    pos["realized_return_pct"] = pd.to_numeric(pos.get("realized_return_pct"), errors="coerce")
    pos["entry_date"] = pd.to_datetime(pos.get("entry_date"), errors="coerce")
    pos["exit_date"] = pd.to_datetime(pos.get("exit_date"), errors="coerce")
    realized = pos[(pos["exit_date"].notna()) & (pos["exit_date"] <= end)].copy()
    realized_pnl = float((realized["target_weight"] * realized["realized_return_pct"].fillna(0.0)).sum())

    if marks.empty:
        open_mtm = 0.0
    else:
        mk = marks.copy()
        mk["mark_date"] = pd.to_datetime(mk["mark_date"], errors="coerce")
        mk = mk[mk["mark_date"] <= end].sort_values(["position_id", "mark_date"])
        latest = mk.groupby("position_id").tail(1)
        active_pos = pos[
            pos["entry_date"].notna()
            & (pos["entry_date"] <= end)
            & (pos["exit_date"].isna() | (pos["exit_date"] > end))
        ].copy()
        merged = latest.merge(active_pos, left_on="position_id", right_on="id", how="inner", suffixes=("_mark", "_position"))
        merged["close_return_pct"] = pd.to_numeric(merged["close_return_pct"], errors="coerce").fillna(0.0)
        merged["target_weight"] = pd.to_numeric(merged["target_weight"], errors="coerce").fillna(0.0)
        open_mtm = float((merged["target_weight"] * merged["close_return_pct"]).sum())
    ledger_total = realized_pnl + open_mtm
    return {
        "ledger_realized_pnl_pct": realized_pnl,
        "ledger_open_mtm_pct": open_mtm,
        "ledger_total_pnl_pct": ledger_total,
        "realized_positions": int(len(realized)),
        "active_positions": int(len(active_pos)) if not marks.empty else 0,
        "marked_active_positions": int(len(merged)) if not marks.empty else 0,
    }


def _build_reconciliation(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    nav: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start = _coerce_date(start_date)
    baseline_date = start - pd.Timedelta(days=1)
    nav_return = float(nav["nav_return_pct"].iloc[-1]) if not nav.empty else 0.0
    baseline = _ledger_snapshot(positions, marks, baseline_date)
    end_snapshot = _ledger_snapshot(positions, marks, end_date)
    realized_pnl = end_snapshot["ledger_realized_pnl_pct"] - baseline["ledger_realized_pnl_pct"]
    open_mtm = end_snapshot["ledger_open_mtm_pct"] - baseline["ledger_open_mtm_pct"]
    ledger_total = end_snapshot["ledger_total_pnl_pct"] - baseline["ledger_total_pnl_pct"]
    error = nav_return - ledger_total
    return {
        "nav_return_pct": nav_return,
        "baseline_date": _date_str(baseline_date),
        "ledger_baseline_pnl_pct": baseline["ledger_total_pnl_pct"],
        "ledger_cumulative_pnl_pct": end_snapshot["ledger_total_pnl_pct"],
        "ledger_realized_pnl_pct": realized_pnl,
        "ledger_open_mtm_pct": open_mtm,
        "ledger_total_pnl_pct": ledger_total,
        "legacy_cumulative_reconciliation_error_pct": nav_return - end_snapshot["ledger_total_pnl_pct"],
        "reconciliation_error_pct": error,
        "reconciliation_pass": abs(error) <= 0.005,
        "baseline_snapshot": baseline,
        "end_snapshot": end_snapshot,
    }


def _split_attribution_residual(nav: pd.DataFrame, residual_total: float) -> dict[str, float]:
    """Split attribution residual into observable proxy buckets.

    This is intentionally conservative: execution/slippage and exit-policy
    effects are observable ledger proxies, while the remainder stays in an
    explicit unexplained reconciliation bucket instead of being hidden inside
    cash / gross exposure.
    """

    execution = float(nav["execution_pnl_pct"].sum()) if "execution_pnl_pct" in nav and not nav.empty else 0.0
    exit_policy = float(nav["exit_pnl_pct"].sum()) if "exit_pnl_pct" in nav and not nav.empty else 0.0
    unexplained = float(residual_total) - execution - exit_policy
    return {
        "residual_total_pct": float(residual_total),
        "execution_slippage_effect_pct": execution,
        "exit_policy_effect_pct": exit_policy,
        "unexplained_reconciliation_residual_pct": unexplained,
    }


def build_nav_attribution(
    start_date: str,
    end_date: str,
    *,
    champion_db_path: str | Path = DEFAULT_CHAMPION_DB_PATH,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> NavArtifacts:
    positions, marks = _load_positions_and_marks(champion_db_path)
    nav = build_daily_nav(positions, marks, start_date, end_date, index_path=index_path)
    attribution = build_sector_attribution(
        positions,
        marks,
        nav,
        start_date,
        end_date,
        stock_db_path=stock_db_path,
        model_dir=model_dir,
    )
    twii = compute_twii_benchmark(start_date, end_date, index_path=index_path)
    try:
        strategy_fit = compute_strategy_fit_benchmark(
            start_date,
            end_date,
            stock_db_path=stock_db_path,
            model_dir=model_dir,
        )
    except Exception as exc:
        strategy_fit = {
            "return_pct": None,
            "source": str(stock_db_path),
            "method": "unavailable",
            "error": str(exc),
        }
    nav_return = float(nav["nav_return_pct"].iloc[-1]) if not nav.empty else 0.0
    benchmark_return = float(attribution["benchmark_contribution_pct"].fillna(0.0).sum()) if not attribution.empty else None
    if strategy_fit.get("return_pct") is None and benchmark_return is not None:
        strategy_fit = {
            **strategy_fit,
            "return_pct": benchmark_return,
            "method": "sector proxy fallback from prediction-universe equal-weight sectors",
        }
    allocation = float(attribution["allocation_effect_pct"].fillna(0.0).sum()) if not attribution.empty else None
    selection = float(attribution["selection_effect_pct"].fillna(0.0).sum()) if not attribution.empty else None
    alpha = nav_return - benchmark_return if benchmark_return is not None else None
    attribution_error = (allocation + selection - alpha) if None not in (allocation, selection, alpha) else None
    gross_exposure_cash_residual = float(
        attribution.loc[
            attribution["sector"].eq("CASH_AND_GROSS_EXPOSURE"),
            "total_effect_pct",
        ].fillna(0.0).sum()
    ) if not attribution.empty and "sector" in attribution.columns else 0.0
    residual_split = _split_attribution_residual(nav, gross_exposure_cash_residual)
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "champion_db_path": str(champion_db_path),
        "nav": {
            "return_pct": nav_return,
            "daily_rows": int(len(nav)),
            "marked_days": int((nav["marked_positions"] > 0).sum()) if "marked_positions" in nav else 0,
            "cash_only_days": int((nav["gross_exposure"].fillna(0.0) <= 0).sum()) if "gross_exposure" in nav else 0,
            "avg_gross_exposure": float(nav["gross_exposure"].mean()) if "gross_exposure" in nav and not nav.empty else 0.0,
            "realized_pnl_pct": float(nav["realized_pnl_pct"].sum()) if "realized_pnl_pct" in nav else 0.0,
            "unrealized_pnl_pct": float(nav["unrealized_pnl_pct"].sum()) if "unrealized_pnl_pct" in nav else 0.0,
            "open_mtm_pct": float(nav["open_mtm_pct"].iloc[-1]) if "open_mtm_pct" in nav and not nav.empty else 0.0,
            "cash_drag_vs_twii_pct": float(nav["cash_drag_vs_twii_pct"].sum()) if "cash_drag_vs_twii_pct" in nav else 0.0,
            "execution_pnl_pct": float(nav["execution_pnl_pct"].sum()) if "execution_pnl_pct" in nav else 0.0,
            "exit_pnl_pct": float(nav["exit_pnl_pct"].sum()) if "exit_pnl_pct" in nav else 0.0,
        },
        "benchmarks": {
            "twii_return_pct": twii.get("return_pct"),
            "strategy_fit_return_pct": strategy_fit.get("return_pct"),
            "sector_proxy_return_pct": benchmark_return,
        },
        "attribution": {
            "allocation_effect_pct": allocation,
            "selection_effect_pct": selection,
            "gross_exposure_cash_residual_pct": gross_exposure_cash_residual,
            "residual_split": residual_split,
            "execution_slippage_effect_pct": residual_split["execution_slippage_effect_pct"],
            "exit_policy_effect_pct": residual_split["exit_policy_effect_pct"],
            "unexplained_reconciliation_residual_pct": residual_split[
                "unexplained_reconciliation_residual_pct"
            ],
            "allocation_plus_selection_pct": (allocation + selection) if None not in (allocation, selection) else None,
            "champion_vs_sector_proxy_alpha_pct": alpha,
            "reconciliation_error_pct": attribution_error,
            "reconciliation_pass": abs(attribution_error or 0.0) <= 0.005 if attribution_error is not None else False,
        },
        "ledger_reconciliation": _build_reconciliation(positions, marks, nav, start_date, end_date),
        "open_winners": _latest_open_winners(positions, marks, end_date),
        "week1_reference": _load_week1_reference(start_date, end_date),
    }
    return NavArtifacts(nav=nav, attribution=attribution, summary=summary)


def _write_markdown(path: Path, artifacts: NavArtifacts) -> None:
    summary = artifacts.summary
    lines = [
        f"# Champion NAV Attribution ({summary['start_date']} ~ {summary['end_date']})",
        "",
        "## NAV Reconciliation",
        f"- NAV return: {_pct(summary['nav']['return_pct'])}",
        f"- Ledger baseline cumulative P&L ({summary['ledger_reconciliation'].get('baseline_date')}): {_pct(summary['ledger_reconciliation'].get('ledger_baseline_pnl_pct'))}",
        f"- Ledger end cumulative P&L: {_pct(summary['ledger_reconciliation'].get('ledger_cumulative_pnl_pct'))}",
        f"- Ledger realized P&L delta: {_pct(summary['ledger_reconciliation']['ledger_realized_pnl_pct'])}",
        f"- Ledger open MTM delta: {_pct(summary['ledger_reconciliation']['ledger_open_mtm_pct'])}",
        f"- Ledger total P&L delta: {_pct(summary['ledger_reconciliation']['ledger_total_pnl_pct'])}",
        f"- Legacy cumulative-error check: {_pct(summary['ledger_reconciliation'].get('legacy_cumulative_reconciliation_error_pct'), 4)}",
        f"- Reconciliation error: {_pct(summary['ledger_reconciliation']['reconciliation_error_pct'], 4)}",
        f"- Reconciliation pass: {summary['ledger_reconciliation']['reconciliation_pass']}",
        "",
        "## Contribution Split",
        f"- Realized P&L contribution: {_pct(summary['nav']['realized_pnl_pct'])}",
        f"- Unrealized daily P&L contribution: {_pct(summary['nav']['unrealized_pnl_pct'])}",
        f"- Open MTM at period end: {_pct(summary['nav']['open_mtm_pct'])}",
        f"- Cash drag vs TWII: {_pct(summary['nav']['cash_drag_vs_twii_pct'])}",
        f"- Average gross exposure: {_pct(summary['nav']['avg_gross_exposure'])}",
        f"- Execution contribution: {_pct(summary['nav']['execution_pnl_pct'])}",
        f"- Exit-day contribution: {_pct(summary['nav']['exit_pnl_pct'])}",
        "",
        "## Sector Attribution",
        f"- Sector proxy return: {_pct(summary['benchmarks']['sector_proxy_return_pct'])}",
        f"- Champion alpha vs sector proxy: {_pct(summary['attribution']['champion_vs_sector_proxy_alpha_pct'])}",
        f"- Allocation effect: {_pct(summary['attribution']['allocation_effect_pct'])}",
        f"- Selection effect: {_pct(summary['attribution']['selection_effect_pct'])}",
        f"- Gross exposure / cash residual: {_pct(summary['attribution']['gross_exposure_cash_residual_pct'])}",
        f"- Allocation + selection: {_pct(summary['attribution']['allocation_plus_selection_pct'])}",
        f"- Attribution reconciliation error: {_pct(summary['attribution']['reconciliation_error_pct'], 4)}",
        "",
        "## Residual Split",
        f"- Residual total: {_pct(summary['attribution']['residual_split']['residual_total_pct'])}",
        f"- Execution / slippage effect: {_pct(summary['attribution']['residual_split']['execution_slippage_effect_pct'])}",
        f"- Exit policy effect: {_pct(summary['attribution']['residual_split']['exit_policy_effect_pct'])}",
        (
            "- Unexplained reconciliation residual: "
            f"{_pct(summary['attribution']['residual_split']['unexplained_reconciliation_residual_pct'])}"
        ),
        "",
        "## Benchmarks",
        f"- TWII: {_pct(summary['benchmarks']['twii_return_pct'])}",
        f"- Strategy-fit: {_pct(summary['benchmarks']['strategy_fit_return_pct'])}",
        "",
    ]
    if summary.get("week1_reference"):
        ref = summary["week1_reference"]
        lines.extend(
            [
                "## Week1 Reference Comparison",
                f"- Reference champion vs benchmark alpha: {_pct(ref.get('champion_vs_benchmark_alpha_pct'))}",
                f"- Reference allocation effect: {_pct(ref.get('allocation_effect_total_pct'))}",
                f"- Reference selection effect: {_pct(ref.get('selection_effect_total_pct'))}",
                f"- Reference allocation + selection: {_pct(ref.get('allocation_plus_selection_pct'))}",
                "",
            ]
        )
    lines.append("## Open Winners Check")
    winners = summary.get("open_winners", [])
    if winners:
        lines.append("| ticker | status | mark_date | close return | weighted open MTM |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in winners:
            lines.append(
                f"| {row['ticker']} | {row['status']} | {row['mark_date']} | "
                f"{_pct(row.get('close_return_pct'))} | {_pct(row.get('weighted_open_mtm_pct'))} |"
            )
    else:
        lines.append("- No configured open winners found in this window.")
    lines.extend(["", "## Top Sector Rows"])
    if artifacts.attribution.empty:
        lines.append("- No sector attribution rows.")
    else:
        rows = artifacts.attribution.head(12)
        lines.append("| sector | champion wt | benchmark wt | allocation | selection | total |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for row in rows.to_dict("records"):
            lines.append(
                f"| {row['sector']} | {_pct(row.get('champion_weight'))} | "
                f"{_pct(row.get('benchmark_weight'))} | {_pct(row.get('allocation_effect_pct'))} | "
                f"{_pct(row.get('selection_effect_pct'))} | {_pct(row.get('total_effect_pct'))} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(
    artifacts: NavArtifacts,
    *,
    report_dir: str | Path = REPORT_DIR,
    prefix: str | None = None,
) -> dict[str, str]:
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    suffix = f"{artifacts.summary['start_date'].replace('-', '')}_{artifacts.summary['end_date'].replace('-', '')}"
    base = prefix or f"champion_nav_attribution_{suffix}"
    nav_path = report_path / f"{base}_nav.csv"
    attribution_path = report_path / f"{base}_sectors.csv"
    json_path = report_path / f"{base}.json"
    md_path = report_path / f"{base}.md"
    nav_out = artifacts.nav.copy()
    nav_out["date"] = pd.to_datetime(nav_out["date"]).dt.strftime("%Y-%m-%d")
    nav_out.to_csv(nav_path, index=False, encoding="utf-8-sig")
    artifacts.attribution.to_csv(attribution_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(artifacts.summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(md_path, artifacts)
    return {
        "nav_csv": str(nav_path),
        "sector_csv": str(attribution_path),
        "json": str(json_path),
        "md": str(md_path),
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Build NAV-based Champion attribution artifacts.")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--stock-db", default=str(DEFAULT_STOCK_DB_PATH))
    parser.add_argument("--model-dir", default=str(MODEL_DIR))
    parser.add_argument("--index-path", default=str(DEFAULT_INDEX_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    artifacts = build_nav_attribution(
        args.start,
        args.end,
        champion_db_path=args.champion_db,
        stock_db_path=args.stock_db,
        model_dir=args.model_dir,
        index_path=args.index_path,
    )
    paths = write_artifacts(artifacts, report_dir=args.report_dir, prefix=args.prefix)
    print(
        "[champion-nav] "
        f"nav_return={_pct(artifacts.summary['nav']['return_pct'])} "
        f"ledger_error={_pct(artifacts.summary['ledger_reconciliation']['reconciliation_error_pct'], 4)} "
        f"allocation={_pct(artifacts.summary['attribution']['allocation_effect_pct'])} "
        f"selection={_pct(artifacts.summary['attribution']['selection_effect_pct'])} "
        f"md={paths['md']}"
    )
    return {"paths": paths, "summary": artifacts.summary}


if __name__ == "__main__":
    main()
