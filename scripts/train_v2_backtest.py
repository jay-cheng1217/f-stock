"""Backtest saved LightGBM models with sector cap and stop-loss controls.

This script is designed for the production-style `lgbm_YYYYMMDD_HHMMSS.txt`
models trained by `ml/train.py`. It loads the saved model metadata, aligns the
feature columns dynamically, applies a sector concentration cap, evaluates a
20-trading-day hold with an optional stop-loss, and writes summary/detail
reports.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, REPORT_DIR
from ml.dataset import build_dataset
from ml.features.sector import load_sector_mapping

HOLD_DAYS = 20
DEFAULT_START = "2024-01-01"
DEFAULT_TOP_N = 30
DEFAULT_SECTOR_CAP = 0.20
DEFAULT_STOP_LOSS = -0.10

BASE_MODEL_META_RE = re.compile(r"^lgbm_\d{8}_\d{6}_meta\.json$")


@dataclass
class BacktestArtifacts:
    summary: dict
    monthly: pd.DataFrame
    trades: pd.DataFrame
    summary_path: Path | None = None
    monthly_path: Path | None = None
    trades_path: Path | None = None


def list_base_model_meta_paths() -> list[Path]:
    model_dir = Path(MODEL_DIR)
    return sorted(
        path for path in model_dir.iterdir()
        if BASE_MODEL_META_RE.match(path.name)
    )


def resolve_model_meta_path(explicit: str | None, role: str, primary: Path | None = None) -> Path | None:
    if explicit is not None:
        lowered = explicit.strip().lower()
        if role == "baseline" and lowered in {"", "none", "off"}:
            return None
        if role == "baseline" and lowered == "auto":
            metas = list_base_model_meta_paths()
            candidates = [path for path in metas if primary is None or path != primary]
            return candidates[-1] if candidates else None
        return Path(explicit).resolve()

    metas = list_base_model_meta_paths()
    if not metas:
        raise FileNotFoundError(f"No base model meta files found in {MODEL_DIR}")

    if role == "primary":
        return metas[-1]

    candidates = [path for path in metas if primary is None or path != primary]
    return candidates[-1] if candidates else None


def load_model_bundle(meta_path: Path) -> tuple[dict, lgb.Booster]:
    with meta_path.open("r", encoding="utf-8") as fh:
        meta = json.load(fh)
    model = lgb.Booster(model_file=meta["model_file"])
    return meta, model


def load_sector_lookup() -> dict[str, str]:
    sector_df = load_sector_mapping()
    if sector_df is None:
        return {}
    return dict(zip(sector_df["Ticker"], sector_df["Sector"]))


def build_backtest_dataset(stop_loss: float) -> pd.DataFrame:
    dataset = build_dataset(verbose=True).copy()
    dataset = dataset.sort_values(["ticker", "Date"]).reset_index(drop=True)

    if "Low" not in dataset.columns:
        dataset["Low"] = dataset["Close"]

    dataset["actual_return_20d"] = (
        dataset.groupby("ticker")["Close"].shift(-HOLD_DAYS) / dataset["Close"] - 1.0
    )
    dataset["stop_loss_return_20d"] = compute_stop_loss_returns(dataset, stop_loss)
    dataset["stop_loss_hit_20d"] = (
        dataset["stop_loss_return_20d"].notna()
        & dataset["actual_return_20d"].notna()
        & (dataset["stop_loss_return_20d"] < dataset["actual_return_20d"])
    )
    return dataset


def compute_stop_loss_returns(dataset: pd.DataFrame, stop_loss: float) -> pd.Series:
    result = np.full(len(dataset), np.nan, dtype=np.float32)

    for _, idx in dataset.groupby("ticker", sort=False).groups.items():
        positions = np.asarray(idx, dtype=np.int64)
        close_arr = dataset.loc[positions, "Close"].to_numpy(dtype=np.float32)
        low_arr = dataset.loc[positions, "Low"].to_numpy(dtype=np.float32)

        if len(close_arr) <= HOLD_DAYS:
            continue

        future_close = close_arr[HOLD_DAYS:] / close_arr[:-HOLD_DAYS] - 1.0
        low_windows = sliding_window_view(low_arr[1:], HOLD_DAYS)
        future_low_min = low_windows.min(axis=1)
        stop_hit = future_low_min / close_arr[:-HOLD_DAYS] - 1.0 <= stop_loss
        realized = np.where(stop_hit, stop_loss, future_close).astype(np.float32)

        valid_positions = positions[:-HOLD_DAYS]
        result[valid_positions] = realized

    return pd.Series(result, index=dataset.index, dtype=np.float32)


def normalize_prediction_output(raw_pred: np.ndarray, n_rows: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    pred = np.asarray(raw_pred)

    if pred.ndim == 1 and pred.shape[0] == n_rows:
        score = pred.astype(np.float32)
        return score, None, None

    if pred.ndim == 1 and pred.size % n_rows == 0:
        pred = pred.reshape(n_rows, pred.size // n_rows)

    if pred.ndim != 2:
        raise ValueError(f"Unexpected prediction shape: {pred.shape}")

    pred = pred.astype(np.float32)
    if pred.shape[1] >= 3:
        prob_down = pred[:, 0]
        prob_up = pred[:, 2]
        score = prob_up
        return score, prob_up, prob_down

    score = pred[:, -1]
    return score, None, None


def select_with_sector_cap(ranked_df: pd.DataFrame, top_n: int, cap_ratio: float) -> pd.DataFrame:
    if ranked_df.empty:
        return ranked_df.copy()

    max_per_sector = max(1, int(top_n * cap_ratio))
    selected_rows = []
    sector_count: dict[str, int] = {}

    for _, row in ranked_df.iterrows():
        sector = row.get("sector", "Other")
        current = sector_count.get(sector, 0)
        if current >= max_per_sector:
            continue
        selected_rows.append(row)
        sector_count[sector] = current + 1
        if len(selected_rows) >= top_n:
            break

    if not selected_rows:
        return ranked_df.head(0).copy()

    return pd.DataFrame(selected_rows).reset_index(drop=True)


def max_drawdown(cumulative: pd.Series) -> float:
    if cumulative.empty:
        return 0.0
    peak = cumulative.cummax()
    drawdown = cumulative / peak - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    std = returns.std(ddof=0)
    if std == 0 or math.isnan(std):
        return 0.0
    return float(returns.mean() / std * math.sqrt(12))


def sortino_ratio(returns: pd.Series) -> float:
    downside = returns[returns < 0]
    if len(downside) < 2:
        return 0.0
    downside_std = downside.std(ddof=0)
    if downside_std == 0 or math.isnan(downside_std):
        return 0.0
    return float(returns.mean() / downside_std * math.sqrt(12))


def sector_hhi(sectors: pd.Series) -> float:
    if sectors.empty:
        return 0.0
    weights = sectors.value_counts(normalize=True)
    return float((weights ** 2).sum())


def run_single_backtest(
    dataset: pd.DataFrame,
    sector_lookup: dict[str, str],
    meta_path: Path,
    top_n: int,
    sector_cap: float,
    stop_loss: float,
    start: str,
    end: str | None,
    save_reports: bool,
    report_tag: str | None,
) -> BacktestArtifacts:
    meta, model = load_model_bundle(meta_path)
    feature_cols = list(meta["feature_columns"])

    missing = [col for col in feature_cols if col not in dataset.columns]
    if missing:
        for col in missing:
            dataset[col] = np.nan

    usable = dataset.copy()
    usable["sector"] = usable["ticker"].map(sector_lookup).fillna("Other")

    end_ts = pd.Timestamp(end) if end else pd.Timestamp(usable["Date"].max())
    month_starts = pd.date_range(pd.Timestamp(start), end_ts, freq="MS")

    monthly_rows: list[dict] = []
    trade_rows: list[dict] = []

    print("=" * 88)
    print(f"Backtesting {meta_path.name}")
    print(f"  model: {Path(meta['model_file']).name}")
    print(f"  trained_at: {meta.get('trained_at', 'unknown')}")
    print(f"  features: {len(feature_cols)}")
    print(f"  period: {month_starts.min().date()} -> {month_starts.max().date()}")
    print(f"  controls: sector_cap={sector_cap:.0%}, stop_loss={stop_loss:.0%}, top_n={top_n}")
    print("=" * 88)

    for month_start in month_starts:
        month_end = month_start + pd.offsets.MonthEnd(0)
        snapshot = usable[(usable["Date"] >= month_start) & (usable["Date"] <= month_end)].copy()
        if snapshot.empty:
            continue

        snapshot = snapshot.sort_values(["ticker", "Date"]).groupby("ticker", as_index=False).tail(1)
        snapshot = snapshot[(month_end - snapshot["Date"]).dt.days <= 5].copy()
        snapshot = snapshot.dropna(subset=["actual_return_20d", "stop_loss_return_20d"])
        if snapshot.empty:
            continue

        x = snapshot[feature_cols].to_numpy(dtype=np.float32, copy=False)
        score, prob_up, prob_down = normalize_prediction_output(model.predict(x), len(snapshot))

        ranked = snapshot[[
            "ticker", "Date", "Close", "Low", "sector", "actual_return_20d",
            "stop_loss_return_20d", "stop_loss_hit_20d",
        ]].copy()
        ranked["score"] = score
        if prob_up is not None:
            ranked["prob_up"] = prob_up
            ranked["prob_down"] = prob_down
        else:
            ranked["prob_up"] = np.nan
            ranked["prob_down"] = np.nan

        ranked = ranked.sort_values(["score", "prob_up"], ascending=False).reset_index(drop=True)
        selected = select_with_sector_cap(ranked, top_n=top_n, cap_ratio=sector_cap)
        if selected.empty:
            continue

        month_key = month_start.strftime("%Y-%m")
        month_return_raw = float(selected["actual_return_20d"].mean())
        month_return_stop = float(selected["stop_loss_return_20d"].mean())
        month_stop_rate = float(selected["stop_loss_hit_20d"].mean())
        month_hhi = sector_hhi(selected["sector"])

        monthly_rows.append({
            "month": month_key,
            "n_candidates": int(len(ranked)),
            "n_selected": int(len(selected)),
            "avg_score": float(selected["score"].mean()),
            "avg_prob_up": float(selected["prob_up"].mean()) if selected["prob_up"].notna().any() else None,
            "return_raw_20d": month_return_raw,
            "return_stop_20d": month_return_stop,
            "win_rate_raw": float((selected["actual_return_20d"] > 0).mean()),
            "win_rate_stop": float((selected["stop_loss_return_20d"] > 0).mean()),
            "stop_loss_rate": month_stop_rate,
            "sector_hhi": month_hhi,
        })

        for rank, row in enumerate(selected.itertuples(index=False), start=1):
            trade_rows.append({
                "month": month_key,
                "rank": rank,
                "ticker": row.ticker,
                "entry_date": row.Date.strftime("%Y-%m-%d"),
                "entry_close": float(row.Close),
                "sector": row.sector,
                "score": float(row.score),
                "prob_up": float(row.prob_up) if not pd.isna(row.prob_up) else None,
                "prob_down": float(row.prob_down) if not pd.isna(row.prob_down) else None,
                "raw_return_20d": float(row.actual_return_20d),
                "stop_return_20d": float(row.stop_loss_return_20d),
                "stop_loss_hit": bool(row.stop_loss_hit_20d),
            })

        print(
            f"  {month_key}  candidates={len(ranked):>4d}  selected={len(selected):>2d}  "
            f"ret_raw={month_return_raw*100:+6.2f}%  ret_stop={month_return_stop*100:+6.2f}%  "
            f"stop={month_stop_rate*100:5.1f}%  hhi={month_hhi:.3f}"
        )

    monthly_df = pd.DataFrame(monthly_rows)
    trades_df = pd.DataFrame(trade_rows)

    if monthly_df.empty:
        raise RuntimeError(f"No valid monthly backtest rows produced for {meta_path}")

    cumulative_stop = (1.0 + monthly_df["return_stop_20d"]).cumprod()
    cumulative_raw = (1.0 + monthly_df["return_raw_20d"]).cumprod()
    months = len(monthly_df)
    cagr_stop = float(cumulative_stop.iloc[-1] ** (12.0 / months) - 1.0) if months > 0 else 0.0
    cagr_raw = float(cumulative_raw.iloc[-1] ** (12.0 / months) - 1.0) if months > 0 else 0.0

    summary = {
        "meta_path": str(meta_path),
        "model_file": meta["model_file"],
        "trained_at": meta.get("trained_at"),
        "n_features": len(feature_cols),
        "period_start": monthly_df["month"].iloc[0],
        "period_end": monthly_df["month"].iloc[-1],
        "months": months,
        "top_n": top_n,
        "sector_cap": sector_cap,
        "stop_loss": stop_loss,
        "avg_monthly_return_raw": float(monthly_df["return_raw_20d"].mean()),
        "avg_monthly_return_stop": float(monthly_df["return_stop_20d"].mean()),
        "cumulative_return_raw": float(cumulative_raw.iloc[-1] - 1.0),
        "cumulative_return_stop": float(cumulative_stop.iloc[-1] - 1.0),
        "cagr_raw": cagr_raw,
        "cagr_stop": cagr_stop,
        "max_drawdown_raw": max_drawdown(cumulative_raw),
        "max_drawdown_stop": max_drawdown(cumulative_stop),
        "sharpe_raw": sharpe_ratio(monthly_df["return_raw_20d"]),
        "sharpe_stop": sharpe_ratio(monthly_df["return_stop_20d"]),
        "sortino_raw": sortino_ratio(monthly_df["return_raw_20d"]),
        "sortino_stop": sortino_ratio(monthly_df["return_stop_20d"]),
        "win_rate_raw": float(monthly_df["win_rate_raw"].mean()),
        "win_rate_stop": float(monthly_df["win_rate_stop"].mean()),
        "avg_stop_loss_rate": float(monthly_df["stop_loss_rate"].mean()),
        "avg_sector_hhi": float(monthly_df["sector_hhi"].mean()),
        "n_trades": int(len(trades_df)),
    }

    print("-" * 88)
    print(
        f"Summary {Path(meta['model_file']).name}: "
        f"cum_stop={summary['cumulative_return_stop']*100:+.1f}%  "
        f"MDD_stop={summary['max_drawdown_stop']*100:+.1f}%  "
        f"Sharpe_stop={summary['sharpe_stop']:.3f}  "
        f"stop_rate={summary['avg_stop_loss_rate']*100:.1f}%"
    )
    print("-" * 88)

    summary_path = None
    monthly_path = None
    trades_path = None
    if save_reports:
        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_tag = Path(meta["model_file"]).stem
        tag = report_tag or model_tag

        report_dir = Path(REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)

        summary_path = report_dir / f"backtest_{tag}_{run_ts}.json"
        monthly_path = report_dir / f"backtest_monthly_{tag}_{run_ts}.csv"
        trades_path = report_dir / f"backtest_detail_{tag}_{run_ts}.csv"

        with summary_path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        monthly_df.to_csv(monthly_path, index=False)
        trades_df.to_csv(trades_path, index=False)

    return BacktestArtifacts(
        summary=summary,
        monthly=monthly_df,
        trades=trades_df,
        summary_path=summary_path,
        monthly_path=monthly_path,
        trades_path=trades_path,
    )


def print_comparison(primary: BacktestArtifacts, baseline: BacktestArtifacts) -> None:
    ps = primary.summary
    bs = baseline.summary
    print("=" * 88)
    print("Comparison")
    print(f"  primary : {Path(ps['model_file']).name}")
    print(f"  baseline: {Path(bs['model_file']).name}")
    print("=" * 88)
    print(
        f"  cumulative stop return: {bs['cumulative_return_stop']*100:+7.1f}% -> "
        f"{ps['cumulative_return_stop']*100:+7.1f}%  "
        f"delta={(ps['cumulative_return_stop'] - bs['cumulative_return_stop'])*100:+.1f}%"
    )
    print(
        f"  MDD stop:               {bs['max_drawdown_stop']*100:+7.1f}% -> "
        f"{ps['max_drawdown_stop']*100:+7.1f}%  "
        f"delta={(ps['max_drawdown_stop'] - bs['max_drawdown_stop'])*100:+.1f}%"
    )
    print(
        f"  Sharpe stop:            {bs['sharpe_stop']:>7.3f} -> "
        f"{ps['sharpe_stop']:>7.3f}  "
        f"delta={ps['sharpe_stop'] - bs['sharpe_stop']:+.3f}"
    )
    print(
        f"  stop-loss hit rate:     {bs['avg_stop_loss_rate']*100:>7.1f}% -> "
        f"{ps['avg_stop_loss_rate']*100:>7.1f}%  "
        f"delta={(ps['avg_stop_loss_rate'] - bs['avg_stop_loss_rate'])*100:+.1f}%"
    )
    print(
        f"  sector HHI:             {bs['avg_sector_hhi']:>7.3f} -> "
        f"{ps['avg_sector_hhi']:>7.3f}  "
        f"delta={ps['avg_sector_hhi'] - bs['avg_sector_hhi']:+.3f}"
    )
    print("=" * 88)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest saved lgbm models with sector cap and stop-loss.")
    parser.add_argument("--model-meta", type=str, default=None, help="Primary model meta path. Default: latest base lgbm meta.")
    parser.add_argument("--baseline-meta", type=str, default="auto", help="Baseline meta path, 'auto', or 'none'.")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Number of names to hold each month.")
    parser.add_argument("--sector-cap", type=float, default=DEFAULT_SECTOR_CAP, help="Max weight by sector expressed as count ratio.")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS, help="Stop-loss threshold, e.g. -0.10.")
    parser.add_argument("--start", type=str, default=DEFAULT_START, help="Backtest month start, YYYY-MM-DD.")
    parser.add_argument("--end", type=str, default=None, help="Backtest month end, YYYY-MM-DD.")
    parser.add_argument("--report-tag", type=str, default=None, help="Optional report filename tag.")
    parser.add_argument("--no-save", action="store_true", help="Run without writing report files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stop_loss = -abs(args.stop_loss)

    primary_meta = resolve_model_meta_path(args.model_meta, role="primary")
    baseline_meta = resolve_model_meta_path(args.baseline_meta, role="baseline", primary=primary_meta)

    dataset = build_backtest_dataset(stop_loss=stop_loss)
    sector_lookup = load_sector_lookup()

    baseline_artifacts = None
    if baseline_meta is not None:
        baseline_artifacts = run_single_backtest(
            dataset=dataset,
            sector_lookup=sector_lookup,
            meta_path=baseline_meta,
            top_n=args.top_n,
            sector_cap=args.sector_cap,
            stop_loss=stop_loss,
            start=args.start,
            end=args.end,
            save_reports=not args.no_save,
            report_tag=(f"baseline_{args.report_tag}" if args.report_tag else None),
        )

    primary_artifacts = run_single_backtest(
        dataset=dataset,
        sector_lookup=sector_lookup,
        meta_path=primary_meta,
        top_n=args.top_n,
        sector_cap=args.sector_cap,
        stop_loss=stop_loss,
        start=args.start,
        end=args.end,
        save_reports=not args.no_save,
        report_tag=args.report_tag,
    )

    if baseline_artifacts is not None:
        print_comparison(primary_artifacts, baseline_artifacts)

        primary_model = Path(primary_artifacts.summary["model_file"]).name
        baseline_model = Path(baseline_artifacts.summary["model_file"]).name
        primary_better_mdd = primary_artifacts.summary["max_drawdown_stop"] > baseline_artifacts.summary["max_drawdown_stop"]
        primary_better_sharpe = primary_artifacts.summary["sharpe_stop"] > baseline_artifacts.summary["sharpe_stop"]

        verdict = (
            f"Decision gate: MDD better={primary_better_mdd}, "
            f"Sharpe better={primary_better_sharpe}  "
            f"({baseline_model} -> {primary_model})"
        )
        print(verdict)

    if not args.no_save:
        for label, artifacts in [("primary", primary_artifacts), ("baseline", baseline_artifacts)]:
            if artifacts is None:
                continue
            print(
                f"{label} reports: "
                f"summary={artifacts.summary_path}  "
                f"monthly={artifacts.monthly_path}  "
                f"trades={artifacts.trades_path}"
            )


if __name__ == "__main__":
    main()
