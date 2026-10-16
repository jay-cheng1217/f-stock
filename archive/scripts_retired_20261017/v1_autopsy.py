from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR

PREDICTION_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
DEFAULT_OUTPUT_JSON = os.path.join(REPORT_DIR, "v1_autopsy_latest.json")
DEFAULT_OUTPUT_MD = os.path.join(REPORT_DIR, "v1_autopsy_latest.md")
FRICTION_COST = 0.004
MAX_OBSERVED_TRADING_DAYS = 10


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        if pd.isna(value):
            return None
        num = float(value)
    except Exception:
        return None
    if not np.isfinite(num):
        return None
    return round(num, digits)


def _read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")


def _prediction_paths(start_date: str, end_date: str) -> list[str]:
    paths: list[str] = []
    for path in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv"))):
        match = PREDICTION_RE.match(os.path.basename(path))
        if not match:
            continue
        date = match.group(1)
        if start_date <= date <= end_date:
            paths.append(path)
    return paths


def _series_stats(series: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "mean": None,
            "median": None,
            "q05": None,
            "q25": None,
            "q75": None,
            "q95": None,
            "min": None,
            "max": None,
        }
    return {
        "mean": _round(values.mean()),
        "median": _round(values.median()),
        "q05": _round(values.quantile(0.05)),
        "q25": _round(values.quantile(0.25)),
        "q75": _round(values.quantile(0.75)),
        "q95": _round(values.quantile(0.95)),
        "min": _round(values.min()),
        "max": _round(values.max()),
    }


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}


def _rates(series: pd.Series) -> dict[str, float]:
    total = len(series)
    if total == 0:
        return {}
    return {str(k): round(float(v) / total, 6) for k, v in series.value_counts(dropna=False).items()}


def _first_value(df: pd.DataFrame, column: str) -> Any:
    if column not in df.columns or df.empty:
        return None
    return df[column].iloc[0]


def _buy_mask(df: pd.DataFrame) -> pd.Series:
    if "recommendation" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["recommendation"].fillna("").astype(str).str.contains("買進", regex=False)


def _top_v2(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if "pred_return_20d" not in df.columns:
        return df.head(0).copy()
    return df.assign(
        pred_return_20d_numeric=pd.to_numeric(df["pred_return_20d"], errors="coerce")
    ).sort_values("pred_return_20d_numeric", ascending=False).head(n).drop(columns=["pred_return_20d_numeric"])


def _load_history(ticker: str) -> pd.DataFrame:
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    try:
        df = pd.read_csv(path, usecols=["Date", "Open", "High", "Low", "Close"])
    except Exception:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)


def _observe_position(ticker: str, prediction_date: str) -> dict[str, Any]:
    history = _load_history(ticker)
    if history.empty:
        return {
            "entry_date": None,
            "settle_date": None,
            "observed_trading_days": 0,
            "partial_return": None,
            "max_drawdown": None,
        }

    pred_ts = pd.Timestamp(prediction_date)
    future = history[history["Date"] > pred_ts].head(MAX_OBSERVED_TRADING_DAYS)
    if future.empty:
        return {
            "entry_date": None,
            "settle_date": None,
            "observed_trading_days": 0,
            "partial_return": None,
            "max_drawdown": None,
        }

    entry_open = float(future.iloc[0]["Open"])
    settle_close = float(future.iloc[-1]["Close"])
    low_col = "Low" if "Low" in future.columns else "Close"
    worst_low = float(pd.to_numeric(future[low_col], errors="coerce").min())
    return {
        "entry_date": future.iloc[0]["Date"].date().isoformat(),
        "settle_date": future.iloc[-1]["Date"].date().isoformat(),
        "observed_trading_days": int(len(future)),
        "partial_return": _round(settle_close / entry_open - 1.0 - FRICTION_COST),
        "max_drawdown": _round(worst_low / entry_open - 1.0),
    }


def _load_replay_summary() -> dict[str, dict[str, Any]]:
    path = os.path.join(REPORT_DIR, "daily_replay_monitor_latest.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return {
        str(day.get("prediction_date")): {
            "selected_count": day.get("selected_count"),
            "breathing_ratio": day.get("breathing_ratio"),
            "avg_partial_return": day.get("avg_partial_return"),
            "worst_max_drawdown": day.get("worst_max_drawdown"),
            "alerts": day.get("alerts", []),
        }
        for day in data.get("days", [])
        if day.get("prediction_date")
    }


def _summarize_day(path: str, top_n: int, replay_by_date: dict[str, dict[str, Any]]) -> dict[str, Any]:
    df = _read_csv(path)
    prediction_date = str(df["date"].iloc[0]) if "date" in df.columns and not df.empty else PREDICTION_RE.match(os.path.basename(path)).group(1)
    top = _top_v2(df, top_n)
    full_signal = df["signal"] if "signal" in df.columns else pd.Series(dtype=str)
    top_signal = top["signal"] if "signal" in top.columns else pd.Series(dtype=str)
    buy = _buy_mask(df)

    summary = {
        "prediction_date": prediction_date,
        "prediction_file": path,
        "rows": int(len(df)),
        "base_model_file": _first_value(df, "base_model_file"),
        "base_model_trained_at": _first_value(df, "base_model_trained_at"),
        "recommendation_counts": _counts(df["recommendation"]) if "recommendation" in df.columns else {},
        "buy_recommendation_count": int(buy.sum()),
        "full_signal_counts": _counts(full_signal),
        "full_signal_rates": _rates(full_signal),
        "top_v2_signal_counts": _counts(top_signal),
        "top_v2_signal_rates": _rates(top_signal),
        "prob_edge": _series_stats(df.get("prob_edge", pd.Series(dtype=float))),
        "top_v2_prob_edge": _series_stats(top.get("prob_edge", pd.Series(dtype=float))),
        "top_v2_pred_return_20d": _series_stats(top.get("pred_return_20d", pd.Series(dtype=float))),
        "top_v2_down_count": int((top_signal == "DOWN").sum()) if not top.empty else 0,
        "top_v2_down_rate": _round((top_signal == "DOWN").mean()) if not top.empty else None,
        "top_v2_v2_only_bull_count": int(pd.to_numeric(top.get("v2_only_bull", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if "v2_only_bull" in top.columns else None,
        "top_v2_dual_track_buy_count": int(pd.to_numeric(top.get("dual_track_buy", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if "dual_track_buy" in top.columns else None,
        "top_v2_v1_buy_ok_count": int(pd.to_numeric(top.get("v1_buy_ok", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if "v1_buy_ok" in top.columns else None,
        "replay": replay_by_date.get(prediction_date, {}),
    }
    return summary


def _wrongly_killed(df: pd.DataFrame, prediction_date: str, top_n: int) -> list[dict[str, Any]]:
    top = _top_v2(df, top_n).copy()
    if top.empty or "signal" not in top.columns:
        return []
    killed = top[top["signal"].eq("DOWN")].copy()
    rows: list[dict[str, Any]] = []
    for _, row in killed.iterrows():
        ticker = str(row.get("ticker"))
        obs = _observe_position(ticker, prediction_date)
        item = {
            "prediction_date": prediction_date,
            "ticker": ticker,
            "signal": row.get("signal"),
            "recommendation": row.get("recommendation"),
            "pred_return_20d": _round(row.get("pred_return_20d")),
            "prob_edge": _round(row.get("prob_edge")),
            "up_prob": _round(row.get("up_prob")),
            "down_prob": _round(row.get("down_prob")),
            "price_vs_ma20": _round(row.get("price_vs_ma20")),
            "risk_tags": str(row.get("risk_tags") or ""),
            **obs,
        }
        rows.append(item)
    return rows


def _format_pct(value: Any, digits: int = 1) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value) * 100:+.{digits}f}%"
    except Exception:
        return "-"


def _format_num(value: Any, digits: int = 3) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _model_group(base_model_file: Any) -> str:
    text = str(base_model_file or "")
    if "20260404" in text:
        return "stable_20260404"
    if "20260409" in text:
        return "incident_20260409"
    return text or "unknown"


def _load_model_meta_summary(base_model_file: Any) -> dict[str, Any]:
    text = str(base_model_file or "")
    if not text:
        return {}
    meta_name = text.replace(".txt", "_meta.json")
    meta_path = os.path.join(MODEL_DIR, meta_name)
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return {}
    feature_cols = meta.get("feature_columns")
    return {
        "meta_file": meta_path,
        "trained_at": meta.get("trained_at"),
        "cross_sectional_zscore": bool(meta.get("cross_sectional_zscore")),
        "feature_count": len(feature_cols) if isinstance(feature_cols, list) else meta.get("feature_count"),
        "avg_precision_up": _round(meta.get("avg_precision_up")),
        "model_version": meta.get("model_version"),
    }


def generate_report(
    start_date: str = "2026-04-02",
    end_date: str = "2026-04-17",
    top_n: int = 30,
) -> dict[str, Any]:
    replay_by_date = _load_replay_summary()
    paths = _prediction_paths(start_date, end_date)
    days: list[dict[str, Any]] = []
    killed_rows: list[dict[str, Any]] = []

    for path in paths:
        df = _read_csv(path)
        prediction_date = str(df["date"].iloc[0]) if "date" in df.columns and not df.empty else PREDICTION_RE.match(os.path.basename(path)).group(1)
        days.append(_summarize_day(path, top_n=top_n, replay_by_date=replay_by_date))
        rows = _wrongly_killed(df, prediction_date=prediction_date, top_n=top_n)
        base_model_file = _first_value(df, "base_model_file")
        for row in rows:
            row["base_model_file"] = base_model_file
            row["model_group"] = _model_group(base_model_file)
        killed_rows.extend(rows)

    days_df = pd.DataFrame(days)
    if not days_df.empty:
        days_df["model_group"] = days_df["base_model_file"].map(_model_group)

    group_summary: dict[str, Any] = {}
    for group, subset in days_df.groupby("model_group", dropna=False):
        group_summary[str(group)] = {
            "days": int(len(subset)),
            "avg_full_down_rate": _round(subset["full_signal_rates"].map(lambda x: x.get("DOWN") if isinstance(x, dict) else np.nan).mean()),
            "avg_top_v2_down_rate": _round(pd.to_numeric(subset["top_v2_down_rate"], errors="coerce").mean()),
            "avg_prob_edge_mean": _round(subset["prob_edge"].map(lambda x: x.get("mean") if isinstance(x, dict) else np.nan).mean()),
            "avg_top_v2_prob_edge_mean": _round(subset["top_v2_prob_edge"].map(lambda x: x.get("mean") if isinstance(x, dict) else np.nan).mean()),
            "avg_buy_recommendation_count": _round(pd.to_numeric(subset["buy_recommendation_count"], errors="coerce").mean(), 2),
            "avg_replay_breathing_ratio": _round(subset["replay"].map(lambda x: x.get("breathing_ratio") if isinstance(x, dict) else np.nan).mean()),
        }

    killed_df = pd.DataFrame(killed_rows)
    observed_killed = killed_df.dropna(subset=["partial_return"]).copy() if not killed_df.empty else killed_df
    incident_observed_killed = (
        observed_killed[observed_killed["model_group"].eq("incident_20260409")].copy()
        if not observed_killed.empty and "model_group" in observed_killed.columns
        else observed_killed
    )
    top_killed_winners = (
        incident_observed_killed.sort_values("partial_return", ascending=False)
        .head(20)
        .replace({np.nan: None})
        .to_dict(orient="records")
        if not incident_observed_killed.empty
        else []
    )

    base_models = sorted({str(day.get("base_model_file")) for day in days if day.get("base_model_file")})

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": {
            "start_date": start_date,
            "end_date": end_date,
            "top_n": top_n,
            "baseline_note": "Existing files support production drift analysis. A clean same-day old-model vs new-model comparison requires rerunning both V1 models on the same snapshot.",
        },
        "group_summary": group_summary,
        "model_metadata": {
            base_model: _load_model_meta_summary(base_model)
            for base_model in base_models
        },
        "days": days,
        "wrongly_killed_summary": {
            "scope": "incident_20260409_top_v2_down_only",
            "all_rows": int(len(killed_df)),
            "all_observed_rows": int(len(observed_killed)),
            "rows": int(len(incident_observed_killed)),
            "observed_rows": int(len(incident_observed_killed)),
            "avg_partial_return": _round(incident_observed_killed["partial_return"].mean()) if not incident_observed_killed.empty else None,
            "win_rate": _round((incident_observed_killed["partial_return"] > 0).mean()) if not incident_observed_killed.empty else None,
            "worst_max_drawdown": _round(incident_observed_killed["max_drawdown"].min()) if not incident_observed_killed.empty else None,
            "top_winners": top_killed_winners,
        },
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# V1 Production Incident Autopsy")
    lines.append("")
    lines.append(f"- Generated: `{report['generated_at']}`")
    scope = report["scope"]
    lines.append(f"- Scope: `{scope['start_date']}` to `{scope['end_date']}`, V2 Top `{scope['top_n']}`")
    lines.append(f"- Limitation: {scope['baseline_note']}")
    lines.append("")

    if report.get("model_metadata"):
        lines.append("## Model Metadata")
        lines.append("")
        lines.append("| Model | Trained At | Z-score | Features | Avg Precision UP |")
        lines.append("|---|---|---:|---:|---:|")
        for model_name, meta in report["model_metadata"].items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        model_name,
                        str(meta.get("trained_at", "-")),
                        str(meta.get("cross_sectional_zscore", "-")),
                        str(meta.get("feature_count", "-")),
                        _format_num(meta.get("avg_precision_up")),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.append("## Model Group Summary")
    lines.append("")
    lines.append("| Group | Days | Full DOWN | Top30 DOWN | Prob Edge Mean | Top30 Prob Edge | Buy Count | Replay Breathing |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for group, s in report["group_summary"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    group,
                    str(s.get("days", "-")),
                    _format_pct(s.get("avg_full_down_rate")),
                    _format_pct(s.get("avg_top_v2_down_rate")),
                    _format_num(s.get("avg_prob_edge_mean")),
                    _format_num(s.get("avg_top_v2_prob_edge_mean")),
                    _format_num(s.get("avg_buy_recommendation_count"), 1),
                    _format_pct(s.get("avg_replay_breathing_ratio")),
                ]
            )
            + " |"
        )
    lines.append("")

    lines.append("## Daily Drift")
    lines.append("")
    lines.append("| Date | Base Model | Full Signal | Top30 Signal | Prob Edge Mean | Top30 Prob Edge | Buy Count | Replay Selected | Alerts |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---|")
    for day in report["days"]:
        replay = day.get("replay", {}) or {}
        alerts = ", ".join(replay.get("alerts", []) or [])
        selected = replay.get("selected_count")
        top_n = report["scope"]["top_n"]
        selected_text = f"{selected}/{top_n}" if selected is not None else "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(day.get("prediction_date", "-")),
                    str(day.get("base_model_file", "-")),
                    json.dumps(day.get("full_signal_counts", {}), ensure_ascii=False),
                    json.dumps(day.get("top_v2_signal_counts", {}), ensure_ascii=False),
                    _format_num((day.get("prob_edge") or {}).get("mean")),
                    _format_num((day.get("top_v2_prob_edge") or {}).get("mean")),
                    str(day.get("buy_recommendation_count", "-")),
                    selected_text,
                    alerts or "-",
                ]
            )
            + " |"
        )
    lines.append("")

    killed = report["wrongly_killed_summary"]
    lines.append("## Top30 DOWN Victims")
    lines.append("")
    lines.append(f"- Scope: `{killed.get('scope')}`")
    lines.append(f"- Incident observed Top30 DOWN rows: `{killed.get('observed_rows')}`")
    lines.append(f"- All observed Top30 DOWN rows: `{killed.get('all_observed_rows')}` / `{killed.get('all_rows')}`")
    lines.append(f"- Avg partial return: `{_format_pct(killed.get('avg_partial_return'), 2)}`")
    lines.append(f"- Win rate: `{_format_pct(killed.get('win_rate'), 1)}`")
    lines.append(f"- Worst MDD: `{_format_pct(killed.get('worst_max_drawdown'), 2)}`")
    lines.append("")
    lines.append("| Date | Ticker | Pred20D | Prob Edge | Price vs MA20 | Partial Return | MDD | Recommendation |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for row in killed.get("top_winners", [])[:15]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("prediction_date", "-")),
                    str(row.get("ticker", "-")),
                    _format_pct(row.get("pred_return_20d"), 2),
                    _format_num(row.get("prob_edge")),
                    _format_pct(row.get("price_vs_ma20"), 1),
                    _format_pct(row.get("partial_return"), 2),
                    _format_pct(row.get("max_drawdown"), 2),
                    str(row.get("recommendation", "-")),
                ]
            )
            + " |"
        )
    lines.append("")

    lines.append("## Readout")
    lines.append("")
    lines.append("- The incident group is expected to show a much higher DOWN distribution and lower prob_edge than the stable group.")
    lines.append("- If replay selected_count collapses on the same dates, the production symptom is guardrail breathing failure rather than pure V2 alpha failure.")
    lines.append("- This report still needs a same-snapshot rerun of `lgbm_20260404_131437.txt` and `lgbm_20260409_125845.txt` to prove the exact model-version delta.")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_json: str, output_md: str) -> None:
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))


def main() -> None:
    parser = argparse.ArgumentParser(description="Autopsy V1 production signal drift around the 2026-04 incident.")
    parser.add_argument("--start-date", default="2026-04-02")
    parser.add_argument("--end-date", default="2026-04-17")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    report = generate_report(start_date=args.start_date, end_date=args.end_date, top_n=args.top_n)
    write_report(report, args.output_json, args.output_md)

    _safe_print(f"Wrote JSON: {args.output_json}")
    _safe_print(f"Wrote Markdown: {args.output_md}")
    for group, summary in report.get("group_summary", {}).items():
        _safe_print(
            f"{group}: days={summary.get('days')} "
            f"full_down={_format_pct(summary.get('avg_full_down_rate'))} "
            f"top30_down={_format_pct(summary.get('avg_top_v2_down_rate'))} "
            f"prob_edge={_format_num(summary.get('avg_prob_edge_mean'))} "
            f"breathing={_format_pct(summary.get('avg_replay_breathing_ratio'))}"
        )
    killed = report.get("wrongly_killed_summary", {})
    _safe_print(
        f"Top30 DOWN victims: observed={killed.get('observed_rows')} "
        f"avg_partial={_format_pct(killed.get('avg_partial_return'), 2)} "
        f"win_rate={_format_pct(killed.get('win_rate'), 1)}"
    )


if __name__ == "__main__":
    main()
