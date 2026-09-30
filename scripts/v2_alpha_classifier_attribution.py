"""Attribute V2 alpha-classifier false positives and false negatives.

This report compares two error buckets from the PR90 experiment:

* False positives: PR90-selected positions that stopped out or lost badly.
* False negatives: baseline winners that were not active PR90 signals.

The feature snapshot is rebuilt point-in-time per case from local daily K data,
while classifier probability/rank context is read from the backfilled
predictions_alpha_cls_YYYY-MM-DD.csv artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR
from ml.features.registry import get_available_features
from scripts.v1_snapshot_ab_test import _compute_single_stock_features

DEFAULT_PR90_POSITIONS = Path(REPORT_DIR) / "alpha_pr90_replay_full" / "unified_execution_replay_positions_latest.csv"
DEFAULT_BASELINE_POSITIONS = (
    Path(REPORT_DIR)
    / "baseline_replay_20260409_20260421"
    / "unified_execution_replay_positions_latest.csv"
)
DEFAULT_OUTPUT_CSV = Path(REPORT_DIR) / "v2_alpha_classifier_attribution_latest.csv"
DEFAULT_OUTPUT_SUMMARY_CSV = Path(REPORT_DIR) / "v2_alpha_classifier_attribution_summary_latest.csv"
DEFAULT_OUTPUT_MD = Path(REPORT_DIR) / "v2_alpha_classifier_attribution_latest.md"

FALSE_POSITIVE_MAX_RETURN = -0.05
FALSE_NEGATIVE_MIN_RETURN = 0.08

PROFILE_COLUMNS = [
    "alpha_win_prob_20d",
    "alpha_win_prob_percentile",
    "alpha_win_prob_rank",
    "pred_return_20d",
    "risk_adjusted_return",
    "leaderboard_score",
    "entry_slippage_pct",
    "effective_return_pct",
    "price_vs_ma20",
    "price_vs_ma60",
    "position_52w",
    "dist_to_high_20d",
    "dist_to_high_60d",
    "return_1d",
    "return_3d",
    "return_5d",
    "return_10d",
    "return_20d",
    "return_60d",
    "momentum_accel",
    "gap_pct",
    "volatility_5d",
    "volatility_20d",
    "atr_pct",
    "atr_pct_rank",
    "high_low_range",
    "vol_ratio_5_20",
    "vol_zscore",
    "bb_position",
    "bb_width",
    "rsi_6",
    "rsi_14",
    "macd_hist",
    "kd_k",
    "adx_14",
    "aroon_up",
    "aroon_down",
    "up_day_ratio_20",
    "upper_shadow_ratio",
    "lower_shadow_ratio",
    "volume_today",
    "avg_5d_volume",
    "avg_20d_volume",
    "avg_5d_amount",
    "avg_20d_amount",
    "beta_60",
    "foreign_cumsum_5d",
    "foreign_cumsum_10d",
    "foreign_cumsum_20d",
    "trust_cumsum_5d",
    "trust_cumsum_10d",
    "trust_cumsum_20d",
    "dealer_cumsum_5d",
    "dealer_cumsum_10d",
    "inst_total_5d",
    "inst_total_10d",
    "inst_accumulation",
    "revenue_yoy",
    "revenue_qoq",
    "revenue_momentum",
    "roa_annualized",
    "roe_annualized",
    "debt_ratio",
    "debt_ratio_trend",
    "gross_margin_latest",
    "operating_margin_latest",
    "net_margin_latest",
    "eps_yoy",
    "eps_qoq",
]


def _read_positions(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"ticker": str})
    df["prediction_date"] = df["prediction_date"].astype(str)
    df["ticker"] = df["ticker"].astype(str).str.strip()
    for col in ["effective_return_pct", "realized_return_pct", "entry_slippage_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _active_pr90_signal_set() -> set[tuple[str, str]]:
    active: set[tuple[str, str]] = set()
    for path in Path(MODEL_DIR).glob("unified_signals_alpha_pr90_*.csv"):
        df = pd.read_csv(path, dtype={"ticker": str})
        if {"prediction_date", "ticker", "signal_type"} - set(df.columns):
            continue
        selected = df[df["signal_type"].astype(str) != "NONE"]
        for row in selected[["prediction_date", "ticker"]].itertuples(index=False):
            active.add((str(row.prediction_date), str(row.ticker).strip()))
    return active


def _load_prediction_context(prediction_date: str, ticker: str) -> dict[str, Any]:
    path = Path(MODEL_DIR) / f"predictions_alpha_cls_{prediction_date}.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"ticker": str})
    row = df[df["ticker"].astype(str).str.strip().eq(str(ticker).strip())]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def _load_signal_context(prediction_date: str, ticker: str) -> dict[str, Any]:
    path = Path(MODEL_DIR) / f"unified_signals_alpha_pr90_{prediction_date}.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"ticker": str})
    row = df[df["ticker"].astype(str).str.strip().eq(str(ticker).strip())]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def _build_case_feature_row(prediction_date: str, ticker: str) -> dict[str, Any]:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return {"feature_error": f"daily K not found: {path}"}

    as_of = pd.Timestamp(prediction_date)
    history = pd.read_csv(path, dtype={"Date": str})
    history["Date"] = pd.to_datetime(history["Date"], errors="coerce").astype("datetime64[ns]")
    history = history.dropna(subset=["Date"]).sort_values("Date")
    history = history[history["Date"] <= as_of].copy()
    if history.empty:
        return {"feature_error": f"no history up to {prediction_date}"}

    history["Ticker"] = str(ticker)
    featured = _compute_single_stock_features(
        history,
        ticker=str(ticker),
        available=get_available_features(),
    )
    latest = featured[featured["Date"].dt.normalize().eq(as_of.normalize())].tail(1)
    if latest.empty:
        return {"feature_error": f"no feature row on {prediction_date}"}
    row = latest.iloc[0].to_dict()
    return {
        key: row.get(key)
        for key in PROFILE_COLUMNS
        if key not in {
            "alpha_win_prob_20d",
            "alpha_win_prob_percentile",
            "alpha_win_prob_rank",
            "pred_return_20d",
            "risk_adjusted_return",
            "leaderboard_score",
            "entry_slippage_pct",
            "effective_return_pct",
            "volume_today",
            "avg_5d_volume",
            "avg_20d_volume",
            "avg_5d_amount",
            "avg_20d_amount",
            "beta_60",
        }
    }


def _select_cases(
    pr90_positions: pd.DataFrame,
    baseline_positions: pd.DataFrame,
    *,
    fp_max_return: float,
    fn_min_return: float,
) -> pd.DataFrame:
    false_positive = pr90_positions[
        pr90_positions["status"].astype(str).eq("stopped_out")
        | pr90_positions["effective_return_pct"].le(fp_max_return)
    ].copy()
    false_positive["case_type"] = "false_positive_pr90_loss"
    false_positive["case_reason"] = "PR90 selected but stopped out or <= loss threshold"

    active = _active_pr90_signal_set()
    base = baseline_positions.copy()
    base["_active_pr90"] = base.apply(
        lambda row: (str(row["prediction_date"]), str(row["ticker"])) in active,
        axis=1,
    )
    false_negative = base[
        base["effective_return_pct"].ge(fn_min_return) & ~base["_active_pr90"]
    ].copy()
    false_negative["case_type"] = "false_negative_missed_winner"
    false_negative["case_reason"] = "Baseline winner not active in PR90 signal"

    keep_cols = [
        "case_type",
        "case_reason",
        "prediction_date",
        "ticker",
        "status",
        "entry_date",
        "entry_price",
        "exit_reason",
        "realized_return_pct",
        "effective_return_pct",
        "entry_slippage_pct",
        "order_type",
    ]
    combined = pd.concat([false_positive, false_negative], ignore_index=True, sort=False)
    for col in keep_cols:
        if col not in combined.columns:
            combined[col] = np.nan
    return combined[keep_cols].sort_values(["case_type", "prediction_date", "ticker"])


def _build_profiles(cases: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for case in cases.itertuples(index=False):
        base = case._asdict()
        prediction_date = str(base["prediction_date"])
        ticker = str(base["ticker"])
        pred_context = _load_prediction_context(prediction_date, ticker)
        signal_context = _load_signal_context(prediction_date, ticker)
        feature_context = _build_case_feature_row(prediction_date, ticker)

        row = dict(base)
        for col in [
            "alpha_win_prob_20d",
            "alpha_win_prob_percentile",
            "alpha_win_prob_rank",
            "pred_return_20d",
            "risk_adjusted_return",
            "leaderboard_score",
            "volume_today",
            "avg_5d_volume",
            "avg_20d_volume",
            "avg_5d_amount",
            "avg_20d_amount",
            "beta_60",
            "recommendation",
            "risk_tags",
            "sector",
        ]:
            row[col] = pred_context.get(col)

        for col in ["signal_type", "tradability_reason", "rank_20d"]:
            row[f"pr90_{col}"] = signal_context.get(col)

        row.update(feature_context)
        rows.append(row)

    out = pd.DataFrame(rows)
    for col in PROFILE_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _summary(profiles: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [col for col in PROFILE_COLUMNS if col in profiles.columns]
    summary = profiles.groupby("case_type")[numeric_cols].mean(numeric_only=True)
    counts = profiles.groupby("case_type").size().rename("count")
    summary = summary.join(counts).reset_index()
    return summary


def _pct(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric * 100:.2f}%"


def _num(value: Any, digits: int = 3) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric:.{digits}f}"


def _write_markdown(path: Path, profiles: pd.DataFrame, summary: pd.DataFrame) -> None:
    fp = profiles[profiles["case_type"].eq("false_positive_pr90_loss")]
    fn = profiles[profiles["case_type"].eq("false_negative_missed_winner")]

    lines = [
        "# V2 Alpha Classifier Attribution",
        "",
        "## Scope",
        "",
        f"- False positives: {len(fp)}",
        f"- False negatives: {len(fn)}",
        "- False positive rule: PR90 selected and stopped out or effective return <= -5%.",
        "- False negative rule: baseline effective return >= +8% and not active in PR90 signal.",
        "",
        "## Case Table",
        "",
        "| type | date | ticker | alpha_prob | alpha_pct | alpha_rank | pred_return | slippage | effective_return | key_status |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in profiles.sort_values(["case_type", "prediction_date", "ticker"]).itertuples(index=False):
        lines.append(
            "| "
            f"{row.case_type} | {row.prediction_date} | {row.ticker} | "
            f"{_pct(getattr(row, 'alpha_win_prob_20d', None))} | "
            f"{_pct(getattr(row, 'alpha_win_prob_percentile', None))} | "
            f"{_num(getattr(row, 'alpha_win_prob_rank', None), 0)} | "
            f"{_pct(getattr(row, 'pred_return_20d', None))} | "
            f"{_pct(getattr(row, 'entry_slippage_pct', None))} | "
            f"{_pct(getattr(row, 'effective_return_pct', None))} | "
            f"{row.status}/{getattr(row, 'exit_reason', '')} |"
        )

    focus_cols = [
        "alpha_win_prob_percentile",
        "pred_return_20d",
        "entry_slippage_pct",
        "price_vs_ma60",
        "gap_pct",
        "volatility_20d",
        "vol_ratio_5_20",
        "atr_pct_rank",
        "beta_60",
        "foreign_cumsum_20d",
        "trust_cumsum_20d",
        "revenue_qoq",
        "roa_annualized",
    ]
    available = [col for col in focus_cols if col in profiles.columns]
    lines.extend(
        [
            "",
            "## Group Means",
            "",
            "| metric | false_positive | false_negative | FP - FN |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    means = profiles.groupby("case_type")[available].mean(numeric_only=True)
    for col in available:
        fp_value = means.loc["false_positive_pr90_loss", col] if "false_positive_pr90_loss" in means.index else np.nan
        fn_value = means.loc["false_negative_missed_winner", col] if "false_negative_missed_winner" in means.index else np.nan
        delta = fp_value - fn_value if pd.notna(fp_value) and pd.notna(fn_value) else np.nan
        formatter = _pct if col.endswith("_pct") or col in {
            "alpha_win_prob_percentile",
            "pred_return_20d",
            "entry_slippage_pct",
            "price_vs_ma60",
            "gap_pct",
            "volatility_20d",
            "revenue_qoq",
            "roa_annualized",
        } else _num
        lines.append(f"| {col} | {formatter(fp_value)} | {formatter(fn_value)} | {formatter(delta)} |")

    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            "- The classifier is useful only if its high-percentile names preserve winners while reducing stop-loss names.",
            "- Large positive FP-FN gaps in slippage, volatility, gap, or volume-ratio metrics indicate adverse selection toward execution-risk proxies.",
            "- Large negative FP-FN gaps in fundamental or institutional metrics indicate the model is underweighting the features that explain missed winners.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_attribution(args: argparse.Namespace) -> dict[str, str]:
    pr90 = _read_positions(Path(args.pr90_positions))
    baseline = _read_positions(Path(args.baseline_positions))
    cases = _select_cases(
        pr90,
        baseline,
        fp_max_return=args.false_positive_max_return,
        fn_min_return=args.false_negative_min_return,
    )
    profiles = _build_profiles(cases)
    summary = _summary(profiles)

    output_csv = Path(args.output_csv)
    output_summary_csv = Path(args.output_summary_csv)
    output_md = Path(args.output_md)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    profiles.to_csv(output_csv, index=False, encoding="utf-8-sig")
    summary.to_csv(output_summary_csv, index=False, encoding="utf-8-sig")
    _write_markdown(output_md, profiles, summary)

    print(f"[alpha-attribution] cases={len(profiles)} report={output_md}")
    return {
        "csv": str(output_csv),
        "summary_csv": str(output_summary_csv),
        "markdown": str(output_md),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build V2 alpha classifier attribution report.")
    parser.add_argument("--pr90-positions", default=str(DEFAULT_PR90_POSITIONS))
    parser.add_argument("--baseline-positions", default=str(DEFAULT_BASELINE_POSITIONS))
    parser.add_argument("--false-positive-max-return", type=float, default=FALSE_POSITIVE_MAX_RETURN)
    parser.add_argument("--false-negative-min-return", type=float, default=FALSE_NEGATIVE_MIN_RETURN)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-summary-csv", default=str(DEFAULT_OUTPUT_SUMMARY_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    run_attribution(args)


if __name__ == "__main__":
    main()
