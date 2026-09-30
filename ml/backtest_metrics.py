"""Risk/audit metrics for walk-forward backtest reports."""

from __future__ import annotations

import math
import re
from typing import Any

from ml.benchmark_contract import build_selection_alpha_benchmark_contract


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _as_int(value: Any, default: int = 0) -> int:
    parsed = _as_float(value)
    if parsed is None:
        return default
    return int(parsed)


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _parse_top_n(payload: dict[str, Any], overall: dict[str, Any]) -> int:
    top_n = _as_int(payload.get("top_n"), 0)
    if top_n > 0:
        return top_n

    match = re.search(r"Top\s+(\d+)", str(overall.get("strategy", "")), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 10


def _fold_pct_return(fold: dict[str, Any], key: str) -> float:
    return (_as_float(fold.get(key), 0.0) or 0.0) / 100.0


def _fold_alpha_value(fold: dict[str, Any]) -> float:
    for key in ("selection_alpha_vs_equal_weight_pct", "alpha_pct"):
        value = _as_float(fold.get(key))
        if value is not None:
            return value
    return 0.0


def _fold_benchmark_return(fold: dict[str, Any]) -> float:
    for key in ("cumul_equal_weight_universe_excess_pct", "cumul_benchmark_pct"):
        value = _as_float(fold.get(key))
        if value is not None:
            return value / 100.0
    return 0.0


def _sum_fold_pct_values(folds: list[dict[str, Any]], *keys: str) -> float | None:
    values: list[float] = []
    for fold in folds:
        for key in keys:
            value = _as_float(fold.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return round(sum(values), 4)


def _compact_fold(fold: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fold:
        return None
    keys = [
        "fold",
        "test_end",
        "n_days",
        "selection_alpha_vs_equal_weight_pct",
        "alpha_pct",
        "hit_rate_pct",
        "beat_benchmark_pct",
        "cumul_portfolio_pct",
        "cumul_equal_weight_universe_excess_pct",
        "cumul_benchmark_pct",
    ]
    return {key: fold.get(key) for key in keys if key in fold}


def _compound_curve(returns: list[float]) -> tuple[float, float, str | None, list[float]]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    min_drawdown_index: int | None = None
    curve: list[float] = []

    for index, ret in enumerate(returns):
        equity *= 1.0 + ret
        curve.append(equity)

        if equity > peak:
            peak = equity

        drawdown = (equity / peak) - 1.0 if peak > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            min_drawdown_index = index

    drawdown_end = str(min_drawdown_index) if min_drawdown_index is not None else None
    return equity, max_drawdown, drawdown_end, curve


def build_backtest_risk_summary(
    fold_summaries: list[dict[str, Any]],
    *,
    top_n: int = 10,
    forward_days: int | None = None,
    total_portfolio_return_pct: float | None = None,
    total_benchmark_return_pct: float | None = None,
) -> dict[str, Any]:
    """Build audit metrics from the fold summary shape used by ``ml.backtest``."""
    folds = [dict(item) for item in fold_summaries if isinstance(item, dict)]
    if not folds:
        return {
            "benchmark_contract": build_selection_alpha_benchmark_contract(),
            "total_signal_days": 0,
            "estimated_selection_count": 0,
            "estimated_weekly_selection_count": top_n * 5,
            "negative_alpha_folds": 0,
            "negative_portfolio_folds": 0,
            "positive_alpha_ratio_pct": None,
            "negative_portfolio_ratio_pct": None,
            "max_drawdown_pct": None,
            "drawdown_end": None,
            "compounded_portfolio_return_pct": None,
            "compounded_benchmark_return_pct": None,
            "return_mdd_ratio": None,
            "annualized_return_pct": None,
            "calmar_ratio": None,
            "best_alpha_fold": None,
            "worst_alpha_fold": None,
            "best_portfolio_fold": None,
            "worst_portfolio_fold": None,
        }

    total_signal_days = sum(_as_int(fold.get("n_days"), 0) for fold in folds)
    estimated_selection_count = total_signal_days * top_n

    alpha_values = [_fold_alpha_value(fold) for fold in folds]
    portfolio_fold_returns = [_fold_pct_return(fold, "cumul_portfolio_pct") for fold in folds]
    benchmark_fold_returns = [_fold_benchmark_return(fold) for fold in folds]

    equity, max_drawdown, drawdown_index, _ = _compound_curve(portfolio_fold_returns)
    benchmark_equity, _, _, _ = _compound_curve(benchmark_fold_returns)

    drawdown_end = None
    if drawdown_index is not None:
        idx = int(drawdown_index)
        if 0 <= idx < len(folds):
            drawdown_end = folds[idx].get("test_end")

    compounded_portfolio_return_pct = (equity - 1.0) * 100.0
    compounded_benchmark_return_pct = (benchmark_equity - 1.0) * 100.0
    max_drawdown_pct = max_drawdown * 100.0

    return_for_ratio = total_portfolio_return_pct
    if return_for_ratio is None:
        return_for_ratio = compounded_portfolio_return_pct
    return_mdd_ratio = None
    if max_drawdown_pct < 0:
        return_mdd_ratio = return_for_ratio / abs(max_drawdown_pct)

    # This legacy report stores fold-level basket returns, not a continuous
    # capital curve. Use reported return divided by MDD as the display proxy.
    annualized_return_pct = None
    calmar_ratio = return_mdd_ratio

    best_alpha = max(folds, key=_fold_alpha_value)
    worst_alpha = min(folds, key=_fold_alpha_value)
    best_portfolio = max(
        folds,
        key=lambda fold: _as_float(fold.get("cumul_portfolio_pct"), float("-inf")) or float("-inf"),
    )
    worst_portfolio = min(
        folds,
        key=lambda fold: _as_float(fold.get("cumul_portfolio_pct"), float("inf")) or float("inf"),
    )

    negative_alpha_folds = sum(1 for value in alpha_values if value < 0)
    negative_portfolio_folds = sum(1 for value in portfolio_fold_returns if value < 0)

    return {
        "benchmark_contract": build_selection_alpha_benchmark_contract(),
        "total_signal_days": total_signal_days,
        "estimated_selection_count": estimated_selection_count,
        "estimated_weekly_selection_count": top_n * 5,
        "estimated_daily_selection_count": top_n,
        "forward_days": forward_days,
        "negative_alpha_folds": negative_alpha_folds,
        "negative_portfolio_folds": negative_portfolio_folds,
        "positive_alpha_ratio_pct": _round_or_none((len(folds) - negative_alpha_folds) / len(folds) * 100.0, 2),
        "negative_portfolio_ratio_pct": _round_or_none(negative_portfolio_folds / len(folds) * 100.0, 2),
        "max_drawdown_pct": _round_or_none(max_drawdown_pct, 4),
        "drawdown_end": drawdown_end,
        "compounded_portfolio_return_pct": _round_or_none(compounded_portfolio_return_pct, 4),
        "compounded_benchmark_return_pct": _round_or_none(compounded_benchmark_return_pct, 4),
        "reported_total_portfolio_return_pct": _round_or_none(total_portfolio_return_pct, 4),
        "reported_total_equal_weight_universe_excess_pct": _round_or_none(total_benchmark_return_pct, 4),
        "reported_total_benchmark_return_pct": _round_or_none(total_benchmark_return_pct, 4),
        "return_mdd_ratio": _round_or_none(return_mdd_ratio, 4),
        "annualized_return_pct": _round_or_none(annualized_return_pct, 4),
        "calmar_ratio": _round_or_none(calmar_ratio, 4),
        "best_alpha_fold": _compact_fold(best_alpha),
        "worst_alpha_fold": _compact_fold(worst_alpha),
        "best_portfolio_fold": _compact_fold(best_portfolio),
        "worst_portfolio_fold": _compact_fold(worst_portfolio),
    }


def enrich_backtest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Add risk_summary to legacy and new backtest payloads without mutating callers."""
    enriched = dict(payload)
    overall = dict(enriched.get("overall") or {})
    folds = enriched.get("fold_summaries") or []
    if not isinstance(folds, list):
        folds = []

    top_n = _parse_top_n(enriched, overall)
    alpha_values = [_fold_alpha_value(fold) for fold in folds]
    risk_summary = build_backtest_risk_summary(
        folds,
        top_n=top_n,
        forward_days=_as_int(overall.get("forward_days"), 0) or None,
        total_portfolio_return_pct=_as_float(overall.get("total_portfolio_return_pct")),
        total_benchmark_return_pct=_as_float(overall.get("total_benchmark_return_pct")),
    )

    overall.setdefault("negative_alpha_folds", risk_summary["negative_alpha_folds"])
    overall.setdefault("negative_portfolio_folds", risk_summary["negative_portfolio_folds"])
    overall.setdefault("total_signal_days", risk_summary["total_signal_days"])
    overall.setdefault("estimated_selection_count", risk_summary["estimated_selection_count"])
    overall.setdefault("max_drawdown_pct", risk_summary["max_drawdown_pct"])
    overall.setdefault("return_mdd_ratio", risk_summary["return_mdd_ratio"])
    overall.setdefault("calmar_ratio", risk_summary["calmar_ratio"])
    overall.setdefault("benchmark_contract", build_selection_alpha_benchmark_contract())
    overall.setdefault(
        "total_portfolio_excess_return_pct",
        _as_float(overall.get("total_portfolio_return_pct"))
        if _as_float(overall.get("total_portfolio_return_pct")) is not None
        else _sum_fold_pct_values(folds, "cumul_portfolio_pct"),
    )
    overall.setdefault(
        "total_equal_weight_universe_excess_pct",
        _as_float(overall.get("total_benchmark_return_pct"))
        if _as_float(overall.get("total_benchmark_return_pct")) is not None
        else _sum_fold_pct_values(folds, "cumul_equal_weight_universe_excess_pct", "cumul_benchmark_pct"),
    )
    overall.setdefault(
        "avg_selection_alpha_vs_equal_weight_pct",
        _as_float(overall.get("avg_alpha_pct"))
        if _as_float(overall.get("avg_alpha_pct")) is not None
        else _round_or_none(sum(alpha_values) / len(alpha_values), 4) if alpha_values else None,
    )

    enriched["overall"] = overall
    enriched["risk_summary"] = risk_summary
    return enriched
