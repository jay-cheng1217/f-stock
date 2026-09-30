"""Production guardrails for T+1 and Dual recommendations.

The T+1 model can still rank names when it is miscalibrated, so production
selection must be gated by realized monitoring and trade-aligned backtest
health before any T+1 or Dual buy signal is promoted.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ml.config import REPORT_DIR

MIN_BACKTEST_EXPECTANCY = 0.0
MIN_BACKTEST_CUMULATIVE_RETURN = 0.0
MIN_BACKTEST_TRADE_WIN_RATE = 0.45
MIN_MONITOR_AVG_IC = 0.02
MIN_MONITOR_WIN_RATE = 0.45
SPECIAL_STATUS_REPORT = "special_stock_status_latest.json"
SPECIAL_STATUS_OPEN = "OK"

T1_HARD_RISK_KEYWORDS = (
    "長上影",
    "上影",
    "收盤偏弱",
    "量能不足",
    "突破過熱",
    "均線乖離",
    "法人反向",
)

D20_HARD_RISK_KEYWORDS = (
    "本業仍虧損",
    "本業虧損",
    "短線偏熱",
    "追價風險高",
    "法人10日賣壓",
    "籌碼頂部背離",
    "流動性不足",
    "異常暴跌",
)


def _read_json(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _count_critical_anomalies(report: dict[str, Any]) -> int:
    summary = report.get("summary", {}) or {}
    critical = summary.get("critical_count")
    if critical is not None:
        try:
            return int(critical)
        except (TypeError, ValueError):
            pass
    anomalies = report.get("anomalies", []) or []
    return sum(1 for item in anomalies if str(item.get("level", "")).upper() == "CRITICAL")


def evaluate_t1_production_gate(report_dir: str = REPORT_DIR) -> dict[str, Any]:
    """Return the current production gate state for T+1-derived signals."""

    reasons: list[str] = []
    checks: dict[str, Any] = {}

    backtest_path = os.path.join(report_dir, "t1_backtest_latest.json")
    backtest = _read_json(backtest_path)
    if backtest is None:
        reasons.append("missing_t1_backtest_report")
        checks["backtest"] = {"status": "missing", "path": backtest_path}
    else:
        overall = backtest.get("overall", {}) or {}
        avg_expectancy = _to_float(overall.get("avg_expectancy"))
        cumulative_return = _to_float(overall.get("cumulative_portfolio_return"))
        trade_win_rate = _to_float(overall.get("avg_trade_win_rate"))
        checks["backtest"] = {
            "status": "ok",
            "path": backtest_path,
            "avg_expectancy": avg_expectancy,
            "cumulative_portfolio_return": cumulative_return,
            "avg_trade_win_rate": trade_win_rate,
            "min_avg_expectancy": MIN_BACKTEST_EXPECTANCY,
            "min_cumulative_portfolio_return": MIN_BACKTEST_CUMULATIVE_RETURN,
            "min_trade_win_rate": MIN_BACKTEST_TRADE_WIN_RATE,
        }
        if avg_expectancy is None or avg_expectancy <= MIN_BACKTEST_EXPECTANCY:
            reasons.append(f"t1_backtest_expectancy={avg_expectancy}")
        if cumulative_return is None or cumulative_return <= MIN_BACKTEST_CUMULATIVE_RETURN:
            reasons.append(f"t1_backtest_cumulative_return={cumulative_return}")
        if trade_win_rate is None or trade_win_rate < MIN_BACKTEST_TRADE_WIN_RATE:
            reasons.append(f"t1_backtest_trade_win_rate={trade_win_rate}")

    monitor_path = os.path.join(report_dir, "monitor_t1_latest.json")
    monitor = _read_json(monitor_path)
    if monitor is None:
        reasons.append("missing_t1_monitor_report")
        checks["monitor"] = {"status": "missing", "path": monitor_path}
    else:
        summary = monitor.get("summary", {}) or {}
        portfolio = monitor.get("portfolio", {}) or {}
        critical_count = _count_critical_anomalies(monitor)
        avg_ic = _to_float(summary.get("avg_ic"))
        overall_win_rate = _to_float(portfolio.get("overall_win_rate"))
        execution_gap = _to_float((portfolio.get("execution_gap", {}) or {}).get("execution_gap"))
        checks["monitor"] = {
            "status": "ok",
            "path": monitor_path,
            "generated_at": monitor.get("generated_at"),
            "critical_count": critical_count,
            "avg_ic": avg_ic,
            "overall_win_rate": overall_win_rate,
            "execution_gap": execution_gap,
            "min_avg_ic": MIN_MONITOR_AVG_IC,
            "min_win_rate": MIN_MONITOR_WIN_RATE,
        }
        if critical_count > 0:
            reasons.append(f"t1_monitor_critical_count={critical_count}")
        if avg_ic is None or avg_ic < MIN_MONITOR_AVG_IC:
            reasons.append(f"t1_monitor_avg_ic={avg_ic}")
        if overall_win_rate is None or overall_win_rate < MIN_MONITOR_WIN_RATE:
            reasons.append(f"t1_monitor_win_rate={overall_win_rate}")
        if execution_gap is not None and execution_gap < 0:
            reasons.append(f"t1_monitor_execution_gap={execution_gap}")

    special_path = os.path.join(report_dir, SPECIAL_STATUS_REPORT)
    special = _read_json(special_path)
    if special is None:
        reasons.append("missing_special_stock_status_report")
        checks["special_status"] = {"status": "missing", "path": special_path}
    else:
        status = str(special.get("status") or "").upper()
        gate_action = str(special.get("gate_action") or "")
        checks["special_status"] = {
            "status": status,
            "path": special_path,
            "fetched_at": special.get("fetched_at"),
            "effective_date": special.get("effective_date"),
            "last_successful_effective_date": special.get("last_successful_effective_date"),
            "gate_action": gate_action,
            "error_message": special.get("error_message"),
        }
        if status != SPECIAL_STATUS_OPEN:
            reasons.append(f"special_stock_status={status or 'UNKNOWN'}")
        if gate_action.startswith("CLOSED"):
            reasons.append(f"special_stock_status_gate={gate_action}")

    closed = bool(reasons)
    return {
        "status": "CLOSED" if closed else "OPEN",
        "closed": closed,
        "action": "freeze_t1_and_dual" if closed else "allow_t1_and_dual",
        "reason": "; ".join(reasons),
        "reasons": reasons,
        "checks": checks,
    }


def matching_risk_keywords(text: Any, keywords: tuple[str, ...]) -> list[str]:
    raw = "" if text is None else str(text)
    if not raw or raw.lower() == "nan":
        return []
    return [keyword for keyword in keywords if keyword in raw]


def t1_hard_risk_reason(text: Any) -> str:
    matches = matching_risk_keywords(text, T1_HARD_RISK_KEYWORDS)
    return "t1_hard_risk:" + "|".join(matches) if matches else ""


def d20_hard_risk_reason(text: Any) -> str:
    matches = matching_risk_keywords(text, D20_HARD_RISK_KEYWORDS)
    return "d20_hard_risk:" + "|".join(matches) if matches else ""


def combine_block_reasons(*reasons: str) -> str:
    return "; ".join(reason for reason in reasons if reason)
