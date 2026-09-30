"""RD-2026-0516-003: MA5_BREAK re-entry v1.2 bounded experiment.

This is a research artifact only. It does not modify production entry,
selection, allocation, or exit code.

v1.2 hard spec:
- Candidate: original exit_reason == MA5_BREAK.
- True-loser precheck: original realized_return_pct < -6% is excluded.
- Trigger: within 1-3 trading days after exit, close above MA5 for at least
  two days, and the trigger-day close is above both MA5 and MA10.
- Execution: enter at the next trading day's open after trigger close.
- Size: min(original_target_weight * 50%, 8% NAV).
- Exit: second close below MA5 after re-entry schedules final exit at the
  next trading day's open. If no next row exists, use last close mark.
- One re-entry per original position.

Classification functions may use future returns for analysis. Trigger
functions must not.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR  # noqa: E402

DEFAULT_POST_EXIT = ROOT / "exports" / "champion_ma5break_post_exit.csv"
DEFAULT_OUTPUT_PREFIX = "rd20260516_003_ma5_reentry_v12"

MAX_REENTRY_WEIGHT = 0.08
REENTRY_WEIGHT_MULTIPLIER = 0.50
TRUE_LOSER_REALIZED_RETURN_CUTOFF = -0.06
TRIGGER_MAX_DAYS = 3
MIN_DAYS_ABOVE_MA5 = 2

# RD-proposed turnover cap. PM can ratify or override during acceptance.
RD_PROPOSED_TURNOVER_CAP = 1.50  # total round-trip notional, x NAV, over this holdout export.


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def classify_post_exit_outcome(group: pd.DataFrame) -> str:
    """Look-ahead classification for analysis only.

    This must not be called by the production trigger.
    """
    group = group.sort_values("post_exit_day_number")
    max_gain = _safe_float(group["max_gain_since_exit"].max()) or 0.0
    day3 = group[group["post_exit_day_number"].le(3)]
    days_above_ma5 = int(day3["close_above_ma5"].fillna(False).astype(bool).sum())
    row3 = group[group["post_exit_day_number"].eq(3)]
    return_3d = _safe_float(row3["return_since_exit"].iloc[-1]) if not row3.empty else None
    row10 = group[group["post_exit_day_number"].eq(10)]
    return_10d = _safe_float(row10["return_since_exit"].iloc[-1]) if not row10.empty else None
    if max_gain < 0.005:
        return "true_loser"
    if days_above_ma5 >= 2 and return_3d is not None and return_3d > 0.02:
        return "whipsaw_short"
    if return_10d is not None and return_10d > 0.05:
        return "late_winner"
    return "drift"


@dataclass(frozen=True)
class TriggerResult:
    triggered: bool
    trigger_day_number: int | None = None
    trigger_date: str | None = None
    entry_day_number: int | None = None
    entry_date: str | None = None
    entry_price: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class ReentryTrade:
    original_position_id: int
    ticker: str
    sector: str
    exit_date: str
    original_exit_price: float
    original_realized_return_pct: float
    original_target_weight: float
    reentry_weight: float
    classification: str
    full_10d_coverage: bool
    trigger_day_number: int
    trigger_date: str
    entry_day_number: int
    entry_date: str
    entry_price: float
    final_exit_day_number: int
    final_exit_date: str
    final_exit_price: float
    final_exit_reason: str
    reentry_return: float
    weighted_contribution: float
    max_drawdown: float
    second_ma5_break: bool
    available_days_after_exit: int


def find_reentry_trigger(group: pd.DataFrame) -> TriggerResult:
    """Find a v1.2 trigger using only days 1-3 after exit and no future returns."""
    first_days = group[group["post_exit_day_number"].between(1, TRIGGER_MAX_DAYS)].copy()
    if first_days.empty:
        return TriggerResult(False, reason="NO_POST_EXIT_ROWS")

    days_above = 0
    for _, row in first_days.sort_values("post_exit_day_number").iterrows():
        close = _safe_float(row.get("post_close"))
        ma5 = _safe_float(row.get("post_ma5"))
        ma10 = _safe_float(row.get("post_ma10"))
        if close is None or ma5 is None or ma10 is None:
            continue
        above_ma5 = close > ma5
        if above_ma5:
            days_above += 1
        if days_above >= MIN_DAYS_ABOVE_MA5 and close > ma5 and close > ma10:
            trigger_day = int(row["post_exit_day_number"])
            entry_rows = group[group["post_exit_day_number"].gt(trigger_day)].sort_values("post_exit_day_number")
            if entry_rows.empty:
                return TriggerResult(
                    False,
                    trigger_day_number=trigger_day,
                    trigger_date=str(row["post_exit_date"]),
                    reason="TRIGGERED_BUT_NO_NEXT_OPEN",
                )
            entry_row = entry_rows.iloc[0]
            entry_price = _safe_float(entry_row.get("post_open"))
            if entry_price is None:
                return TriggerResult(
                    False,
                    trigger_day_number=trigger_day,
                    trigger_date=str(row["post_exit_date"]),
                    reason="TRIGGERED_BUT_MISSING_NEXT_OPEN",
                )
            return TriggerResult(
                True,
                trigger_day_number=trigger_day,
                trigger_date=str(row["post_exit_date"]),
                entry_day_number=int(entry_row["post_exit_day_number"]),
                entry_date=str(entry_row["post_exit_date"]),
                entry_price=entry_price,
                reason="TRIGGERED",
            )
    return TriggerResult(False, reason="NO_DUAL_MA_CONFIRMATION")


def simulate_reentry_trade(group: pd.DataFrame, classification: str) -> tuple[ReentryTrade | None, str]:
    """Simulate one v1.2 re-entry, if eligible."""
    group = group.sort_values("post_exit_day_number").copy()
    first = group.iloc[0]
    original_id = int(first["original_position_id"])
    realized = _safe_float(first.get("realized_return_pct"))
    target_weight = _safe_float(first.get("target_weight"))
    exit_price = _safe_float(first.get("exit_price"))
    if realized is None:
        return None, "MISSING_ORIGINAL_REALIZED_RETURN"
    if target_weight is None or target_weight <= 0:
        return None, "MISSING_ORIGINAL_TARGET_WEIGHT"
    if exit_price is None or exit_price <= 0:
        return None, "MISSING_ORIGINAL_EXIT_PRICE"
    if realized < TRUE_LOSER_REALIZED_RETURN_CUTOFF:
        return None, "EXCLUDED_TRUE_LOSER_PRECHECK"

    trigger = find_reentry_trigger(group)
    if not trigger.triggered:
        return None, trigger.reason

    assert trigger.entry_day_number is not None
    assert trigger.entry_price is not None

    after_entry = group[group["post_exit_day_number"].ge(trigger.entry_day_number)].sort_values("post_exit_day_number")
    max_drawdown = 0.0
    final_exit_reason = "HORIZON_MARK"
    final_exit_day = int(after_entry.iloc[-1]["post_exit_day_number"])
    final_exit_date = str(after_entry.iloc[-1]["post_exit_date"])
    final_exit_price = _safe_float(after_entry.iloc[-1].get("post_close")) or trigger.entry_price
    second_ma5_break = False

    rows = list(after_entry.iterrows())
    pending_exit = False
    for idx, (_, row) in enumerate(rows):
        open_price = _safe_float(row.get("post_open"))
        low = _safe_float(row.get("post_low"))
        close = _safe_float(row.get("post_close"))
        ma5 = _safe_float(row.get("post_ma5"))
        day_num = int(row["post_exit_day_number"])
        date_str = str(row["post_exit_date"])

        if pending_exit:
            if open_price is not None:
                final_exit_day = day_num
                final_exit_date = date_str
                final_exit_price = open_price
                final_exit_reason = "SECOND_MA5_BREAK_NEXT_OPEN"
                break

        if low is not None:
            max_drawdown = min(max_drawdown, low / trigger.entry_price - 1.0)

        if close is not None and ma5 is not None and close < ma5:
            second_ma5_break = True
            pending_exit = True
            if idx == len(rows) - 1:
                final_exit_day = day_num
                final_exit_date = date_str
                final_exit_price = close
                final_exit_reason = "SECOND_MA5_BREAK_LAST_CLOSE"
                break

    reentry_return = final_exit_price / trigger.entry_price - 1.0
    weight = min(target_weight * REENTRY_WEIGHT_MULTIPLIER, MAX_REENTRY_WEIGHT)
    max_available_day = int(group["post_exit_day_number"].max())
    trade = ReentryTrade(
        original_position_id=original_id,
        ticker=str(first["ticker"]).zfill(4),
        sector=str(first.get("sector") or ""),
        exit_date=str(first["exit_date"]),
        original_exit_price=exit_price,
        original_realized_return_pct=realized,
        original_target_weight=target_weight,
        reentry_weight=weight,
        classification=classification,
        full_10d_coverage=max_available_day >= 10,
        trigger_day_number=int(trigger.trigger_day_number or 0),
        trigger_date=str(trigger.trigger_date),
        entry_day_number=int(trigger.entry_day_number),
        entry_date=str(trigger.entry_date),
        entry_price=float(trigger.entry_price),
        final_exit_day_number=final_exit_day,
        final_exit_date=final_exit_date,
        final_exit_price=float(final_exit_price),
        final_exit_reason=final_exit_reason,
        reentry_return=float(reentry_return),
        weighted_contribution=float(reentry_return * weight),
        max_drawdown=float(max_drawdown),
        second_ma5_break=second_ma5_break,
        available_days_after_exit=max_available_day,
    )
    return trade, "TRADE"


def build_overlay_nav(trades: pd.DataFrame, post_exit: pd.DataFrame) -> pd.DataFrame:
    """Build incremental overlay NAV contribution by date for MDD diagnostics."""
    if trades.empty:
        return pd.DataFrame(columns=["date", "overlay_pnl", "overlay_drawdown"])

    pnl_rows: list[dict[str, Any]] = []
    grouped_post = {
        int(pid): g.sort_values("post_exit_day_number")
        for pid, g in post_exit.groupby("original_position_id")
    }
    for trade in trades.to_dict("records"):
        pid = int(trade["original_position_id"])
        g = grouped_post[pid]
        entry_day = int(trade["entry_day_number"])
        exit_day = int(trade["final_exit_day_number"])
        entry_price = float(trade["entry_price"])
        weight = float(trade["reentry_weight"])
        relevant = g[g["post_exit_day_number"].between(entry_day, exit_day)].copy()
        for _, row in relevant.iterrows():
            close = _safe_float(row.get("post_close"))
            if close is None:
                continue
            day_num = int(row["post_exit_day_number"])
            if day_num == exit_day:
                close = float(trade["final_exit_price"])
            pnl_rows.append(
                {
                    "date": str(row["post_exit_date"]),
                    "original_position_id": pid,
                    "weighted_pnl": weight * (close / entry_price - 1.0),
                }
            )
    marks = pd.DataFrame(pnl_rows)
    if marks.empty:
        return pd.DataFrame(columns=["date", "overlay_pnl", "overlay_drawdown"])

    pivot = marks.pivot_table(
        index="date",
        columns="original_position_id",
        values="weighted_pnl",
        aggfunc="last",
    ).sort_index()
    pivot = pivot.ffill().fillna(0.0)
    out = pd.DataFrame({"date": pivot.index, "overlay_pnl": pivot.sum(axis=1).values})
    running_peak = out["overlay_pnl"].cummax()
    out["overlay_drawdown"] = out["overlay_pnl"] - running_peak
    return out


def summarize_subset(trades: pd.DataFrame, *, name: str) -> dict[str, Any]:
    if trades.empty:
        return {
            "subset": name,
            "trades": 0,
            "avg_return": None,
            "win_rate": None,
            "weighted_contribution": 0.0,
            "avg_weight": None,
            "second_ma5_break_rate": None,
        }
    return {
        "subset": name,
        "trades": int(len(trades)),
        "avg_return": float(trades["reentry_return"].mean()),
        "win_rate": float((trades["reentry_return"] > 0).mean()),
        "weighted_contribution": float(trades["weighted_contribution"].sum()),
        "avg_weight": float(trades["reentry_weight"].mean()),
        "second_ma5_break_rate": float(trades["second_ma5_break"].astype(bool).mean()),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    post_exit = pd.read_csv(args.post_exit_csv, dtype={"ticker": str})
    ma5 = post_exit[post_exit["exit_reason"].astype(str).eq("MA5_BREAK")].copy()
    if ma5.empty:
        raise ValueError("No MA5_BREAK rows found")

    trades: list[ReentryTrade] = []
    rejection_rows: list[dict[str, Any]] = []
    classification_rows: list[dict[str, Any]] = []

    for pid, group in ma5.groupby("original_position_id"):
        classification = classify_post_exit_outcome(group)
        classification_rows.append(
            {
                "original_position_id": int(pid),
                "ticker": str(group.iloc[0]["ticker"]).zfill(4),
                "classification": classification,
                "full_10d_coverage": int(group["post_exit_day_number"].max()) >= 10,
                "realized_return_pct": _safe_float(group.iloc[0].get("realized_return_pct")),
                "target_weight": _safe_float(group.iloc[0].get("target_weight")),
            }
        )
        trade, reason = simulate_reentry_trade(group, classification)
        if trade is None:
            rejection_rows.append(
                {
                    "original_position_id": int(pid),
                    "ticker": str(group.iloc[0]["ticker"]).zfill(4),
                    "classification": classification,
                    "rejection_reason": reason,
                    "full_10d_coverage": int(group["post_exit_day_number"].max()) >= 10,
                    "realized_return_pct": _safe_float(group.iloc[0].get("realized_return_pct")),
                    "target_weight": _safe_float(group.iloc[0].get("target_weight")),
                }
            )
        else:
            trades.append(trade)

    trades_df = pd.DataFrame([asdict(t) for t in trades])
    rejections_df = pd.DataFrame(rejection_rows)
    classifications_df = pd.DataFrame(classification_rows)
    overlay_nav = build_overlay_nav(trades_df, ma5)

    all_stats = summarize_subset(trades_df, name="all_horizon")
    full_stats = summarize_subset(trades_df[trades_df["full_10d_coverage"].astype(bool)], name="full_10d_coverage")
    classification_stats = (
        trades_df.groupby("classification", dropna=False)
        .agg(
            trades=("original_position_id", "count"),
            avg_return=("reentry_return", "mean"),
            win_rate=("reentry_return", lambda s: float((s > 0).mean())),
            weighted_contribution=("weighted_contribution", "sum"),
            avg_weight=("reentry_weight", "mean"),
            second_ma5_break_rate=("second_ma5_break", lambda s: float(s.astype(bool).mean())),
        )
        .reset_index()
        if not trades_df.empty
        else pd.DataFrame()
    )
    rejection_stats = (
        rejections_df.groupby("rejection_reason", dropna=False)
        .agg(count=("original_position_id", "count"))
        .reset_index()
        .sort_values("count", ascending=False)
        if not rejections_df.empty
        else pd.DataFrame()
    )

    mdd_worsening = float(-overlay_nav["overlay_drawdown"].min()) if not overlay_nav.empty else 0.0
    round_trip_turnover = float(trades_df["reentry_weight"].sum() * 2.0) if not trades_df.empty else 0.0
    direction_consistent = (
        all_stats["weighted_contribution"] > 0
        and full_stats["weighted_contribution"] > 0
    )
    kill_checks = {
        "weighted_contribution": {
            "value": all_stats["weighted_contribution"],
            "threshold": 0.005,
            "pass": all_stats["weighted_contribution"] >= 0.005,
        },
        "mdd_worsening": {
            "value": mdd_worsening,
            "threshold": 0.010,
            "pass": mdd_worsening <= 0.010,
        },
        "round_trip_turnover": {
            "value": round_trip_turnover,
            "threshold": RD_PROPOSED_TURNOVER_CAP,
            "pass": round_trip_turnover <= RD_PROPOSED_TURNOVER_CAP,
            "note": "RD-proposed cap; requires PM approval.",
        },
        "coverage_direction_consistency": {
            "value": direction_consistent,
            "threshold": True,
            "pass": bool(direction_consistent),
        },
    }
    overall_pass = all(item["pass"] for item in kill_checks.values())

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    paths = {
        "md": output_dir / f"{prefix}.md",
        "json": output_dir / f"{prefix}.json",
        "trades": output_dir / f"{prefix}_trades.csv",
        "rejections": output_dir / f"{prefix}_rejections.csv",
        "classifications": output_dir / f"{prefix}_classifications.csv",
        "overlay_nav": output_dir / f"{prefix}_overlay_nav.csv",
        "classification_stats": output_dir / f"{prefix}_classification_stats.csv",
    }
    trades_df.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    rejections_df.to_csv(paths["rejections"], index=False, encoding="utf-8-sig")
    classifications_df.to_csv(paths["classifications"], index=False, encoding="utf-8-sig")
    overlay_nav.to_csv(paths["overlay_nav"], index=False, encoding="utf-8-sig")
    classification_stats.to_csv(paths["classification_stats"], index=False, encoding="utf-8-sig")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "post_exit_csv": str(Path(args.post_exit_csv).resolve()),
        "ma5_break_original_positions": int(ma5["original_position_id"].nunique()),
        "classification_counts": classifications_df["classification"].value_counts().to_dict(),
        "reentry_trades": int(len(trades_df)),
        "rejected": int(len(rejections_df)),
        "all_horizon": all_stats,
        "full_10d_coverage": full_stats,
        "mdd_worsening": mdd_worsening,
        "round_trip_turnover": round_trip_turnover,
        "rd_proposed_turnover_cap": RD_PROPOSED_TURNOVER_CAP,
        "kill_checks": kill_checks,
        "overall_pass": overall_pass,
        "decision": "PASS" if overall_pass else "KILL",
        "paths": {key: str(value) for key, value in paths.items()},
    }
    paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    write_markdown(paths["md"], result, classification_stats, rejection_stats)
    return result


def write_markdown(
    path: Path,
    result: dict[str, Any],
    classification_stats: pd.DataFrame,
    rejection_stats: pd.DataFrame,
) -> None:
    lines = [
        "# RD-2026-0516-003 MA5_BREAK Re-entry v1.2 Bounded Experiment",
        "",
        f"- Generated at: `{result['generated_at']}`",
        f"- Input: `{result['post_exit_csv']}`",
        "- Scope: research only; no production code path changed.",
        "- Trigger module uses only post-exit day 1-3 MA5/MA10 information. Classification is analysis-only.",
        "",
        "## v1.2 Hard Spec Implemented",
        "",
        "| Item | Value |",
        "|---|---|",
        "| Position size | `min(original_target_weight * 50%, 8% NAV)` |",
        "| True-loser precheck | original `realized_return_pct < -6%` excluded |",
        "| Trigger | post-exit day 1-3, close above MA5 for >=2 days |",
        "| Dual confirmation | trigger day close > MA5 and > MA10 |",
        "| Exit | second MA5 break -> final exit; no re-entry again |",
        "| Per-position cap | one re-entry max per original position |",
        "",
        "## Summary",
        "",
        f"- MA5_BREAK original positions: `{result['ma5_break_original_positions']}`",
        f"- Re-entry trades: `{result['reentry_trades']}`",
        f"- Rejected candidates: `{result['rejected']}`",
        f"- Decision: `{'PASS' if result['overall_pass'] else 'KILL'}`",
        "",
        "## All-horizon vs Full-10D Coverage",
        "",
        "| Subset | Trades | Avg return | Win rate | Weighted contribution | Avg weight | Second MA5 break rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ["all_horizon", "full_10d_coverage"]:
        row = result[key]
        lines.append(
            f"| {row['subset']} | {row['trades']} | {_fmt_pct(row['avg_return'])} | "
            f"{_fmt_pct(row['win_rate'])} | {_fmt_pct(row['weighted_contribution'])} | "
            f"{_fmt_pct(row['avg_weight'])} | {_fmt_pct(row['second_ma5_break_rate'])} |"
        )
    lines.extend(["", "## Kill Criteria", "", "| Check | Value | Threshold | Pass |", "|---|---:|---:|:---:|"])
    for name, item in result["kill_checks"].items():
        value = item["value"]
        threshold = item["threshold"]
        if isinstance(value, bool):
            value_text = str(value)
            threshold_text = str(threshold)
        else:
            value_text = _fmt_pct(float(value))
            threshold_text = _fmt_pct(float(threshold))
        lines.append(f"| {name} | {value_text} | {threshold_text} | {'PASS' if item['pass'] else 'FAIL'} |")

    lines.extend(
        [
            "",
            "## Classification Stats (Triggered Trades Only)",
            "",
            "| Classification | Trades | Avg return | Win rate | Weighted contribution | Avg weight | Second MA5 break rate |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if classification_stats.empty:
        lines.append("| n/a | 0 | n/a | n/a | n/a | n/a | n/a |")
    else:
        for row in classification_stats.to_dict("records"):
            lines.append(
                f"| {row['classification']} | {int(row['trades'])} | {_fmt_pct(row['avg_return'])} | "
                f"{_fmt_pct(row['win_rate'])} | {_fmt_pct(row['weighted_contribution'])} | "
                f"{_fmt_pct(row['avg_weight'])} | {_fmt_pct(row['second_ma5_break_rate'])} |"
            )

    lines.extend(["", "## Rejection Reasons", "", "| Reason | Count |", "|---|---:|"])
    if rejection_stats.empty:
        lines.append("| n/a | 0 |")
    else:
        for row in rejection_stats.to_dict("records"):
            lines.append(f"| {row['rejection_reason']} | {int(row['count'])} |")

    lines.extend(
        [
            "",
            "## RD Verdict",
            "",
            "- Turnover cap proposed by RD: `150% NAV round-trip` across the hold-out export.",
            "- If PM does not approve this cap, turnover criterion remains pending even if numerically under cap.",
            f"- Final bounded-experiment verdict: `{'PASS' if result['overall_pass'] else 'KILL'}`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-exit-csv", default=str(DEFAULT_POST_EXIT))
    parser.add_argument("--output-dir", default=str(REPORT_DIR))
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    return parser.parse_args()


def main() -> int:
    result = run(parse_args())
    print(f"[ma5-reentry-v12] decision={result['decision']}")
    print(
        "[ma5-reentry-v12] all_horizon "
        f"trades={result['all_horizon']['trades']} "
        f"weighted={_fmt_pct(result['all_horizon']['weighted_contribution'])} "
        f"avg={_fmt_pct(result['all_horizon']['avg_return'])} "
        f"win={_fmt_pct(result['all_horizon']['win_rate'])}"
    )
    print(
        "[ma5-reentry-v12] full10 "
        f"trades={result['full_10d_coverage']['trades']} "
        f"weighted={_fmt_pct(result['full_10d_coverage']['weighted_contribution'])}"
    )
    print(
        "[ma5-reentry-v12] risk "
        f"mdd_worsening={_fmt_pct(result['mdd_worsening'])} "
        f"turnover={_fmt_pct(result['round_trip_turnover'])}"
    )
    print(f"[ma5-reentry-v12] report={result['paths']['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
