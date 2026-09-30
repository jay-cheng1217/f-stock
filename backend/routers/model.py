from __future__ import annotations

import csv
import math
import os
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ml.config import BASE_DIR, INDEX_DIR, REPORT_DIR

router = APIRouter(prefix="/api/model", tags=["model"])

BACKTEST_PERIOD = "2024-01 ~ 2026-04"
BACKTEST_SUMMARY_FILENAME = "two_stage_cl3_backtest_20260509_summary.csv"
BACKTEST_MONTHLY_RETURNS_IMAGE = "two_stage_cl3_backtest_20260509_monthly_returns.png"
TREATMENT_VARIANT = "Treatment_TwoStage_N75"
BACKTEST_CHART_LINES = [
    {
        "key": "control_return",
        "color": "#2563eb",
        "label": "藍線：舊版回歸 Top30",
        "description": "舊版 V2 regression 直接選 Top30 的每月報酬",
    },
    {
        "key": "treatment_return",
        "color": "#dc2626",
        "label": "紅線：Two-Stage N=75",
        "description": "現行 Two-Stage Champion N=75 重排後 Top30 的每月報酬",
    },
]
OOS_START_DATE = date(2026, 5, 9)
OOS_EXPECTED_AVAILABLE_AFTER = "2026-06-06"
OOS_HOLD_TRADING_DAYS = 20
CHAMPION_DB_PATH = os.path.abspath(
    os.environ.get(
        "V2_CHAMPION_DB_PATH",
        os.path.join(BASE_DIR, "paper_portfolio_v2_champion.db"),
    )
)
OOS_CHART_LINES = [
    {
        "key": "champion",
        "color": "#10b981",
        "label": "綠線：Champion OOS",
        "description": "Two-Stage Champion 上線後，已成熟部位的實現報酬累計",
    },
    {
        "key": "twii",
        "color": "#60a5fa",
        "label": "藍線：TWII 同期基準",
        "description": "同一批成熟部位持有期間對應的加權指數報酬累計",
    },
]


def _summary_path() -> str:
    return os.path.join(REPORT_DIR, BACKTEST_SUMMARY_FILENAME)


def _monthly_returns_image_path() -> str:
    return os.path.join(REPORT_DIR, BACKTEST_MONTHLY_RETURNS_IMAGE)


def _pct(value: Any) -> float:
    return round(float(value) * 100.0, 2)


def _ratio(value: Any) -> float:
    return round(float(value), 2)


def _today() -> date:
    return date.today()


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _load_twii_close_series() -> tuple[list[date], dict[date, float]]:
    path = os.path.join(INDEX_DIR, "index_TWII.csv")
    if not os.path.exists(path):
        return [], {}

    dates: list[date] = []
    close_by_date: dict[date, float] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            row_date = _parse_date(row.get("Date"))
            close = _safe_float(row.get("Close"))
            if row_date is None or close is None:
                continue
            dates.append(row_date)
            close_by_date[row_date] = close
    dates = sorted(set(dates))
    return dates, close_by_date


def _maturity_date(entry_date: date, trading_dates: list[date]) -> date | None:
    start_index = bisect_left(trading_dates, entry_date)
    maturity_index = start_index + OOS_HOLD_TRADING_DAYS
    if start_index >= len(trading_dates) or maturity_index >= len(trading_dates):
        return None
    return trading_dates[maturity_index]


def _twii_return(
    entry_date: date,
    exit_date: date,
    trading_dates: list[date],
    close_by_date: dict[date, float],
) -> float | None:
    entry_index = bisect_left(trading_dates, entry_date)
    exit_index = bisect_right(trading_dates, exit_date) - 1
    if entry_index >= len(trading_dates) or exit_index < 0 or exit_index < entry_index:
        return None

    entry_close = close_by_date.get(trading_dates[entry_index])
    exit_close = close_by_date.get(trading_dates[exit_index])
    if entry_close is None or entry_close <= 0 or exit_close is None:
        return None
    return exit_close / entry_close - 1.0


def _load_oos_positions() -> list[sqlite3.Row]:
    if not os.path.exists(CHAMPION_DB_PATH):
        return []
    try:
        with sqlite3.connect(CHAMPION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return list(
                conn.execute(
                    """
                    SELECT
                        id,
                        ticker,
                        entry_date,
                        exit_date,
                        status,
                        realized_return_pct,
                        target_weight,
                        exit_reason
                    FROM unified_positions
                    WHERE status IN ('closed', 'stopped_out')
                      AND entry_date IS NOT NULL
                      AND exit_date IS NOT NULL
                      AND realized_return_pct IS NOT NULL
                      AND entry_date >= ?
                    ORDER BY exit_date, id
                    """,
                    (OOS_START_DATE.isoformat(),),
                )
            )
    except sqlite3.Error:
        return []


def _empty_oos_response(
    *,
    total_closed_after_start: int,
    immature_closed_count: int,
) -> dict[str, Any]:
    return {
        "status": "empty",
        "model": "Two-Stage Champion N=75",
        "strategy_start_date": OOS_START_DATE.isoformat(),
        "expected_available_after": OOS_EXPECTED_AVAILABLE_AFTER,
        "empty_message": f"OOS 資料累積中，預計 {OOS_EXPECTED_AVAILABLE_AFTER} 後可用",
        "mature_sample_count": 0,
        "total_closed_after_start": total_closed_after_start,
        "immature_closed_count": immature_closed_count,
        "latest_mature_exit_date": None,
        "monthly_excess_return_pct": None,
        "win_rate_pct": None,
        "champion_cumulative_return_pct": None,
        "twii_cumulative_return_pct": None,
        "chart_title": "Rolling OOS 累計報酬",
        "chart_note": "只納入 entry_date + 20 個交易日已成熟的 Two-Stage Champion 部位；未成熟部位不估算。",
        "chart_lines": OOS_CHART_LINES,
        "curve": [],
    }


def _build_oos_performance() -> dict[str, Any]:
    trading_dates, close_by_date = _load_twii_close_series()
    rows = _load_oos_positions()
    today = _today()
    mature_trades: list[dict[str, Any]] = []
    immature_closed_count = 0

    for row in rows:
        entry_date = _parse_date(row["entry_date"])
        exit_date = _parse_date(row["exit_date"])
        realized_return = _safe_float(row["realized_return_pct"])
        if entry_date is None or exit_date is None or realized_return is None:
            continue
        maturity = _maturity_date(entry_date, trading_dates)
        if maturity is None or maturity > today:
            immature_closed_count += 1
            continue
        benchmark_return = _twii_return(entry_date, exit_date, trading_dates, close_by_date)
        mature_trades.append(
            {
                "ticker": row["ticker"],
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "maturity_date": maturity.isoformat(),
                "status": row["status"],
                "exit_reason": row["exit_reason"],
                "realized_return": realized_return,
                "twii_return": benchmark_return,
            }
        )

    if not mature_trades:
        return _empty_oos_response(
            total_closed_after_start=len(rows),
            immature_closed_count=immature_closed_count,
        )

    by_exit_date: dict[str, list[dict[str, Any]]] = {}
    by_month: dict[str, list[dict[str, Any]]] = {}
    for trade in mature_trades:
        by_exit_date.setdefault(trade["exit_date"], []).append(trade)
        by_month.setdefault(trade["exit_date"][:7], []).append(trade)

    curve: list[dict[str, Any]] = []
    champion_cumulative = 1.0
    twii_cumulative = 1.0
    for exit_date in sorted(by_exit_date):
        bucket = by_exit_date[exit_date]
        champion_return = sum(item["realized_return"] for item in bucket) / len(bucket)
        twii_values = [
            item["twii_return"]
            for item in bucket
            if item["twii_return"] is not None
        ]
        twii_return = sum(twii_values) / len(twii_values) if twii_values else None
        champion_cumulative *= 1.0 + champion_return
        if twii_return is not None:
            twii_cumulative *= 1.0 + twii_return
        curve.append(
            {
                "date": exit_date,
                "champion_return_pct": _pct(champion_cumulative - 1.0),
                "twii_return_pct": _pct(twii_cumulative - 1.0),
                "sample_count": len(bucket),
            }
        )

    monthly_excess: list[float] = []
    for bucket in by_month.values():
        champion_return = sum(item["realized_return"] for item in bucket) / len(bucket)
        twii_values = [
            item["twii_return"]
            for item in bucket
            if item["twii_return"] is not None
        ]
        if twii_values:
            monthly_excess.append(champion_return - (sum(twii_values) / len(twii_values)))

    wins = sum(1 for trade in mature_trades if trade["realized_return"] > 0)
    latest_exit_date = max(trade["exit_date"] for trade in mature_trades)
    final_point = curve[-1]
    return {
        "status": "ok",
        "model": "Two-Stage Champion N=75",
        "strategy_start_date": OOS_START_DATE.isoformat(),
        "maturity_rule": f"entry_date + {OOS_HOLD_TRADING_DAYS} trading days",
        "method": "成熟 closed/stopped_out 部位等權，按 exit_date 彙總；未成熟部位不估算。",
        "mature_sample_count": len(mature_trades),
        "total_closed_after_start": len(rows),
        "immature_closed_count": immature_closed_count,
        "latest_mature_exit_date": latest_exit_date,
        "monthly_excess_return_pct": _pct(sum(monthly_excess) / len(monthly_excess))
        if monthly_excess
        else None,
        "win_rate_pct": _pct(wins / len(mature_trades)),
        "champion_cumulative_return_pct": final_point["champion_return_pct"],
        "twii_cumulative_return_pct": final_point["twii_return_pct"],
        "chart_title": "Rolling OOS 累計報酬",
        "chart_note": "綠線是 Two-Stage Champion 上線後已成熟部位；藍線是同一批持有期間的 TWII 同期基準。",
        "chart_lines": OOS_CHART_LINES,
        "curve": curve,
    }


def _load_two_stage_summary() -> dict[str, Any]:
    path = _summary_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Backtest summary not found: {path}")

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    row = next((item for item in rows if item.get("variant") == TREATMENT_VARIANT), None)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Variant {TREATMENT_VARIANT} not found in {path}",
        )

    chart_path = _monthly_returns_image_path()
    return {
        "status": "ok",
        "model": "Two-Stage Champion N=75",
        "source": BACKTEST_SUMMARY_FILENAME,
        "chart_url": "/api/model/backtest_summary/monthly_returns.png"
        if os.path.exists(chart_path)
        else None,
        "chart_title": "Two-Stage Champion 每月回測報酬",
        "chart_note": "藍線是舊版回歸 Top30；紅線是現行 Two-Stage N=75。兩條線皆為每月等權 Top30 報酬，不是累積淨值。",
        "chart_lines": BACKTEST_CHART_LINES,
        "period": BACKTEST_PERIOD,
        "months": int(float(row["months"])),
        "monthly_excess_return_pct": _pct(row["monthly_alpha"]),
        "max_drawdown_pct": _pct(row["mdd"]),
        "sharpe": _ratio(row["sharpe"]),
        "calmar": _ratio(row["calmar"]),
        "alpha_vs_twii_pct_per_month": _pct(row["monthly_alpha"]),
        "monthly_return_pct": _pct(row["monthly_return"]),
        "win_rate_pct": _pct(row["win_rate"]),
    }


@router.get("/backtest_summary")
def get_backtest_summary() -> dict[str, Any]:
    return _load_two_stage_summary()


@router.get("/backtest_summary/monthly_returns.png")
def get_backtest_monthly_returns_png() -> FileResponse:
    path = _monthly_returns_image_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Backtest chart not found: {path}")
    return FileResponse(path, media_type="image/png")


@router.get("/oos_performance")
def get_oos_performance() -> dict[str, Any]:
    return _build_oos_performance()
