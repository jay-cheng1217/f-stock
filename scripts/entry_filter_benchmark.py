"""Offline benchmark report for unified entry-filter decisions.

This report answers a narrow SA/RD question before changing production gates:
did the ML-selected entry basket beat simple, inspectable alternatives?

It is intentionally read-only. It does not modify model artifacts, scheduler
state, paper portfolio databases, or production gate rules.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402

DEFAULT_DB_PATH = BASE_DIR / "stock.duckdb"
DEFAULT_OUTPUT_PREFIX = "entry_filter_benchmark_latest"
SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")


@dataclass(frozen=True)
class BasketMetric:
    basket: str
    names: int
    avg_return: float | None
    median_return: float | None
    win_rate: float | None
    avg_pred_return_20d: float | None
    avg_prob_edge: float | None
    avg_votes: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "basket": self.basket,
            "names": self.names,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "win_rate": self.win_rate,
            "avg_pred_return_20d": self.avg_pred_return_20d,
            "avg_prob_edge": self.avg_prob_edge,
            "avg_votes": self.avg_votes,
        }


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _fmt_float(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def _coerce_date(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value).normalize()
    if pd.isna(result):
        raise ValueError(f"Invalid date: {value}")
    return result


def _signal_paths_between(start_date: str, end_date: str, model_dir: str | Path = MODEL_DIR) -> list[Path]:
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    paths: list[tuple[pd.Timestamp, Path]] = []
    for path in Path(model_dir).glob("unified_signals_*.csv"):
        match = SIGNAL_RE.match(path.name)
        if not match:
            continue
        signal_date = _coerce_date(match.group(1))
        if start <= signal_date <= end:
            paths.append((signal_date, path))
    return [path for _, path in sorted(paths)]


def _latest_price_date(db_path: str | Path = DEFAULT_DB_PATH) -> str:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute("SELECT MAX(Date)::DATE FROM daily_k").fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        raise RuntimeError("daily_k has no price dates")
    return str(row[0])


def _trading_dates_between(
    start_date: str,
    end_date: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        dates = con.execute(
            """
            SELECT DISTINCT Date::DATE AS trading_date
            FROM daily_k
            WHERE Date BETWEEN ? AND ?
            ORDER BY trading_date
            """,
            [start_date, end_date],
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in dates]


def _next_trading_date(signal_date: str, trading_dates: list[str]) -> str | None:
    for trading_date in trading_dates:
        if trading_date > signal_date:
            return trading_date
    return None


def _read_signal(path: Path) -> pd.DataFrame:
    match = SIGNAL_RE.match(path.name)
    if not match:
        raise ValueError(f"Not a unified signal file: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if df.empty:
        return df
    df["ticker"] = df["ticker"].astype(str).str.strip().str.zfill(4)
    df["signal_date"] = match.group(1)
    return df


def _numeric_series(df: pd.DataFrame, columns: list[str], default: float = np.nan) -> pd.Series:
    for column in columns:
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def _text_series(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    for column in columns:
        if column in df.columns:
            return df[column].fillna("").astype(str)
    return pd.Series("", index=df.index, dtype="object")


def add_simple_benchmark_votes(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate candidate rows with transparent, non-production benchmark votes."""

    out = df.copy()
    price_vs_ma20 = _numeric_series(out, ["price_vs_ma20_20d", "price_vs_ma20"])
    price_vs_ma60 = _numeric_series(out, ["price_vs_ma60_20d", "price_vs_ma60"])
    price_vs_ma5 = _numeric_series(out, ["price_vs_ma5_20d", "price_vs_ma5"])
    beta_60 = _numeric_series(out, ["beta_60_20d", "beta_60"])
    gap_pct = _numeric_series(out, ["gap_pct_20d", "gap_pct"])
    foreign_flow = _numeric_series(out, ["foreign_cumsum_20d_raw_20d", "foreign_cumsum_20d_raw"])
    penalty_reason = _text_series(out, ["penalty_overlay_reason"])
    prob_edge = _numeric_series(out, ["prob_edge"])

    out["bench_trend_vote"] = (price_vs_ma20 > 0) & (price_vs_ma60 > 0) & (price_vs_ma5 >= -0.02)
    out["bench_flow_vote"] = (foreign_flow > 0) | ~penalty_reason.str.contains("foreign_selling", na=False)
    out["bench_gap_vote"] = (gap_pct <= 0.03) & ~penalty_reason.str.contains("gap_risk", na=False)
    out["bench_quality_vote"] = ~penalty_reason.str.contains("quality", na=False)
    out["bench_risk_vote"] = beta_60.fillna(0) <= 1.25
    out["bench_edge_vote"] = prob_edge.fillna(-1.0) >= -0.10

    vote_cols = [
        "bench_trend_vote",
        "bench_flow_vote",
        "bench_gap_vote",
        "bench_quality_vote",
        "bench_risk_vote",
        "bench_edge_vote",
    ]
    out["benchmark_votes"] = out[vote_cols].sum(axis=1).astype(int)
    out["benchmark_consensus"] = np.select(
        [
            out["benchmark_votes"] >= 5,
            out["benchmark_votes"].between(3, 4),
        ],
        ["strong", "watch"],
        default="reject",
    )
    return out


def _load_prices_for_rows(
    rows: pd.DataFrame,
    *,
    mark_date: str,
    db_path: str | Path,
) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()

    tickers = sorted(rows["ticker"].dropna().astype(str).unique())
    dates = sorted(set(rows["entry_date"].dropna().astype(str).tolist() + [mark_date]))
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        prices = con.execute(
            """
            SELECT Ticker AS ticker, Date::DATE AS date, Open, High, Low, Close
            FROM daily_k
            WHERE CAST(Date AS VARCHAR) IN (SELECT unnest(?))
              AND Ticker IN (SELECT unnest(?))
            """,
            [dates, tickers],
        ).fetchdf()
    finally:
        con.close()

    prices["ticker"] = prices["ticker"].astype(str).str.zfill(4)
    prices["date"] = prices["date"].astype(str)
    entry_prices = prices[["ticker", "date", "Open"]].rename(
        columns={"date": "entry_date", "Open": "entry_open"}
    )
    mark_prices = (
        prices[prices["date"] == mark_date][["ticker", "Close", "High", "Low"]]
        .rename(columns={"Close": "mark_close", "High": "mark_high", "Low": "mark_low"})
    )
    out = rows.merge(entry_prices, on=["ticker", "entry_date"], how="left")
    out = out.merge(mark_prices, on="ticker", how="left")
    out["return_to_mark"] = pd.to_numeric(out["mark_close"], errors="coerce") / pd.to_numeric(
        out["entry_open"], errors="coerce"
    ) - 1.0
    return out


def build_candidate_frame(
    start_date: str,
    end_date: str,
    *,
    mark_date: str | None = None,
    model_dir: str | Path = MODEL_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    mark = mark_date or _latest_price_date(db_path)
    trading_dates = _trading_dates_between(start_date, mark, db_path=db_path)
    frames: list[pd.DataFrame] = []
    for path in _signal_paths_between(start_date, end_date, model_dir):
        frame = _read_signal(path)
        if frame.empty:
            continue
        signal_date = frame["signal_date"].iloc[0]
        entry_date = _next_trading_date(signal_date, trading_dates)
        if entry_date is None or entry_date > mark:
            continue
        frame["entry_date"] = entry_date
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    candidates = pd.concat(frames, ignore_index=True).copy()
    candidates["selected_by_ml"] = pd.to_numeric(candidates.get("target_units", 0), errors="coerce").fillna(0) > 0
    candidates = add_simple_benchmark_votes(candidates)
    candidates = _load_prices_for_rows(candidates, mark_date=mark, db_path=db_path)
    candidates["mark_date"] = mark
    candidates["has_return"] = candidates["return_to_mark"].notna()
    return candidates


def _basket_metric(name: str, df: pd.DataFrame) -> BasketMetric:
    clean = df[pd.to_numeric(df["return_to_mark"], errors="coerce").notna()].copy()
    returns = pd.to_numeric(clean["return_to_mark"], errors="coerce")
    pred = _numeric_series(clean, ["pred_return_20d"])
    edge = _numeric_series(clean, ["prob_edge"])
    votes = _numeric_series(clean, ["benchmark_votes"])
    if clean.empty:
        return BasketMetric(name, 0, None, None, None, None, None, None)
    return BasketMetric(
        basket=name,
        names=int(clean.shape[0]),
        avg_return=float(returns.mean()),
        median_return=float(returns.median()),
        win_rate=float((returns > 0).mean()),
        avg_pred_return_20d=float(pred.mean()) if pred.notna().any() else None,
        avg_prob_edge=float(edge.mean()) if edge.notna().any() else None,
        avg_votes=float(votes.mean()) if votes.notna().any() else None,
    )


def _selected_repair_policy_masks(candidates: pd.DataFrame) -> dict[str, pd.Series]:
    """Return ex-ante shadow repair policies for ML-selected rows only."""

    selected = candidates["selected_by_ml"].fillna(False).astype(bool)
    reason = _text_series(candidates, ["penalty_overlay_reason"])
    rank = _numeric_series(candidates, ["rank_20d"])
    edge = _numeric_series(candidates, ["prob_edge"])
    votes = _numeric_series(candidates, ["benchmark_votes"])
    foreign_gap_negative_edge = (
        reason.str.contains("foreign_selling", na=False)
        & reason.str.contains("gap_risk", na=False)
        & (edge < -0.10)
    )
    weak_late_rank = (rank > 10) & (edge < -0.10)

    return {
        "shadow_guard_prob_edge_or_strong_consensus": selected & ((edge >= -0.10) | (votes >= 5)),
        "shadow_guard_votes_ge4": selected & (votes >= 4),
        "shadow_guard_prob_edge_ge_neg10": selected & (edge >= -0.10),
        "shadow_guard_edge15_and_votes4": selected & (edge >= -0.15) & (votes >= 4),
        "shadow_guard_block_foreign_gap_neg_edge": selected & ~foreign_gap_negative_edge,
        "shadow_guard_block_late_rank_neg_edge": selected & ~weak_late_rank,
    }


def _repair_policy_metrics(candidates: pd.DataFrame, current_metric: BasketMetric) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    selected_count = int(candidates["selected_by_ml"].fillna(False).astype(bool).sum())
    current_avg = current_metric.avg_return
    for name, mask in _selected_repair_policy_masks(candidates).items():
        metric = _basket_metric(name, candidates[mask])
        avg_return = metric.avg_return
        policies.append(
            {
                **metric.to_dict(),
                "kept_selected": metric.names,
                "dropped_selected": max(selected_count - metric.names, 0),
                "avg_return_improvement_vs_current": (
                    avg_return - current_avg if avg_return is not None and current_avg is not None else None
                ),
            }
        )
    return policies


def _twii_open_to_mark_by_entry(candidates: pd.DataFrame, *, db_path: str | Path) -> dict[str, float]:
    if candidates.empty:
        return {}
    mark_date = str(candidates["mark_date"].dropna().iloc[0])
    entry_dates = sorted(candidates["entry_date"].dropna().astype(str).unique())
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        index = con.execute(
            """
            SELECT Date::DATE AS date, Open, Close
            FROM indices
            WHERE Index_Name = 'TWII'
              AND CAST(Date AS VARCHAR) IN (SELECT unnest(?))
            """,
            [sorted(set(entry_dates + [mark_date]))],
        ).fetchdf()
    except Exception:
        return {}
    finally:
        con.close()
    if index.empty:
        return {}
    index["date"] = index["date"].astype(str)
    mark_rows = index[index["date"] == mark_date]
    if mark_rows.empty:
        return {}
    mark_close = float(mark_rows["Close"].iloc[0])
    result: dict[str, float] = {}
    for _, row in index[index["date"].isin(entry_dates)].iterrows():
        if pd.notna(row["Open"]) and float(row["Open"]) != 0:
            result[str(row["date"])] = mark_close / float(row["Open"]) - 1.0
    return result


def compute_report(
    candidates: pd.DataFrame,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if candidates.empty:
        return {
            "status": "no_data",
            "metrics": [],
            "daily": [],
            "failures": ["no candidate rows"],
        }

    selected = candidates[candidates["selected_by_ml"]]
    blocked = candidates[~candidates["selected_by_ml"]]
    strong = candidates[candidates["benchmark_consensus"] == "strong"]
    watch = candidates[candidates["benchmark_consensus"].isin(["strong", "watch"])]
    rejected = candidates[candidates["benchmark_consensus"] == "reject"]
    selected_strong = selected[selected["benchmark_consensus"] == "strong"]
    selected_reject = selected[selected["benchmark_consensus"] == "reject"]

    metrics = [
        _basket_metric("ml_selected", selected),
        _basket_metric("ml_blocked_or_none", blocked),
        _basket_metric("simple_consensus_strong", strong),
        _basket_metric("simple_consensus_watch_or_strong", watch),
        _basket_metric("simple_consensus_reject", rejected),
        _basket_metric("ml_selected_and_consensus_strong", selected_strong),
        _basket_metric("ml_selected_but_consensus_reject", selected_reject),
    ]
    metric_map = {metric.basket: metric for metric in metrics}

    twii_by_entry = _twii_open_to_mark_by_entry(candidates, db_path=db_path)
    selected_daily: list[dict[str, Any]] = []
    for (signal_date, entry_date), group in selected.groupby(["signal_date", "entry_date"], dropna=False):
        metric = _basket_metric("selected_day", group)
        twii_return = twii_by_entry.get(str(entry_date))
        selected_daily.append(
            {
                "signal_date": str(signal_date),
                "entry_date": str(entry_date),
                **metric.to_dict(),
                "twii_open_to_mark": twii_return,
                "alpha_vs_twii": (
                    metric.avg_return - twii_return
                    if metric.avg_return is not None and twii_return is not None
                    else None
                ),
            }
        )

    ml_selected = metric_map["ml_selected"]
    blocked_metric = metric_map["ml_blocked_or_none"]
    strong_metric = metric_map["simple_consensus_strong"]
    reject_metric = metric_map["ml_selected_but_consensus_reject"]
    repair_policies = _repair_policy_metrics(candidates, ml_selected)
    proposed_policy = next(
        (
            policy
            for policy in repair_policies
            if policy["basket"] == "shadow_guard_prob_edge_or_strong_consensus"
        ),
        None,
    )
    selected_minus_blocked = (
        ml_selected.avg_return - blocked_metric.avg_return
        if ml_selected.avg_return is not None and blocked_metric.avg_return is not None
        else None
    )
    selected_minus_strong = (
        ml_selected.avg_return - strong_metric.avg_return
        if ml_selected.avg_return is not None and strong_metric.avg_return is not None
        else None
    )

    failures: list[str] = []
    warnings: list[str] = []
    if selected_minus_blocked is not None and selected_minus_blocked <= 0:
        failures.append("ml_selected_did_not_beat_blocked_counterfactual")
    if selected_minus_strong is not None and selected_minus_strong <= 0:
        failures.append("ml_selected_did_not_beat_simple_consensus_strong")
    if ml_selected.win_rate is not None and ml_selected.win_rate < 0.45:
        failures.append("ml_selected_win_rate_below_45pct")
    if reject_metric.names > 0:
        warnings.append("ml_selected_contains_consensus_reject_names")
    if proposed_policy and proposed_policy["avg_return_improvement_vs_current"] is not None:
        if proposed_policy["avg_return_improvement_vs_current"] <= 0:
            warnings.append("proposed_shadow_guard_did_not_improve_current_window")

    status = "pass"
    if failures:
        status = "fail"
    elif warnings:
        status = "warn"

    return {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": str(candidates["signal_date"].min()),
        "end_date": str(candidates["signal_date"].max()),
        "mark_date": str(candidates["mark_date"].dropna().iloc[0]),
        "candidate_rows": int(candidates.shape[0]),
        "priced_rows": int(candidates["has_return"].sum()),
        "selected_minus_blocked": selected_minus_blocked,
        "selected_minus_simple_consensus_strong": selected_minus_strong,
        "failures": failures,
        "warnings": warnings,
        "metrics": [metric.to_dict() for metric in metrics],
        "repair_policies": repair_policies,
        "proposed_shadow_guard": proposed_policy,
        "daily": selected_daily,
        "twii_open_to_mark_by_entry": twii_by_entry,
    }


def _render_markdown(report: dict[str, Any], candidates: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# Entry Filter Benchmark")
    lines.append("")
    lines.append(f"- Generated: `{report.get('generated_at', '-')}`")
    lines.append(f"- Signal window: `{report.get('start_date', '-')}` to `{report.get('end_date', '-')}`")
    lines.append(f"- Mark date: `{report.get('mark_date', '-')}`")
    lines.append(f"- Status: `{report.get('status', '-')}`")
    lines.append(f"- Candidate rows / priced rows: `{report.get('candidate_rows', 0)}` / `{report.get('priced_rows', 0)}`")
    lines.append("")
    lines.append("> Read-only offline benchmark. No production gate, model, scheduler, DB schema, or paper-book mutation.")
    lines.append("")

    lines.append("## Basket Metrics")
    lines.append("")
    lines.append("| Basket | Names | Avg | Median | Win | Avg 20D Pred | Avg Prob Edge | Avg Votes |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for metric in report.get("metrics", []):
        lines.append(
            "| {basket} | {names} | {avg} | {median} | {win} | {pred} | {edge} | {votes} |".format(
                basket=metric["basket"],
                names=metric["names"],
                avg=_fmt_pct(metric["avg_return"]),
                median=_fmt_pct(metric["median_return"]),
                win=_fmt_pct(metric["win_rate"]),
                pred=_fmt_pct(metric["avg_pred_return_20d"]),
                edge=_fmt_pct(metric["avg_prob_edge"]),
                votes=_fmt_float(metric["avg_votes"]),
            )
        )

    lines.append("")
    lines.append("## Sanity Checks")
    lines.append("")
    lines.append(f"- ML selected minus blocked counterfactual: `{_fmt_pct(report.get('selected_minus_blocked'))}`")
    lines.append(
        "- ML selected minus simple consensus strong: "
        f"`{_fmt_pct(report.get('selected_minus_simple_consensus_strong'))}`"
    )
    failures = report.get("failures", [])
    warnings = report.get("warnings", [])
    lines.append(f"- Failures: `{', '.join(failures) if failures else 'none'}`")
    lines.append(f"- Warnings: `{', '.join(warnings) if warnings else 'none'}`")

    if report.get("repair_policies"):
        lines.append("")
        lines.append("## Shadow Repair Policies")
        lines.append("")
        lines.append("| Policy | Kept | Dropped | Avg | Median | Win | Improvement vs Current |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for policy in report["repair_policies"]:
            lines.append(
                "| {policy} | {kept} | {dropped} | {avg} | {median} | {win} | {improvement} |".format(
                    policy=policy["basket"],
                    kept=policy["kept_selected"],
                    dropped=policy["dropped_selected"],
                    avg=_fmt_pct(policy["avg_return"]),
                    median=_fmt_pct(policy["median_return"]),
                    win=_fmt_pct(policy["win_rate"]),
                    improvement=_fmt_pct(policy["avg_return_improvement_vs_current"]),
                )
            )
        proposed = report.get("proposed_shadow_guard")
        if proposed:
            lines.append("")
            lines.append("Proposed P0 shadow guard:")
            lines.append("")
            lines.append(
                "- Keep ML-selected names only when `prob_edge >= -10%` "
                "or `benchmark_votes >= 5`."
            )
            lines.append(
                "- This is ex-ante only: no realized return, NAV/B4 label, or post-entry field is used."
            )
            lines.append(
                "- The same rule is available in the unified signal path as "
                "`ENTRY_BENCHMARK_GUARD`; use this report to audit its effect."
            )

    if report.get("daily"):
        lines.append("")
        lines.append("## Daily Selected Basket")
        lines.append("")
        lines.append("| Signal | Entry | Names | Avg | Win | TWII Open-to-Mark | Alpha |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for row in report["daily"]:
            lines.append(
                "| {signal} | {entry} | {names} | {avg} | {win} | {twii} | {alpha} |".format(
                    signal=row["signal_date"],
                    entry=row["entry_date"],
                    names=row["names"],
                    avg=_fmt_pct(row["avg_return"]),
                    win=_fmt_pct(row["win_rate"]),
                    twii=_fmt_pct(row.get("twii_open_to_mark")),
                    alpha=_fmt_pct(row.get("alpha_vs_twii")),
                )
            )

    priced = candidates[candidates["has_return"]].copy()
    if not priced.empty:
        cols = [
            "signal_date",
            "entry_date",
            "ticker",
            "selected_by_ml",
            "benchmark_consensus",
            "benchmark_votes",
            "return_to_mark",
            "pred_return_20d",
            "prob_edge",
            "penalty_overlay_reason",
            "tradability_reason",
        ]
        available_cols = [col for col in cols if col in priced.columns]
        top = priced.sort_values("return_to_mark", ascending=False)[available_cols].head(10)
        bottom = priced.sort_values("return_to_mark")[available_cols].head(10)
        for title, table in [("Best Candidates", top), ("Worst Candidates", bottom)]:
            lines.append("")
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| " + " | ".join(available_cols) + " |")
            lines.append("|" + "|".join(["---"] * len(available_cols)) + "|")
            for _, row in table.iterrows():
                values: list[str] = []
                for col in available_cols:
                    value = row[col]
                    if col in {"return_to_mark", "pred_return_20d", "prob_edge"}:
                        values.append(_fmt_pct(value))
                    else:
                        values.append("" if pd.isna(value) else str(value))
                lines.append("| " + " | ".join(values) + " |")

    return "\n".join(lines) + "\n"


def write_outputs(
    report: dict[str, Any],
    candidates: pd.DataFrame,
    *,
    output_dir: str | Path = REPORT_DIR,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    md_path = output_path / f"{output_prefix}.md"
    json_path = output_path / f"{output_prefix}.json"
    detail_path = output_path / f"{output_prefix}_candidates.csv"

    md_path.write_text(_render_markdown(report, candidates), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates.to_csv(detail_path, index=False, encoding="utf-8-sig")
    return {"md": str(md_path), "json": str(json_path), "candidates": str(detail_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark unified entry-filter decisions against simple baselines.")
    parser.add_argument("--start-date", required=True, help="First unified signal date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Last unified signal date, YYYY-MM-DD.")
    parser.add_argument("--mark-date", default=None, help="Mark-to-market date. Default: latest daily_k date.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--model-dir", default=str(MODEL_DIR))
    parser.add_argument("--output-dir", default=str(REPORT_DIR))
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = build_candidate_frame(
        args.start_date,
        args.end_date,
        mark_date=args.mark_date,
        model_dir=args.model_dir,
        db_path=args.db_path,
    )
    report = compute_report(candidates, db_path=args.db_path)
    outputs = write_outputs(report, candidates, output_dir=args.output_dir, output_prefix=args.output_prefix)
    print(f"Entry filter benchmark status: {report['status']}")
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
