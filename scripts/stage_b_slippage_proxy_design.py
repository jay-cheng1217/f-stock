"""RD-002 Stage B ex-ante slippage proxy offline evaluation.

This is an offline design artifact only. It ranks orders by ex-ante proxy
scores and compares those ranks with realized entry slippage from Stage A.
It must not change production order behavior or use NAV attribution residuals.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from scripts.slippage_stage_a_oracle import (
    DEFAULT_DB_PATH,
    STAGE_B_MIN_ORACLE_EDGE,
    load_positions,
    prepare_slippage_frame,
)

MIN_SHADOW_TRADES = 100
MIN_TAIL_CAPTURE = 0.40
THRESHOLD_QUANTILES = (0.60, 0.70, 0.80, 0.90)
BOOTSTRAP_ROUNDS = 500

CANDIDATE_SCORE_COLUMNS = (
    "chase_intensity_proxy",
    "liquidity_impact_proxy",
    "volatility_exhaustion_proxy",
    "open_stress_proxy",
    "hybrid_rule_proxy",
)

FORBIDDEN_PROXY_INPUT_COLUMNS = {
    "entry_slippage_pct",
    "positive_slippage_pct",
    "weighted_slippage_cost_pct",
    "price_improvement_pct",
    "weighted_price_improvement_pct",
    "realized_return_pct",
    "latest_close_return_pct",
    "effective_return_pct",
    "weighted_effective_return_pct",
    "NAV unexplained",
    "B1",
    "B2",
    "B3",
    "B4",
    "b4_dominance_flag",
    "cross_window_sign_stability",
}

EX_ANTE_INPUT_COLUMNS = {
    "order_type",
    "target_weight",
    "target_units",
    "planned_entry_ref_price",
    "price_vs_ma20_at_entry",
    "entry_rank_20d",
    "entry_score",
    "entry_prob_edge",
    "entry_risk_adjusted_return_pre_penalty",
    "entry_penalty_overlay_total",
}


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _num(df: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _rank01(series: pd.Series, *, neutral: float = 0.5) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() <= 1 or values.nunique(dropna=True) <= 1:
        return pd.Series(neutral, index=series.index, dtype="float64")
    return values.rank(pct=True, method="average").fillna(neutral).astype("float64")


def _safe_corr(x: pd.Series, y: pd.Series, *, method: str = "spearman") -> float:
    aligned = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(aligned) < 3 or aligned["x"].nunique() <= 1 or aligned["y"].nunique() <= 1:
        return float("nan")
    return float(aligned["x"].corr(aligned["y"], method=method))


def _bootstrap_spearman_lower(
    df: pd.DataFrame,
    score_col: str,
    *,
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = 7,
) -> float:
    sample = df[[score_col, "entry_slippage_pct"]].dropna()
    if len(sample) < 10 or sample[score_col].nunique() <= 1:
        return float("nan")
    rng = np.random.default_rng(seed)
    values: list[float] = []
    sample_size = len(sample)
    for _ in range(rounds):
        idx = rng.integers(0, sample_size, sample_size)
        boot = sample.iloc[idx]
        corr = _safe_corr(boot[score_col], boot["entry_slippage_pct"])
        if np.isfinite(corr):
            values.append(corr)
    if not values:
        return float("nan")
    return float(np.percentile(values, 5))


def _calibration_stats(df: pd.DataFrame, score_col: str) -> dict[str, float]:
    sample = df[[score_col, "entry_slippage_pct"]].dropna()
    if len(sample) < 3 or sample[score_col].nunique() <= 1:
        return {"calibration_mae_pct": float("nan"), "calibration_slope": float("nan"), "calibration_intercept": float("nan")}
    x = sample[score_col].astype(float).to_numpy()
    y = sample["entry_slippage_pct"].astype(float).to_numpy()
    design = np.column_stack([np.ones(len(x)), x])
    intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
    pred = intercept + slope * x
    return {
        "calibration_mae_pct": float(np.mean(np.abs(pred - y))),
        "calibration_slope": float(slope),
        "calibration_intercept": float(intercept),
    }


def _liquidity_bucket(order_notional_proxy: pd.Series) -> pd.Series:
    labels = ["Q1_low_order_size", "Q2", "Q3", "Q4_high_order_size"]
    values = pd.to_numeric(order_notional_proxy, errors="coerce")
    if values.notna().sum() < 4 or values.nunique(dropna=True) < 4:
        return pd.Series("unknown_order_size", index=values.index, dtype="object")
    ranked = values.rank(method="first")
    return pd.qcut(ranked, q=4, labels=labels).astype("object")


def prepare_stage_b_features(stage_a_frame: pd.DataFrame) -> pd.DataFrame:
    """Add rule-first ex-ante proxy scores to a Stage A slippage frame."""
    forbidden_overlap = EX_ANTE_INPUT_COLUMNS & FORBIDDEN_PROXY_INPUT_COLUMNS
    if forbidden_overlap:
        raise ValueError(f"forbidden ex-ante inputs configured: {sorted(forbidden_overlap)}")

    df = stage_a_frame.copy()
    df["order_type"] = df.get("order_type", "UNKNOWN").fillna("UNKNOWN").astype(str)
    df["entry_date"] = df.get("entry_date", "").fillna("").astype(str)
    df["ticker"] = df.get("ticker", "").fillna("").astype(str)
    df["target_weight"] = _num(df, "target_weight", 0.0).fillna(0.0)
    df["target_units"] = _num(df, "target_units", 0.0).fillna(0.0)
    df["planned_entry_ref_price"] = _num(df, "planned_entry_ref_price")
    df["order_notional_proxy"] = df["target_units"] * df["planned_entry_ref_price"]

    intraday = (df["order_type"].str.upper() == "INTRADAY_CHASE").astype(float)
    open_order = (df["order_type"].str.upper() == "OPEN").astype(float)
    price_vs_ma20 = _num(df, "price_vs_ma20_at_entry")
    entry_prob_edge = _num(df, "entry_prob_edge")

    target_weight_rank = _rank01(df["target_weight"])
    notional_rank = _rank01(df["order_notional_proxy"])
    price_positive_rank = _rank01(price_vs_ma20.clip(lower=0.0))
    price_abs_rank = _rank01(price_vs_ma20.abs())
    prob_edge_rank = _rank01(entry_prob_edge)

    df["chase_intensity_proxy"] = (0.65 * intraday) + (0.20 * price_positive_rank) + (0.15 * prob_edge_rank)
    df["liquidity_impact_proxy"] = (0.55 * target_weight_rank) + (0.35 * notional_rank) + (0.10 * intraday)
    df["volatility_exhaustion_proxy"] = (0.45 * price_abs_rank) + (0.35 * price_positive_rank) + (0.20 * target_weight_rank)
    df["open_stress_proxy"] = (0.60 * open_order) + (0.25 * price_abs_rank) + (0.15 * target_weight_rank)
    df["hybrid_rule_proxy"] = (
        0.40 * df["chase_intensity_proxy"]
        + 0.25 * df["liquidity_impact_proxy"]
        + 0.25 * df["volatility_exhaustion_proxy"]
        + 0.10 * df["open_stress_proxy"]
    )
    df["liquidity_bucket_proxy"] = _liquidity_bucket(df["order_notional_proxy"])
    return df


def _threshold_row(df: pd.DataFrame, score_col: str, quantile: float) -> dict[str, Any]:
    cutoff = float(df[score_col].quantile(quantile))
    flagged = df[df[score_col] >= cutoff].copy()
    weighted_return = flagged["weighted_effective_return_pct"].fillna(0.0)
    avoided_bad = float((-weighted_return[weighted_return < 0]).sum())
    missed_good = float(weighted_return[weighted_return > 0].sum())
    slippage_saved = float(flagged["weighted_slippage_cost_pct"].sum())
    avoid_miss_delta = avoided_bad - missed_good
    return {
        "candidate": score_col,
        "threshold_quantile": quantile,
        "threshold_label": f"top_{int(round((1.0 - quantile) * 100))}pct",
        "score_cutoff": cutoff,
        "trade_count": int(len(flagged)),
        "slippage_saved_pct": slippage_saved,
        "avoided_bad_chase_pct": avoided_bad,
        "missed_good_chase_pct": missed_good,
        "avoid_miss_delta_pct": avoid_miss_delta,
        "net_skip_oracle_pct": slippage_saved + avoided_bad - missed_good,
    }


def _bootstrap_avoid_miss_delta_ci(
    df: pd.DataFrame,
    score_col: str,
    score_cutoff: float,
    *,
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = 23,
) -> dict[str, float]:
    sample = df[[score_col, "weighted_effective_return_pct"]].dropna()
    if len(sample) < 10 or sample[score_col].nunique() <= 1:
        return {
            "avoid_miss_bootstrap_90_lower_pct": float("nan"),
            "avoid_miss_bootstrap_median_pct": float("nan"),
            "avoid_miss_bootstrap_90_upper_pct": float("nan"),
        }

    rng = np.random.default_rng(seed)
    values: list[float] = []
    sample_size = len(sample)
    for _ in range(rounds):
        boot = sample.iloc[rng.integers(0, sample_size, sample_size)]
        flagged = boot[boot[score_col] >= score_cutoff]
        weighted_return = flagged["weighted_effective_return_pct"].fillna(0.0)
        avoided_bad = float((-weighted_return[weighted_return < 0]).sum())
        missed_good = float(weighted_return[weighted_return > 0].sum())
        values.append(avoided_bad - missed_good)

    lower, median, upper = np.percentile(values, [5, 50, 95])
    return {
        "avoid_miss_bootstrap_90_lower_pct": float(lower),
        "avoid_miss_bootstrap_median_pct": float(median),
        "avoid_miss_bootstrap_90_upper_pct": float(upper),
    }


def evaluate_candidate(df: pd.DataFrame, score_col: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    thresholds = [_threshold_row(df, score_col, quantile) for quantile in THRESHOLD_QUANTILES]
    best = max(thresholds, key=lambda row: row["net_skip_oracle_pct"])
    total_cost = float(df["weighted_slippage_cost_pct"].sum())
    top20 = df[df[score_col] >= float(df[score_col].quantile(0.80))]
    tail_capture = float(top20["weighted_slippage_cost_pct"].sum() / total_cost) if total_cost > 0 else 0.0
    spearman = _safe_corr(df[score_col], df["entry_slippage_pct"])
    spearman_lower = _bootstrap_spearman_lower(df, score_col)
    avoid_miss_ci = _bootstrap_avoid_miss_delta_ci(df, score_col, best["score_cutoff"])
    calibration = _calibration_stats(df, score_col)
    avoid_miss_ci_pass = bool(
        np.isfinite(avoid_miss_ci["avoid_miss_bootstrap_90_lower_pct"])
        and avoid_miss_ci["avoid_miss_bootstrap_90_lower_pct"] > 0
    )
    shadow_gates = {
        "min_trade_count": int(len(df) >= MIN_SHADOW_TRADES),
        "tail_capture": int(tail_capture >= MIN_TAIL_CAPTURE),
        "spearman_bootstrap_lower_positive": int(np.isfinite(spearman_lower) and spearman_lower > 0),
        "net_skip_above_050pp": int(best["net_skip_oracle_pct"] > STAGE_B_MIN_ORACLE_EDGE),
        "avoided_bad_gt_missed_good": int(best["avoided_bad_chase_pct"] > best["missed_good_chase_pct"]),
    }
    summary = {
        "candidate": score_col,
        "trade_count": int(len(df)),
        "spearman": spearman,
        "spearman_bootstrap_90_lower": spearman_lower,
        "top_quintile_trade_count": int(len(top20)),
        "top_quintile_tail_capture_pct": tail_capture,
        "best_threshold_quantile": best["threshold_quantile"],
        "best_threshold_label": best["threshold_label"],
        "best_score_cutoff": best["score_cutoff"],
        "best_net_skip_oracle_pct": best["net_skip_oracle_pct"],
        "best_slippage_saved_pct": best["slippage_saved_pct"],
        "best_avoided_bad_chase_pct": best["avoided_bad_chase_pct"],
        "best_missed_good_chase_pct": best["missed_good_chase_pct"],
        "best_avoid_miss_delta_pct": best["avoid_miss_delta_pct"],
        **avoid_miss_ci,
        "pm_condition_avoid_miss_ci_pass": avoid_miss_ci_pass,
        **calibration,
        "shadow_gate_results": shadow_gates,
        "shadow_candidate_offline": bool(all(shadow_gates.values())),
        "shadow_review_ready_with_pm_condition_1": bool(all(shadow_gates.values()) and avoid_miss_ci_pass),
    }
    return summary, thresholds


def _group_metric_rows(df: pd.DataFrame, score_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = {
        "order_type": "order_type",
        "entry_date": "entry_date",
        "ticker": "ticker",
        "liquidity_bucket_proxy": "liquidity_bucket_proxy",
    }
    for group_type, column in groups.items():
        for group_value, group in df.groupby(column, dropna=False):
            rows.append(
                {
                    "candidate": score_col,
                    "group_type": group_type,
                    "group_value": str(group_value),
                    "trade_count": int(len(group)),
                    "avg_proxy_score": float(group[score_col].mean()),
                    "avg_realized_entry_slippage_pct": float(group["entry_slippage_pct"].mean()),
                    "slippage_cost_pct": float(group["weighted_slippage_cost_pct"].sum()),
                    "avg_effective_return_pct": float(group["effective_return_pct"].mean()),
                }
            )
    return rows


def _feature_coverage(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for column in sorted(EX_ANTE_INPUT_COLUMNS):
        if column in df.columns:
            non_null = int(df[column].notna().sum())
        else:
            non_null = 0
        rows.append(
            {
                "field": column,
                "non_null_count": non_null,
                "coverage_pct": float(non_null / len(df)) if len(df) else 0.0,
            }
        )
    rows.append(
        {
            "field": "ADV / intraday volume curve",
            "non_null_count": 0,
            "coverage_pct": 0.0,
            "note": "not available; liquidity impact uses order-size proxy only",
        }
    )
    return rows


def _case_6419(df: pd.DataFrame, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case = df[(df["ticker"] == "6419") & (df["entry_date"] == "2026-05-12")]
    if case.empty:
        return []
    row = case.iloc[0]
    records: list[dict[str, Any]] = []
    for summary in summaries:
        candidate = summary["candidate"]
        records.append(
            {
                "ticker": row["ticker"],
                "entry_date": row["entry_date"],
                "candidate": candidate,
                "order_type": row["order_type"],
                "target_weight": float(row["target_weight"]),
                "planned_entry_ref_price": float(row["planned_entry_ref_price"]),
                "price_vs_ma20_at_entry": float(row["price_vs_ma20_at_entry"]),
                "entry_rank_20d": float(row["entry_rank_20d"]),
                "entry_score": float(row["entry_score"]),
                "entry_prob_edge": float(row["entry_prob_edge"]),
                "proxy_score": float(row[candidate]),
                "score_cutoff": float(summary["best_score_cutoff"]),
                "would_flag_at_best_threshold": bool(row[candidate] >= summary["best_score_cutoff"]),
            }
        )
    return records


def analyze_stage_b_proxy(stage_a_frame: pd.DataFrame) -> dict[str, Any]:
    df = prepare_stage_b_features(stage_a_frame)
    candidate_summaries: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    for score_col in CANDIDATE_SCORE_COLUMNS:
        summary, thresholds = evaluate_candidate(df, score_col)
        candidate_summaries.append(summary)
        threshold_rows.extend(thresholds)
        group_rows.extend(_group_metric_rows(df, score_col))

    selected = max(candidate_summaries, key=lambda row: row["best_net_skip_oracle_pct"])
    any_shadow_candidate = any(row["shadow_candidate_offline"] for row in candidate_summaries)
    any_pm_condition_1_ready = any(row["shadow_review_ready_with_pm_condition_1"] for row in candidate_summaries)
    recommendation = (
        "OFFLINE_PROXY_READY_FOR_SHADOW_REVIEW_AFTER_BASELINE"
        if any_pm_condition_1_ready
        else (
            "OFFLINE_PROXY_APPROVED_SHADOW_REVIEW_HELD_FOR_PM_CONDITIONS"
            if any_shadow_candidate
            else "DATA_EXPANSION_OR_ADVISORY_ONLY_REVIEW_REQUIRED"
        )
    )
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_count": int(len(df)),
        "target_label": "realized_entry_slippage",
        "diagnostic_only": True,
        "production_change": False,
        "rd006_boundary": "NAV/B4/RD-006 reporting labels forbidden as target/features",
        "post_entry_leakage_guard": "post-entry realized returns forbidden as proxy features",
        "candidate_count": len(CANDIDATE_SCORE_COLUMNS),
        "selected_candidate_by_net_skip": selected["candidate"],
        "recommendation": recommendation,
        "shadow_or_production_blocker": "accepted Champion baseline required before shadow/production promotion",
        "pm_condition_1": "(avoided_bad - missed_good) bootstrap 90% lower bound must be above 0 before shadow review",
        "pm_condition_1_any_candidate_pass": any_pm_condition_1_ready,
        "pm_condition_2": "liquidity path decision required before shadow review",
        "pm_condition_2_sa_recommendation": "Path A: collect missing liquidity observations before shadow review",
    }
    return {
        "summary": summary,
        "candidate_metrics": candidate_summaries,
        "thresholds": threshold_rows,
        "group_metrics": group_rows,
        "feature_coverage": _feature_coverage(df),
        "case_6419": _case_6419(df, candidate_summaries),
    }


def _metrics_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| candidate | Spearman | boot 90% lower | top quintile capture | best threshold | net skip | avoided bad | missed good | avoid-miss CI LB | shadow gate | PM CI gate |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['candidate']}` | {row['spearman']:+.3f} | {row['spearman_bootstrap_90_lower']:+.3f} | "
            f"{_pct(row['top_quintile_tail_capture_pct'])} | {row['best_threshold_label']} | "
            f"{_pct(row['best_net_skip_oracle_pct'])} | {_pct(row['best_avoided_bad_chase_pct'])} | "
            f"{_pct(row['best_missed_good_chase_pct'])} | {_pct(row['avoid_miss_bootstrap_90_lower_pct'])} | "
            f"{row['shadow_candidate_offline']} | {row['pm_condition_avoid_miss_ci_pass']} |"
        )
    return lines


def _case_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| candidate | proxy score | best cutoff | would flag | order type | price vs MA20 | rank 20d | prob edge |",
        "|---|---:|---:|---|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['candidate']}` | {row['proxy_score']:.4f} | {row['score_cutoff']:.4f} | "
            f"{row['would_flag_at_best_threshold']} | {row['order_type']} | "
            f"{_pct(row['price_vs_ma20_at_entry'])} | {row['entry_rank_20d']:.0f} | {_pct(row['entry_prob_edge'])} |"
        )
    return lines


def write_report(results: dict[str, Any], *, output_prefix: str, report_dir: str | Path = REPORT_DIR) -> tuple[Path, Path, Path, Path, Path, Path]:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{output_prefix}.md"
    json_path = out_dir / f"{output_prefix}.json"
    candidate_path = out_dir / f"{output_prefix}_candidate_metrics.csv"
    threshold_path = out_dir / f"{output_prefix}_thresholds.csv"
    group_path = out_dir / f"{output_prefix}_group_metrics.csv"
    case_path = out_dir / f"{output_prefix}_6419_case.csv"

    pd.DataFrame(results["candidate_metrics"]).to_csv(candidate_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(results["thresholds"]).to_csv(threshold_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(results["group_metrics"]).to_csv(group_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(results["case_6419"]).to_csv(case_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = results["summary"]
    selected = next(row for row in results["candidate_metrics"] if row["candidate"] == summary["selected_candidate_by_net_skip"])
    lines = [
        "# RD-002 Stage B Ex-Ante Slippage Proxy Offline Evaluation",
        "",
        "Offline evaluation only. No production scheduler, order gate, model, database schema, or Champion attribution path was changed.",
        "",
        "## Scope Locks",
        "- Target label: `realized_entry_slippage` from the RD-002 Stage A trade/slippage frame.",
        "- NAV unexplained, B1/B2/B3/B4, `b4_dominance_flag`, and `cross_window_sign_stability` are forbidden as target/features.",
        "- Post-entry realized returns are forbidden as proxy features and are used only for avoided-bad-chase / missed-good-chase accounting.",
        "- 6419 is treated as an execution-slippage case study only, not selection alpha or allocation evidence.",
        "",
        "## Summary",
        f"- Trades evaluated: {summary['trade_count']}",
        f"- Candidate proxies: {summary['candidate_count']}",
        f"- Selected by offline net skip oracle: `{summary['selected_candidate_by_net_skip']}`",
        f"- Recommendation: **{summary['recommendation']}**",
        f"- Shadow/production blocker: {summary['shadow_or_production_blocker']}",
        f"- PM condition 1 status: any candidate pass = {summary['pm_condition_1_any_candidate_pass']}",
        f"- PM condition 2 SA recommendation: {summary['pm_condition_2_sa_recommendation']}",
        "",
        "## Candidate Metrics",
        *_metrics_table(results["candidate_metrics"]),
        "",
        "## Selected Candidate Detail",
        f"- Candidate: `{selected['candidate']}`",
        f"- Best threshold: {selected['best_threshold_label']} at score >= {selected['best_score_cutoff']:.4f}",
        f"- Net skip oracle: {_pct(selected['best_net_skip_oracle_pct'])}",
        f"- Slippage saved: {_pct(selected['best_slippage_saved_pct'])}",
        f"- Avoided bad chase: {_pct(selected['best_avoided_bad_chase_pct'])}",
        f"- Missed good chase: {_pct(selected['best_missed_good_chase_pct'])}",
        f"- Avoided minus missed: {_pct(selected['best_avoid_miss_delta_pct'])}",
        f"- Avoided-minus-missed bootstrap 90% CI: "
        f"{_pct(selected['avoid_miss_bootstrap_90_lower_pct'])} to {_pct(selected['avoid_miss_bootstrap_90_upper_pct'])}",
        f"- PM condition 1 pass: {selected['pm_condition_avoid_miss_ci_pass']}",
        "",
        "## 6419 Case Appendix",
        "The table below lists only ex-ante feature values and proxy verdicts available before the 2026-05-12 entry. The known +9.85% entry slippage is the offline validation label, not a feature.",
        "",
        *_case_table(results["case_6419"]),
        "",
        "## Feature Coverage Notes",
        "- `order_type`, target size, planned reference price, price-vs-MA20, rank, score, and probability edge are available for the current frame.",
        "- ADV, spread, intraday volume curve, and limit-up/limit-down state are not available in this dataset.",
        "- Liquidity impact is therefore a partial order-size proxy, not a true market-liquidity model.",
        "- SA recommends Path A before shadow review: collect missing liquidity observations under a timebox, then rerun this offline report.",
        "",
        "## Adoption Status",
        "- Offline design: complete.",
        "- Shadow candidate: held until PM conditions pass and Champion baseline is accepted.",
        "- PM condition 1 is not met for the selected hybrid proxy if its bootstrap lower bound is not above zero.",
        "- PM condition 2 requires PM sign-off on the liquidity path decision before shadow review.",
        "- Production candidate: not authorized by this report.",
        "- If PM rejects the current partial liquidity proxy, the next RD step is expanded observation fields, not a production rule.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path, candidate_path, threshold_path, group_path, case_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RD-002 Stage B ex-ante slippage proxy offline evaluation.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--output-prefix", default=f"rd002_stage_b_proxy_design_{datetime.now():%Y%m%d}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    positions = load_positions(args.db_path)
    stage_a_frame = prepare_slippage_frame(positions)
    results = analyze_stage_b_proxy(stage_a_frame)
    paths = write_report(results, output_prefix=args.output_prefix, report_dir=args.report_dir)
    print(f"[stage-b-proxy] report={paths[0]}")
    print(f"[stage-b-proxy] selected={results['summary']['selected_candidate_by_net_skip']}")
    print(f"[stage-b-proxy] recommendation={results['summary']['recommendation']}")
    print(f"[stage-b-proxy] json={paths[1]}")
    print(f"[stage-b-proxy] candidates={paths[2]}")
    print(f"[stage-b-proxy] thresholds={paths[3]}")
    print(f"[stage-b-proxy] groups={paths[4]}")
    print(f"[stage-b-proxy] case_6419={paths[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
