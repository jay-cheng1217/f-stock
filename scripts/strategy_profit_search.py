"""Search simple Taiwan equity strategy rules for a target backtest profit.

This is a research harness. It does not change production model selection,
portfolio ledgers, model pins, or registries.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "ml" / "models"
REPORT_DIR = ROOT / "ml" / "reports"

DEFAULT_START_DATE = "2024-01-01"
DEFAULT_HOLDOUT_START_MONTH = "2025-07"
DEFAULT_FRICTION = 0.004
DEFAULT_PROFIT_THRESHOLD = 0.70

BASE_COLUMNS = [
    "Date",
    "ticker",
    "Open",
    "Close",
    "Volume",
    "AMOUNT_MA_20",
    "trade_return_20d",
    "trade_excess_return_20d",
    "taiex_20d_return_pct",
    "taiex_vs_ma60_pct",
    "market_breadth_20d_pct",
    "return_5d",
    "return_10d",
    "return_20d",
    "return_60d",
    "mom_20d",
    "momentum_accel",
    "price_vs_ma20",
    "price_vs_ma60",
    "rsi_14",
    "atr_pct_rank",
    "vol_ratio_5_20",
    "vol_zscore",
    "up_day_ratio_20",
    "sector_relative_return_20d",
    "sector_momentum_20d",
    "sector_return_rank",
    "inst_total_20d_norm",
    "foreign_cumsum_20d_norm",
    "trust_cumsum_20d_norm",
    "inst_buy_ratio_20d",
    "price_vs_inst_cost",
    "revenue_yoy_3m_avg",
    "revenue_yoy_momentum",
    "gross_margin_latest",
    "operating_margin_latest",
    "roe_annualized",
    "pe_ratio",
    "pb_ratio",
    "dividend_yield",
    "whale_pct_chg",
    "whale_acc_momentum",
    "whale_retail_ratio",
    "entry_score",
    "vol_contraction_ratio",
    "turnover_rate",
]

SINGLE_FACTOR_DIRECTIONS = {
    "Open": "low",
    "Close": "low",
    "Volume": "high",
    "AMOUNT_MA_20": "high",
    "return_5d": "high",
    "return_10d": "high",
    "return_20d": "high",
    "return_60d": "high",
    "mom_20d": "high",
    "momentum_accel": "high",
    "price_vs_ma20": "high",
    "price_vs_ma60": "high",
    "rsi_14": "high",
    "atr_pct_rank": "low",
    "vol_ratio_5_20": "high",
    "vol_zscore": "high",
    "up_day_ratio_20": "high",
    "sector_relative_return_20d": "high",
    "sector_momentum_20d": "high",
    "sector_return_rank": "high",
    "inst_total_20d_norm": "high",
    "foreign_cumsum_20d_norm": "high",
    "trust_cumsum_20d_norm": "high",
    "inst_buy_ratio_20d": "high",
    "price_vs_inst_cost": "high",
    "revenue_yoy_3m_avg": "high",
    "revenue_yoy_momentum": "high",
    "gross_margin_latest": "high",
    "operating_margin_latest": "high",
    "roe_annualized": "high",
    "pe_ratio": "low",
    "pb_ratio": "low",
    "dividend_yield": "high",
    "whale_pct_chg": "high",
    "whale_acc_momentum": "high",
    "whale_retail_ratio": "high",
    "entry_score": "high",
    "vol_contraction_ratio": "low",
    "turnover_rate": "high",
}

COMPOSITES: dict[str, list[tuple[str, str]]] = {
    "liquid_momentum": [
        ("return_60d", "high"),
        ("return_20d", "high"),
        ("Volume", "high"),
        ("AMOUNT_MA_20", "high"),
        ("atr_pct_rank", "low"),
    ],
    "chip_momentum": [
        ("inst_total_20d_norm", "high"),
        ("foreign_cumsum_20d_norm", "high"),
        ("trust_cumsum_20d_norm", "high"),
        ("inst_buy_ratio_20d", "high"),
        ("return_20d", "high"),
    ],
    "quality_value_momentum": [
        ("pb_ratio", "low"),
        ("pe_ratio", "low"),
        ("dividend_yield", "high"),
        ("roe_annualized", "high"),
        ("return_20d", "high"),
        ("revenue_yoy_3m_avg", "high"),
    ],
    "trend_strength": [
        ("return_20d", "high"),
        ("return_60d", "high"),
        ("price_vs_ma20", "high"),
        ("price_vs_ma60", "high"),
        ("up_day_ratio_20", "high"),
    ],
    "low_price_momentum": [
        ("Open", "low"),
        ("return_20d", "high"),
        ("Volume", "high"),
        ("up_day_ratio_20", "high"),
    ],
    "small_liquid_value": [
        ("Open", "low"),
        ("pb_ratio", "low"),
        ("dividend_yield", "high"),
        ("Volume", "high"),
        ("AMOUNT_MA_20", "high"),
    ],
}

FILTERS: dict[str, list[tuple[str, str, float]]] = {
    "none": [],
    "no_penny_open_ge_10": [("Open", ">=", 10.0)],
    "liquid_open_ge_10_volume_ge_250k_amount_ge_20m": [
        ("Open", ">=", 10.0),
        ("Volume", ">=", 250_000.0),
        ("AMOUNT_MA_20", ">=", 20_000_000.0),
    ],
    "liquid_open_ge_20_volume_ge_500k_amount_ge_100m": [
        ("Open", ">=", 20.0),
        ("Volume", ">=", 500_000.0),
        ("AMOUNT_MA_20", ">=", 100_000_000.0),
    ],
}

TOP_N_VALUES = [3, 5, 8, 10, 15, 20, 30]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    score_col: str
    direction: str
    top_n: int
    filter_name: str
    gate_name: str


def _latest_file(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(f"No files match {root / pattern}")
    return matches[-1]


def _available_parquet_columns(path: Path) -> set[str]:
    return set(pq.ParquetFile(path).schema.names)


def _load_monthly_frame(path: Path, start_date: str) -> pd.DataFrame:
    available = _available_parquet_columns(path)
    required = [col for col in BASE_COLUMNS if col in available]
    missing = {"Date", "ticker", "trade_return_20d", "trade_excess_return_20d"} - set(required)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")

    frame = pd.read_parquet(path, columns=required)
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame = frame[frame["Date"] >= pd.Timestamp(start_date)].dropna(subset=["trade_return_20d"]).copy()
    frame["month"] = frame["Date"].dt.to_period("M").astype(str)
    frame = (
        frame.sort_values(["month", "ticker", "Date"])
        .groupby(["month", "ticker"], observed=True)
        .tail(1)
        .reset_index(drop=True)
    )
    return frame


def _rank_percentile(frame: pd.DataFrame, column: str, direction: str) -> pd.Series:
    ascending = direction == "high"
    return frame.groupby("month", observed=True)[column].rank(pct=True, ascending=ascending).fillna(0.5)


def _add_composites(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for name, components in COMPOSITES.items():
        score = np.zeros(len(out), dtype=float)
        count = 0
        for column, direction in components:
            if column not in out.columns:
                continue
            score += _rank_percentile(out, column, direction).to_numpy()
            count += 1
        if count:
            out[name] = score / count
    return out


def _apply_filter(frame: pd.DataFrame, filter_name: str) -> pd.DataFrame:
    out = frame
    for column, op, value in FILTERS[filter_name]:
        if column not in out.columns:
            continue
        if op == ">=":
            out = out[out[column] >= value]
        else:
            raise ValueError(f"Unsupported filter op: {op}")
    return out


def _build_market_gates(frame: pd.DataFrame) -> dict[str, set[str]]:
    cols = ["taiex_20d_return_pct", "taiex_vs_ma60_pct", "market_breadth_20d_pct"]
    available = [col for col in cols if col in frame.columns]
    monthly = frame.groupby("month", observed=True)[available].median(numeric_only=True)
    all_months = set(frame["month"].drop_duplicates())

    gates: dict[str, set[str]] = {"all": all_months}
    if {"taiex_vs_ma60_pct", "market_breadth_20d_pct"} <= set(monthly.columns):
        risk_on = monthly[
            (monthly["taiex_vs_ma60_pct"] > -0.02)
            & (monthly["market_breadth_20d_pct"] > -0.02)
        ].index
        gates["risk_on_breadth_ma60"] = set(risk_on)
    if "taiex_20d_return_pct" in monthly.columns:
        gates["taiex_20d_positive"] = set(monthly[monthly["taiex_20d_return_pct"] > 0].index)
    return gates


def _curve_stats(monthly: pd.DataFrame) -> dict[str, Any]:
    equity = (1.0 + monthly["net_return"]).cumprod()
    excess_equity = (1.0 + monthly["net_excess_return"]).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "cumulative_net_return": float(equity.iloc[-1] - 1.0) if len(equity) else 0.0,
        "cumulative_net_excess_return": float(excess_equity.iloc[-1] - 1.0) if len(excess_equity) else 0.0,
        "avg_monthly_net_return": float(monthly["net_return"].mean()) if len(monthly) else 0.0,
        "avg_monthly_net_excess_return": float(monthly["net_excess_return"].mean()) if len(monthly) else 0.0,
        "positive_months": int((monthly["net_return"] > 0).sum()),
        "months": int(len(monthly)),
        "avg_pick_win_rate": float(monthly["pick_win_rate"].mean(skipna=True)) if len(monthly) else None,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "avg_open": float(monthly["avg_open"].mean(skipna=True)) if "avg_open" in monthly else None,
        "avg_volume": float(monthly["avg_volume"].mean(skipna=True)) if "avg_volume" in monthly else None,
        "avg_amount_ma20": float(monthly["avg_amount_ma20"].mean(skipna=True))
        if "avg_amount_ma20" in monthly
        else None,
    }


def _evaluate_picks(
    picks_by_month: dict[str, pd.DataFrame],
    all_months: list[str],
    friction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    pick_frames: list[pd.DataFrame] = []
    for month in all_months:
        picks = picks_by_month.get(month)
        if picks is None or picks.empty:
            rows.append(
                {
                    "month": month,
                    "n_picks": 0,
                    "net_return": 0.0,
                    "net_excess_return": 0.0,
                    "pick_win_rate": np.nan,
                    "avg_open": np.nan,
                    "avg_volume": np.nan,
                    "avg_amount_ma20": np.nan,
                }
            )
            continue
        picks = picks.copy()
        picks["selection_rank"] = np.arange(1, len(picks) + 1)
        picks["net_trade_return_20d"] = picks["trade_return_20d"] - friction
        picks["net_trade_excess_return_20d"] = picks["trade_excess_return_20d"] - friction
        pick_frames.append(picks)
        rows.append(
            {
                "month": month,
                "n_picks": int(len(picks)),
                "net_return": float(picks["net_trade_return_20d"].mean()),
                "net_excess_return": float(picks["net_trade_excess_return_20d"].mean()),
                "pick_win_rate": float((picks["net_trade_return_20d"] > 0).mean()),
                "avg_open": float(picks["Open"].mean()) if "Open" in picks.columns else np.nan,
                "avg_volume": float(picks["Volume"].mean()) if "Volume" in picks.columns else np.nan,
                "avg_amount_ma20": float(picks["AMOUNT_MA_20"].mean())
                if "AMOUNT_MA_20" in picks.columns
                else np.nan,
            }
        )
    monthly = pd.DataFrame(rows)
    pick_detail = pd.concat(pick_frames, ignore_index=True) if pick_frames else pd.DataFrame()
    return monthly, pick_detail


def _evaluate_strategy(
    frame: pd.DataFrame,
    spec: StrategySpec,
    all_months: list[str],
    gate_months: set[str],
    friction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    filtered = _apply_filter(frame, spec.filter_name)
    ascending = spec.direction == "low"
    picks_by_month: dict[str, pd.DataFrame] = {}
    for month, group in filtered.groupby("month", observed=True):
        if month not in gate_months:
            continue
        clean = group.dropna(subset=[spec.score_col, "trade_return_20d", "trade_excess_return_20d"])
        if clean.empty:
            continue
        picks_by_month[month] = (
            clean.sort_values([spec.score_col, "ticker"], ascending=[ascending, True])
            .head(spec.top_n)
            .copy()
        )
    return _evaluate_picks(picks_by_month, all_months, friction)


def _summarize_strategy(
    spec_name: str,
    monthly: pd.DataFrame,
    holdout_start_month: str,
    threshold: float,
) -> dict[str, Any]:
    train = monthly[monthly["month"] < holdout_start_month]
    holdout = monthly[monthly["month"] >= holdout_start_month]
    full_stats = _curve_stats(monthly)
    train_stats = _curve_stats(train)
    holdout_stats = _curve_stats(holdout)
    return {
        "strategy": spec_name,
        "full_cum_return_pct": full_stats["cumulative_net_return"] * 100.0,
        "full_cum_excess_pct": full_stats["cumulative_net_excess_return"] * 100.0,
        "train_cum_return_pct": train_stats["cumulative_net_return"] * 100.0,
        "train_cum_excess_pct": train_stats["cumulative_net_excess_return"] * 100.0,
        "holdout_cum_return_pct": holdout_stats["cumulative_net_return"] * 100.0,
        "holdout_cum_excess_pct": holdout_stats["cumulative_net_excess_return"] * 100.0,
        "avg_monthly_net_return_pct": full_stats["avg_monthly_net_return"] * 100.0,
        "avg_monthly_net_excess_pct": full_stats["avg_monthly_net_excess_return"] * 100.0,
        "max_drawdown_pct": full_stats["max_drawdown"] * 100.0,
        "positive_months": full_stats["positive_months"],
        "months": full_stats["months"],
        "avg_pick_win_rate_pct": (full_stats["avg_pick_win_rate"] or 0.0) * 100.0,
        "avg_open": full_stats["avg_open"],
        "avg_volume": full_stats["avg_volume"],
        "avg_amount_ma20": full_stats["avg_amount_ma20"],
        "pass_70pct_full": full_stats["cumulative_net_return"] >= threshold,
        "pass_train_positive": train_stats["cumulative_net_return"] > 0,
        "pass_holdout_positive": holdout_stats["cumulative_net_return"] > 0,
        "pass_full_excess_positive": full_stats["cumulative_net_excess_return"] > 0,
    }


def _strategy_specs(frame: pd.DataFrame) -> list[StrategySpec]:
    score_columns: dict[str, str] = {
        col: direction
        for col, direction in SINGLE_FACTOR_DIRECTIONS.items()
        if col in frame.columns and pd.api.types.is_numeric_dtype(frame[col])
    }
    for name in COMPOSITES:
        if name in frame.columns:
            score_columns[name] = "high"

    specs: list[StrategySpec] = []
    for score_col, direction in score_columns.items():
        for top_n in TOP_N_VALUES:
            for filter_name in FILTERS:
                for gate_name in ("all", "risk_on_breadth_ma60", "taiex_20d_positive"):
                    specs.append(
                        StrategySpec(
                            name=f"{score_col}_{direction}_top{top_n}_{filter_name}_{gate_name}",
                            score_col=score_col,
                            direction=direction,
                            top_n=top_n,
                            filter_name=filter_name,
                            gate_name=gate_name,
                        )
                    )
    return specs


def _load_v2_fold_strategy(path: Path, all_months: list[str], friction: float, top_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    fold = pd.read_csv(path)
    if not {"month", "rank", "trade_return_20d", "trade_excess_return_20d"} <= set(fold.columns):
        raise ValueError(f"{path} does not look like a V2 fold TopN artifact")
    picks_by_month = {
        month: group.sort_values(["rank", "ticker"], ascending=[True, True]).head(top_n).copy()
        for month, group in fold.groupby("month", observed=True)
    }
    return _evaluate_picks(picks_by_month, all_months, friction)


def _fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.1f}%"


def _write_report(
    path: Path,
    *,
    summary: dict[str, Any],
    best: dict[str, Any],
    results: pd.DataFrame,
    best_monthly: pd.DataFrame,
    artifacts: dict[str, str],
) -> None:
    top = results.head(15)
    lines = [
        "# Strategy Profit Search",
        "",
        "## Scope",
        f"- Dataset: `{summary['dataset_path']}`",
        f"- Start date: `{summary['start_date']}`",
        f"- Holdout start month: `{summary['holdout_start_month']}`",
        f"- Friction: `{summary['friction_pct']:.2f}%` round trip",
        f"- Profit hurdle: `{summary['profit_threshold_pct']:.1f}%` cumulative net raw return",
        f"- Strategies evaluated: `{summary['strategies_evaluated']}`",
        "",
        "## Best Passing Method",
        f"- Strategy: `{best['strategy']}`",
        f"- Full cumulative net return: `{_fmt_pct(best['full_cum_return_pct'])}`",
        f"- Full cumulative net excess return: `{_fmt_pct(best['full_cum_excess_pct'])}`",
        f"- Train cumulative net return: `{_fmt_pct(best['train_cum_return_pct'])}`",
        f"- Holdout cumulative net return: `{_fmt_pct(best['holdout_cum_return_pct'])}`",
        f"- Max drawdown: `{_fmt_pct(best['max_drawdown_pct'])}`",
        f"- Positive months: `{int(best['positive_months'])}/{int(best['months'])}`",
        f"- Average pick win rate: `{_fmt_pct(best['avg_pick_win_rate_pct'])}`",
        "",
        "Selection rule: require the 70% full-period hurdle, positive train and holdout raw returns, and positive full-period excess return; then prefer lower drawdown before higher raw return. Higher-return rows below are more aggressive candidates.",
        "",
        "This is a research candidate, not a production promotion. Production use still needs separate slippage, capacity, overlap, and same-snapshot validation.",
        "",
        "## Top Search Results",
        "| rank | strategy | full net | full excess | train net | holdout net | max DD | pos months |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(top.to_dict("records"), start=1):
        lines.append(
            f"| {rank} | `{row['strategy']}` | {_fmt_pct(row['full_cum_return_pct'])} | "
            f"{_fmt_pct(row['full_cum_excess_pct'])} | {_fmt_pct(row['train_cum_return_pct'])} | "
            f"{_fmt_pct(row['holdout_cum_return_pct'])} | {_fmt_pct(row['max_drawdown_pct'])} | "
            f"{int(row['positive_months'])}/{int(row['months'])} |"
        )
    lines.extend(
        [
            "",
            "## Best Method Monthly Returns",
            "| month | picks | net return | net excess | pick win |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in best_monthly.to_dict("records"):
        lines.append(
            f"| {row['month']} | {int(row['n_picks'])} | {_fmt_pct(row['net_return'] * 100.0)} | "
            f"{_fmt_pct(row['net_excess_return'] * 100.0)} | {_fmt_pct(row['pick_win_rate'] * 100.0)} |"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            f"- Results CSV: `{artifacts['results_csv']}`",
            f"- Best monthly CSV: `{artifacts['best_monthly_csv']}`",
            f"- Best picks CSV: `{artifacts['best_picks_csv']}`",
            f"- Summary JSON: `{artifacts['summary_json']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset) if args.dataset else _latest_file(MODEL_DIR, "dataset_cache_*.parquet")
    dataset_path = dataset_path.resolve()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    frame = _add_composites(_load_monthly_frame(dataset_path, args.start_date))
    all_months = sorted(frame["month"].drop_duplicates())
    if not all_months:
        raise ValueError("No monthly rows available for strategy search")
    gate_map = _build_market_gates(frame)

    summaries: list[dict[str, Any]] = []
    monthly_by_strategy: dict[str, pd.DataFrame] = {}
    picks_by_strategy: dict[str, pd.DataFrame] = {}
    for spec in _strategy_specs(frame):
        gate_months = gate_map.get(spec.gate_name)
        if gate_months is None:
            continue
        monthly, picks = _evaluate_strategy(frame, spec, all_months, gate_months, args.friction)
        monthly["strategy"] = spec.name
        if not picks.empty:
            picks["strategy"] = spec.name
        summary = _summarize_strategy(spec.name, monthly, args.holdout_start_month, args.threshold)
        summaries.append(summary)
        monthly_by_strategy[spec.name] = monthly
        picks_by_strategy[spec.name] = picks

    if args.v2_fold_top30:
        v2_path = Path(args.v2_fold_top30).resolve()
    else:
        try:
            v2_path = _latest_file(REPORT_DIR, "v2_fold_top30_*.csv").resolve()
        except FileNotFoundError:
            v2_path = None
    if v2_path and v2_path.exists():
        for top_n in [5, 10, 15, 20, 30]:
            name = f"v2_walkforward_pred_return_top{top_n}"
            monthly, picks = _load_v2_fold_strategy(v2_path, all_months, args.friction, top_n)
            monthly["strategy"] = name
            if not picks.empty:
                picks["strategy"] = name
            summaries.append(_summarize_strategy(name, monthly, args.holdout_start_month, args.threshold))
            monthly_by_strategy[name] = monthly
            picks_by_strategy[name] = picks

    results = pd.DataFrame(summaries)
    results["robust_raw_pass"] = (
        results["pass_70pct_full"] & results["pass_train_positive"] & results["pass_holdout_positive"]
    )
    results["research_quality_score"] = (
        results["full_cum_return_pct"]
        + results["full_cum_excess_pct"].clip(lower=-100.0)
        + results["holdout_cum_return_pct"].clip(lower=-100.0)
        + results["train_cum_return_pct"].clip(lower=-100.0)
        + results["max_drawdown_pct"]
    )
    results = results.sort_values(
        ["robust_raw_pass", "pass_full_excess_positive", "research_quality_score"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    passing = results[results["robust_raw_pass"]].copy()
    if passing.empty:
        best = results.iloc[0].to_dict()
    else:
        positive_excess = passing[passing["pass_full_excess_positive"]]
        best_pool = positive_excess if not positive_excess.empty else passing
        best = best_pool.sort_values(
            ["pass_full_excess_positive", "max_drawdown_pct", "full_cum_return_pct"],
            ascending=[False, False, False],
        ).iloc[0].to_dict()

    timestamp = args.output_prefix or datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"strategy_profit_search_{timestamp}"
    results_csv = REPORT_DIR / f"{prefix}.csv"
    best_monthly_csv = REPORT_DIR / f"{prefix}_best_monthly.csv"
    best_picks_csv = REPORT_DIR / f"{prefix}_best_picks.csv"
    summary_json = REPORT_DIR / f"{prefix}.json"
    report_md = REPORT_DIR / f"{prefix}.md"

    best_monthly = monthly_by_strategy[best["strategy"]].copy()
    best_picks = picks_by_strategy[best["strategy"]].copy()
    results.to_csv(results_csv, index=False, encoding="utf-8-sig")
    best_monthly.to_csv(best_monthly_csv, index=False, encoding="utf-8-sig")
    best_picks.to_csv(best_picks_csv, index=False, encoding="utf-8-sig")

    summary = {
        "dataset_path": str(dataset_path),
        "start_date": args.start_date,
        "holdout_start_month": args.holdout_start_month,
        "friction_pct": args.friction * 100.0,
        "profit_threshold_pct": args.threshold * 100.0,
        "strategies_evaluated": int(len(results)),
        "best_strategy": best,
        "artifacts": {
            "results_csv": str(results_csv),
            "best_monthly_csv": str(best_monthly_csv),
            "best_picks_csv": str(best_picks_csv),
            "summary_json": str(summary_json),
            "report_md": str(report_md),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(
        report_md,
        summary=summary,
        best=best,
        results=results,
        best_monthly=best_monthly,
        artifacts=summary["artifacts"],
    )
    print(f"[strategy-search] strategies={len(results)}")
    print(f"[strategy-search] best={best['strategy']}")
    print(f"[strategy-search] full_cum_return_pct={best['full_cum_return_pct']:.2f}")
    print(f"[strategy-search] report={report_md}")
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search strategy rules for a cumulative profit target.")
    parser.add_argument("--dataset", default=None, help="Parquet dataset cache. Defaults to latest dataset_cache_*.parquet")
    parser.add_argument("--v2-fold-top30", default=None, help="Optional V2 fold Top30 CSV to include as a model strategy")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--holdout-start-month", default=DEFAULT_HOLDOUT_START_MONTH)
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION)
    parser.add_argument("--threshold", type=float, default=DEFAULT_PROFIT_THRESHOLD)
    parser.add_argument("--output-prefix", default=None, help="Timestamp/prefix suffix, defaults to current timestamp")
    return parser.parse_args()


def main() -> int:
    run_search(_parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
