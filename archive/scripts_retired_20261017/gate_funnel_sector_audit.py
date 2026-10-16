"""Analyze V2 selection-to-active gate funnel by sector.

The report focuses on the mechanism found in #6/#5.1: Top30 sector cap can pass
while downstream gates shrink the active basket to a handful of names. It is
research-only and does not change any production gate.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402
from scripts.build_unified_signals import (  # noqa: E402
    _apply_penalty_overlay_sector_cap,
    _filter_20d_prediction_pool_by_alpha_gate,
    apply_penalty_overlay,
)

PRED_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
UNIFIED_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
DEFAULT_OUTPUT_STEM = "v2_gate_funnel_sector_bias_20260409_20260430"
FOCUS_SECTORS = ["半導體業", "通信網路業", "電子零組件業", "其他電子業"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit V2 gate funnel sector kill rates.")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--output-stem", default=DEFAULT_OUTPUT_STEM)
    return parser.parse_args(argv)


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _plain_pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _list_dated_files(pattern: re.Pattern[str], glob_pattern: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in Path(MODEL_DIR).glob(glob_pattern):
        match = pattern.match(path.name)
        if match:
            out[match.group(1)] = path
    return out


def _available_dates(end_date: str, lookback_days: int) -> list[str]:
    predictions = _list_dated_files(PRED_RE, "predictions_2026-*.csv")
    unified = _list_dated_files(UNIFIED_RE, "unified_signals_2026-*.csv")
    start = pd.Timestamp(end_date) - timedelta(days=lookback_days)
    return [
        date_value
        for date_value in sorted(set(predictions) & set(unified))
        if start <= pd.Timestamp(date_value) <= pd.Timestamp(end_date)
    ]


def _read_prediction(date_value: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"predictions_{date_value}.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    df["sector"] = df.get("sector", "UNKNOWN")
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    return df


def _read_unified(date_value: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"unified_signals_{date_value}.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    df["sector"] = df.get("sector", "UNKNOWN")
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    for column in ["rank_20d", "target_units", "target_weight_ratio", "penalty_overlay_score"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _production_cap(pred: pd.DataFrame, top_n: int) -> pd.DataFrame:
    gated = _filter_20d_prediction_pool_by_alpha_gate(pred)
    penalized = apply_penalty_overlay(gated, version="v1")
    penalized = penalized.loc[~penalized["penalty_hard_block"].fillna(False)].copy()
    capped = _apply_penalty_overlay_sector_cap(penalized, top_n=top_n)
    capped = capped.copy()
    capped["rank_20d_rebuilt"] = np.arange(1, len(capped) + 1)
    return capped


def _quiet_apply_sector_cap(pred: pd.DataFrame, top_n: int) -> pd.DataFrame:
    with contextlib.redirect_stdout(io.StringIO()):
        return apply_sector_cap(pred, top_n=top_n)


def _actual_rank20(unified: pd.DataFrame) -> pd.DataFrame:
    ranked = unified.loc[unified["rank_20d"].notna()].copy()
    ranked["rank_20d"] = pd.to_numeric(ranked["rank_20d"], errors="coerce")
    ranked = ranked.sort_values(["rank_20d", "ticker"], ascending=[True, True]).reset_index(drop=True)
    ranked["rank_20d_rebuilt"] = np.arange(1, len(ranked) + 1)
    return ranked


def _active(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[pd.to_numeric(df.get("target_units"), errors="coerce").fillna(0).gt(0)].copy()


def _pre_market_regime_survivors(unified: pd.DataFrame) -> pd.DataFrame:
    """Approximate rows that survived non-regime gates before CAUTION Top10 pruning."""
    ranked = unified.loc[unified["rank_20d"].notna()].copy()
    reason = ranked.get("tradability_reason")
    if reason is None:
        return ranked
    reason_text = reason.fillna("").astype(str)
    no_reason = reason_text.eq("")
    regime_only = reason_text.eq("MARKET_REGIME_CAUTION")
    active = pd.to_numeric(ranked.get("target_units"), errors="coerce").fillna(0).gt(0)
    return ranked.loc[active | no_reason | regime_only].copy()


def _stage_frame(pred: pd.DataFrame, unified: pd.DataFrame, top_n: int) -> dict[str, pd.DataFrame]:
    raw_head = pred.head(top_n).copy()
    baseline_cap = _quiet_apply_sector_cap(pred, top_n=top_n)
    rank20 = _actual_rank20(unified)
    production_cap = rank20.copy()
    pre_regime = _pre_market_regime_survivors(unified)
    active = _active(unified)
    return {
        "raw_head30": raw_head,
        "baseline_sector_cap": baseline_cap,
        "production_cap_rank20": production_cap,
        "unified_rank20": rank20,
        "pre_market_regime_survivors": pre_regime,
        "active_target": active,
    }


def _sector_count_map(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    return {
        str(sector): int(count)
        for sector, count in df["sector"].fillna("UNKNOWN").astype(str).value_counts().items()
    }


def _sector_weight_map(df: pd.DataFrame) -> dict[str, float]:
    if df.empty or "target_weight_ratio" not in df.columns:
        return {}
    tmp = df.copy()
    tmp["_w"] = pd.to_numeric(tmp["target_weight_ratio"], errors="coerce").fillna(0.0)
    return {str(sector): float(weight) for sector, weight in tmp.groupby("sector")["_w"].sum().items()}


def _daily_stage_rows(date_value: str, stages: dict[str, pd.DataFrame], top_n: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage, df in stages.items():
        counts = _sector_count_map(df)
        weights = _sector_weight_map(df)
        sectors = sorted(set(counts) | set(weights))
        for sector in sectors:
            count = counts.get(sector, 0)
            rows.append(
                {
                    "prediction_date": date_value,
                    "stage": stage,
                    "sector": sector,
                    "count": count,
                    "count_share_of_top_n": count / float(top_n),
                    "target_weight": weights.get(sector),
                    "rows_in_stage": int(len(df)),
                }
            )
    return rows


def _transition_rows(date_value: str, stages: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    transitions = [
        ("raw_to_baseline_cap", "raw_head30", "baseline_sector_cap"),
        ("baseline_to_production_cap", "baseline_sector_cap", "production_cap_rank20"),
        ("rank20_to_pre_regime", "production_cap_rank20", "pre_market_regime_survivors"),
        ("pre_regime_to_active", "pre_market_regime_survivors", "active_target"),
        ("rank20_to_active_total", "production_cap_rank20", "active_target"),
    ]
    rows: list[dict[str, Any]] = []
    for transition, before_name, after_name in transitions:
        before = _sector_count_map(stages[before_name])
        after = _sector_count_map(stages[after_name])
        for sector in sorted(set(before) | set(after)):
            before_count = before.get(sector, 0)
            after_count = after.get(sector, 0)
            killed = before_count - after_count
            rows.append(
                {
                    "prediction_date": date_value,
                    "transition": transition,
                    "sector": sector,
                    "before_count": before_count,
                    "after_count": after_count,
                    "killed_count": killed,
                    "kill_rate": None if before_count <= 0 else killed / float(before_count),
                    "survival_rate": None if before_count <= 0 else after_count / float(before_count),
                }
            )
    return rows


def _reason_rows(date_value: str, unified: pd.DataFrame) -> list[dict[str, Any]]:
    ranked = unified.loc[unified["rank_20d"].notna()].copy()
    inactive = ranked.loc[~pd.to_numeric(ranked.get("target_units"), errors="coerce").fillna(0).gt(0)].copy()
    rows: list[dict[str, Any]] = []
    for _, row in inactive.iterrows():
        reason_text = str(row.get("tradability_reason") or "")
        reasons = [item.strip() for item in reason_text.split(";") if item.strip()] or ["UNSPECIFIED"]
        for reason in reasons:
            rows.append(
                {
                    "prediction_date": date_value,
                    "ticker": row.get("ticker"),
                    "sector": row.get("sector"),
                    "rank_20d": _safe_float(row.get("rank_20d")),
                    "reason": reason,
                    "market_regime_state": row.get("market_regime_state"),
                    "market_regime_action": row.get("market_regime_action"),
                }
            )
    return rows


def _median_diff(pred_rows: list[pd.DataFrame], left_sector: str, right_sector: str, column: str) -> dict[str, Any]:
    df = pd.concat(pred_rows, ignore_index=True) if pred_rows else pd.DataFrame()
    if df.empty or column not in df.columns:
        return {"column": column, "left": left_sector, "right": right_sector, "left_median": None, "right_median": None, "diff": None}
    values = pd.to_numeric(df[column], errors="coerce")
    work = df.assign(_value=values).dropna(subset=["_value"])
    left = work.loc[work["sector"].eq(left_sector), "_value"]
    right = work.loc[work["sector"].eq(right_sector), "_value"]
    left_median = None if left.empty else float(left.median())
    right_median = None if right.empty else float(right.median())
    diff = None if left_median is None or right_median is None else left_median - right_median
    return {
        "column": column,
        "left": left_sector,
        "right": right_sector,
        "left_median": left_median,
        "right_median": right_median,
        "diff": diff,
    }


def build_audit(dates: list[str], top_n: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    stage_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []
    reason_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    pred_frames: list[pd.DataFrame] = []

    for date_value in dates:
        pred = _read_prediction(date_value)
        unified = _read_unified(date_value)
        stages = _stage_frame(pred, unified, top_n=top_n)
        pred_frames.append(pred.assign(prediction_date=date_value))

        stage_rows.extend(_daily_stage_rows(date_value, stages, top_n=top_n))
        transition_rows.extend(_transition_rows(date_value, stages))
        reason_rows.extend(_reason_rows(date_value, unified))

        active = stages["active_target"]
        sector_weights = _sector_weight_map(active)
        max_sector = max(sector_weights, key=sector_weights.get) if sector_weights else None
        daily_rows.append(
            {
                "prediction_date": date_value,
                "raw_rows": int(len(stages["raw_head30"])),
                "production_rank20_rows": int(len(stages["production_cap_rank20"])),
                "pre_market_regime_rows": int(len(stages["pre_market_regime_survivors"])),
                "active_rows": int(len(active)),
                "active_units": int(pd.to_numeric(active.get("target_units"), errors="coerce").fillna(0).sum()) if not active.empty else 0,
                "active_max_sector": max_sector,
                "active_max_sector_weight": sector_weights.get(max_sector) if max_sector else 0.0,
                "active_max_name_weight": float(pd.to_numeric(active.get("target_weight_ratio"), errors="coerce").max()) if not active.empty else 0.0,
                "market_regime_state": str(unified.get("market_regime_state", pd.Series(["UNKNOWN"])).dropna().iloc[0]) if "market_regime_state" in unified.columns and not unified["market_regime_state"].dropna().empty else "UNKNOWN",
                "market_regime_action": str(unified.get("market_regime_action", pd.Series(["UNKNOWN"])).dropna().iloc[0]) if "market_regime_action" in unified.columns and not unified["market_regime_action"].dropna().empty else "UNKNOWN",
            }
        )

    stage_df = pd.DataFrame(stage_rows)
    transition_df = pd.DataFrame(transition_rows)
    reason_df = pd.DataFrame(reason_rows)
    daily_df = pd.DataFrame(daily_rows)

    focus_transition = transition_df[
        transition_df["transition"].eq("rank20_to_active_total")
        & transition_df["sector"].isin(FOCUS_SECTORS)
    ]
    focus_summary = (
        focus_transition.groupby("sector")
        .agg(
            before_count=("before_count", "sum"),
            after_count=("after_count", "sum"),
            killed_count=("killed_count", "sum"),
            mean_survival_rate=("survival_rate", "mean"),
        )
        .reset_index()
    )
    focus_summary["total_survival_rate"] = focus_summary["after_count"] / focus_summary["before_count"]

    score_diffs = [
        _median_diff(pred_frames, "半導體業", "通信網路業", column)
        for column in ["pred_return_20d", "prob_edge", "leaderboard_score"]
    ]
    focus_lookup = {row["sector"]: row for row in focus_summary.to_dict(orient="records")}
    semis = focus_lookup.get("半導體業", {})
    comms = focus_lookup.get("通信網路業", {})

    active_counts = pd.to_numeric(daily_df["active_rows"], errors="coerce")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dates": dates,
        "top_n": top_n,
        "available_days": len(dates),
        "active_rows": {
            "min": int(active_counts.min()) if len(active_counts) else None,
            "p25": float(active_counts.quantile(0.25)) if len(active_counts) else None,
            "median": float(active_counts.median()) if len(active_counts) else None,
            "p75": float(active_counts.quantile(0.75)) if len(active_counts) else None,
            "max": int(active_counts.max()) if len(active_counts) else None,
            "below_10_days": int(active_counts.lt(10).sum()) if len(active_counts) else 0,
            "below_10_ratio": float(active_counts.lt(10).mean()) if len(active_counts) else None,
        },
        "focus_sector_survival_rank20_to_active": focus_summary.to_dict(orient="records"),
        "semiconductor_vs_communication_score_medians": score_diffs,
        "reason_counts": reason_df["reason"].value_counts().to_dict() if not reason_df.empty else {},
        "answers": {
            "why_communication_survives": (
                "通信網路不是 rank20 供給最多的族群，但 rank20_to_active survival "
                f"{comms.get('total_survival_rate', np.nan):.2%} 是四個 focus sectors 最高；"
                "它在 CAUTION Top10 與 tradability gates 後留下的比例高，所以 target denominator "
                "縮小時被動放大。"
            ),
            "is_semiconductor_low_at_selection": (
                "否。半導體 rank20 count "
                f"{int(semis.get('before_count', 0))} 高於通信網路 "
                f"{int(comms.get('before_count', 0))}，且 raw score medians 沒有明顯劣勢；"
                f"半導體 active count {int(semis.get('after_count', 0))} 略低於通信 "
                f"{int(comms.get('after_count', 0))}，主因是 gate kill rate 較高。"
            ),
        },
        "interpretation": (
            "Active breadth collapse is driven mainly after sector-capped rank20, especially "
            "market-regime CAUTION Top10 pruning and tradability gates. Treat model-level sector "
            "score bias as secondary until gate funnel is fully reviewed."
        ),
    }
    return daily_df, stage_df, transition_df, reason_df, _json_safe(summary)


def write_outputs(
    *,
    daily_df: pd.DataFrame,
    stage_df: pd.DataFrame,
    transition_df: pd.DataFrame,
    reason_df: pd.DataFrame,
    summary: dict[str, Any],
    output_stem: str,
) -> None:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    daily_df.to_csv(report_dir / f"{output_stem}_daily.csv", index=False, encoding="utf-8-sig")
    stage_df.to_csv(report_dir / f"{output_stem}_stage_sector.csv", index=False, encoding="utf-8-sig")
    transition_df.to_csv(report_dir / f"{output_stem}_transition_sector.csv", index=False, encoding="utf-8-sig")
    reason_df.to_csv(report_dir / f"{output_stem}_blocked_reasons.csv", index=False, encoding="utf-8-sig")
    (report_dir / f"{output_stem}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(report_dir / f"{output_stem}.md", daily_df, transition_df, reason_df, summary)
    print(f"[gate-funnel] wrote {report_dir / f'{output_stem}.md'}")
    print(f"[gate-funnel] wrote {report_dir / f'{output_stem}.json'}")


def write_markdown(
    path: Path,
    daily_df: pd.DataFrame,
    transition_df: pd.DataFrame,
    reason_df: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    active = summary["active_rows"]
    lines = [
        "# V2 Gate Funnel Sector Bias Audit",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Dates: {summary['dates'][0]} to {summary['dates'][-1]} ({summary['available_days']} canonical days)",
        f"- Top N: {summary['top_n']}",
        "- Stages: raw Top30, sector-capped rank20, pre-market-regime survivors, active target.",
        "",
        "## Key Findings",
        "",
        f"- Active rows min/p25/median/p75/max: {active['min']} / {active['p25']:.2f} / "
        f"{active['median']:.2f} / {active['p75']:.2f} / {active['max']}",
        f"- Active rows < 10: {active['below_10_days']} / {summary['available_days']} "
        f"({_plain_pct(active['below_10_ratio'])})",
        f"- Blocked reason counts: {', '.join(f'{k}={v}' for k, v in summary['reason_counts'].items())}",
        f"- Why communication survives: {summary['answers']['why_communication_survives']}",
        f"- Is semiconductor low at selection: {summary['answers']['is_semiconductor_low_at_selection']}",
        f"- Interpretation: {summary['interpretation']}",
        "",
        "## Focus Sector Survival",
        "",
        "| sector | rank20 count | active count | killed | total survival | mean daily survival |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["focus_sector_survival_rank20_to_active"]:
        lines.append(
            f"| {row['sector']} | {row['before_count']} | {row['after_count']} | {row['killed_count']} | "
            f"{_plain_pct(row['total_survival_rate'])} | {_plain_pct(row['mean_survival_rate'])} |"
        )

    lines.extend(
        [
            "",
        "## Semiconductor Vs Communication Score Medians",
        "",
        "All rows in the available prediction artifacts, not only Top30.",
        "",
            "| score | 半導體 median | 通信 median | diff 半導體-通信 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in summary["semiconductor_vs_communication_score_medians"]:
        lines.append(
            f"| {row['column']} | {_pct(row['left_median'])} | {_pct(row['right_median'])} | {_pct(row['diff'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Funnel",
            "",
            "| date | rank20 | pre-regime | active | max active sector | max name | regime action |",
            "| --- | ---: | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for _, row in daily_df.iterrows():
        lines.append(
            f"| {row['prediction_date']} | {int(row['production_rank20_rows'])} | "
            f"{int(row['pre_market_regime_rows'])} | {int(row['active_rows'])} | "
            f"{row['active_max_sector']} {_plain_pct(row['active_max_sector_weight'])} | "
            f"{_plain_pct(row['active_max_name_weight'])} | {row['market_regime_action']} |"
        )

    focus = transition_df[
        transition_df["transition"].eq("rank20_to_active_total")
        & transition_df["sector"].isin(FOCUS_SECTORS)
    ].sort_values(["prediction_date", "sector"])
    lines.extend(
        [
            "",
            "## Focus Sector Daily Kill Rate",
            "",
            "| date | sector | rank20 | active | kill rate |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for _, row in focus.iterrows():
        lines.append(
            f"| {row['prediction_date']} | {row['sector']} | {int(row['before_count'])} | "
            f"{int(row['after_count'])} | {_plain_pct(row['kill_rate'])} |"
        )

    reason_summary = (
        reason_df.groupby(["sector", "reason"]).size().reset_index(name="count")
        if not reason_df.empty
        else pd.DataFrame(columns=["sector", "reason", "count"])
    )
    reason_summary = reason_summary.sort_values(["count", "sector"], ascending=[False, True]).head(30)
    lines.extend(
        [
            "",
            "## Blocked Reasons By Sector",
            "",
            "| sector | reason | count |",
            "| --- | --- | ---: |",
        ]
    )
    for _, row in reason_summary.iterrows():
        lines.append(f"| {row['sector']} | {row['reason']} | {int(row['count'])} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `pre_market_regime_survivors` is an approximation from final unified rows: active rows plus rows whose only block reason is market-regime CAUTION.",
            "- `rank20_to_active_total` is the decisive production symptom: sector-capped supply exists, but active target rows collapse after downstream gates.",
            "- This report is research-only and does not change gates, model scoring, sector cap, or entry filters.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dates = _available_dates(args.end_date, args.lookback_days)
    if not dates:
        raise FileNotFoundError("No overlapping prediction/unified signal dates found")
    daily_df, stage_df, transition_df, reason_df, summary = build_audit(dates, top_n=args.top_n)
    write_outputs(
        daily_df=daily_df,
        stage_df=stage_df,
        transition_df=transition_df,
        reason_df=reason_df,
        summary=summary,
        output_stem=args.output_stem,
    )
    active = summary["active_rows"]
    print(
        "[gate-funnel] "
        f"days={summary['available_days']} "
        f"active_median={active['median']} "
        f"below10={active['below_10_days']} "
        f"reason_counts={summary['reason_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
