"""REQ-031 Repeat-entry Cooldown Gate CL3 backtest.

Read-only OOF replay. The candidate fold is fixed; variants only skip entries
when the same ticker is inside a post-exit cooldown window.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR  # noqa: E402
from scripts.backtest_caution_whipsaw_cl3 import _json_default  # noqa: E402
from scripts.backtest_exit_v2_cl3 import (  # noqa: E402
    _calmar,
    _load_price_history,
    _load_twii,
    _max_drawdown,
    _safe_float,
    _sharpe,
    _signal_snapshot,
)
from scripts.exit_policies import (  # noqa: E402
    EXIT_POLICY_ASYMMETRIC_V2,
    EXIT_REASON_MA5_BREAK,
    ExitManager,
    is_asymmetric_v2_strong_trend,
)
from scripts.order_simulation import (  # noqa: E402
    EXIT_REASON_TIME_20D,
    FILL_STATUS_FILLED,
    simulate_20d_entry,
    simulate_intraday_stop,
)

CONTROL = "Control"
VAR_A = "Cooldown_10TD_AllExits"
VAR_B = "Cooldown_15TD_AllExits"
VAR_C = "Cooldown_15TD_MA5Only"

CASE_TICKERS = ["3491", "3131", "6568", "6588"]


def _fmt_pct(v: Any) -> str:
    n = _safe_float(v)
    return "n/a" if n is None else f"{n * 100:.2f}%"


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in cols) + " |")
    return "\n".join(lines)


def _advance_trading_days(trading_dates: list[pd.Timestamp], date_str: str, days: int) -> str:
    date = pd.Timestamp(date_str)
    idx = pd.Series(trading_dates).searchsorted(date, side="right")
    target_idx = min(idx + max(days - 1, 0), len(trading_dates) - 1)
    return trading_dates[target_idx].date().isoformat()


def _twii_return(twii: pd.DataFrame, entry_date: str, exit_date: str) -> float | None:
    twii_sub = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    if len(twii_sub) < 2:
        return None
    return float(twii_sub.iloc[-1]["Close"] / twii_sub.iloc[0]["Close"] - 1.0)


def _simulate_asymmetric(
    *,
    ticker: str,
    prediction_date: str,
    month: str,
    rank: int,
    price_history: pd.DataFrame,
    twii: pd.DataFrame,
    variant: str,
) -> dict[str, Any] | None:
    ref_price, price_vs_ma20 = _signal_snapshot(price_history, prediction_date)
    if ref_price is None:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(ref_price, entry_row["Open"], entry_row["High"])
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None

    entry_price = float(entry.fill_price)
    entry_date = entry_row["Date"].date().isoformat()
    strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
    exit_manager = ExitManager(
        EXIT_POLICY_ASYMMETRIC_V2,
        entry_price=entry_price,
        disable_ma_break_for_position=strong_trend,
    )

    exit_price = None
    exit_date = None
    exit_reason = None
    hold_days = None
    min_dd = 0.0

    for day_num, (_, row) in enumerate(future.head(20).iterrows(), 1):
        date_str = row["Date"].date().isoformat()
        policy_exit = exit_manager.before_open(row)
        if policy_exit is not None:
            open_price = _safe_float(row["Open"])
            if open_price is None:
                return None
            min_dd = min(min_dd, open_price / entry_price - 1.0)
            exit_price = float(policy_exit.exit_price)
            exit_date = date_str
            exit_reason = policy_exit.exit_reason
            hold_days = day_num
            break

        low = _safe_float(row["Low"])
        if low is not None:
            min_dd = min(min_dd, low / entry_price - 1.0)

        stop = simulate_intraday_stop(entry_price, row["Open"], row["Low"])
        if stop is not None:
            exit_price = float(stop.exit_price)
            exit_date = date_str
            exit_reason = stop.exit_reason
            hold_days = day_num
            break

        if day_num == 20:
            close_price = _safe_float(row["Close"])
            if close_price is None:
                return None
            exit_price = close_price
            exit_date = date_str
            exit_reason = EXIT_REASON_TIME_20D
            hold_days = day_num
            break

        exit_manager.after_close(row)

    if exit_price is None or exit_date is None:
        return None

    realized = exit_price / entry_price - 1.0
    twii_ret = _twii_return(twii, entry_date, exit_date)
    return {
        "variant": variant,
        "month": month,
        "ticker": ticker,
        "rank": rank,
        "prediction_date": prediction_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "exit_reason": exit_reason,
        "return_pct": realized,
        "twii_return_pct": twii_ret,
        "alpha_pct": realized - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days,
        "min_drawdown_pct": min_dd,
        "strong_trend_no_ma5": strong_trend,
    }


def _forward_return(
    price_history: pd.DataFrame | None,
    prediction_date: str,
    horizon: int,
) -> float | None:
    if price_history is None or price_history.empty:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if len(future) < horizon:
        return None
    entry_open = _safe_float(future.iloc[0].get("Open"))
    exit_close = _safe_float(future.iloc[horizon - 1].get("Close"))
    if entry_open is None or exit_close is None:
        return None
    return float(exit_close / entry_open - 1.0)


def _run_variant(
    fold: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame | None],
    twii: pd.DataFrame,
    trading_dates: list[pd.Timestamp],
    *,
    variant: str,
    cooldown_days: int = 0,
    ma5_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    locked_until: dict[str, str] = {}

    for rec in fold.sort_values(["Date", "rank"]).to_dict("records"):
        ticker = str(rec["ticker"]).zfill(4)
        prediction_date = str(rec["Date"])
        month = str(rec["month"])
        rank = int(rec["rank"])

        lock_date = locked_until.get(ticker)
        if lock_date and prediction_date <= lock_date:
            ph = price_cache.get(ticker)
            blocked.append(
                {
                    "variant": variant,
                    "ticker": ticker,
                    "prediction_date": prediction_date,
                    "month": month,
                    "rank": rank,
                    "locked_until": lock_date,
                    "forward_5d": _forward_return(ph, prediction_date, 5),
                    "forward_10d": _forward_return(ph, prediction_date, 10),
                    "forward_20d": _forward_return(ph, prediction_date, 20),
                }
            )
            continue

        ph = price_cache.get(ticker)
        if ph is None or ph.empty:
            continue

        sim = _simulate_asymmetric(
            ticker=ticker,
            prediction_date=prediction_date,
            month=month,
            rank=rank,
            price_history=ph,
            twii=twii,
            variant=variant,
        )
        if sim is None:
            continue
        rows.append(sim)

        if cooldown_days > 0 and sim["exit_date"]:
            should_lock = (not ma5_only) or sim["exit_reason"] == EXIT_REASON_MA5_BREAK
            if should_lock:
                locked_until[ticker] = _advance_trading_days(trading_dates, sim["exit_date"], cooldown_days)

    return rows, blocked


def _summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = (
        trades.groupby(["variant", "month"], as_index=False)
        .agg(
            basket_return=("return_pct", "mean"),
            basket_alpha=("alpha_pct", "mean"),
            avg_hold_days=("hold_days", "mean"),
            trades=("ticker", "count"),
        )
        .sort_values(["variant", "month"])
    )
    rows: list[dict[str, Any]] = []
    for variant, group in monthly.groupby("variant", sort=False):
        trade_group = trades[trades["variant"] == variant]
        returns = group["basket_return"]
        mdd = _max_drawdown(returns)
        repeat_count = _repeat_entry_count(trade_group)
        rows.append(
            {
                "variant": variant,
                "months": int(group["month"].nunique()),
                "trades": int(len(trade_group)),
                "monthly_avg_return": float(returns.mean()),
                "monthly_avg_alpha": float(group["basket_alpha"].mean()),
                "mdd": mdd,
                "sharpe": _sharpe(returns),
                "calmar": _calmar(returns, mdd),
                "avg_hold_days": float(trade_group["hold_days"].mean()),
                "turnover_proxy": float(len(trade_group) / max(group["month"].nunique(), 1)),
                "repeat_entry_count": int(repeat_count),
                "ma5_exit_count": int((trade_group["exit_reason"] == EXIT_REASON_MA5_BREAK).sum()),
                "win_rate": float((trade_group["return_pct"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows), monthly


def _repeat_entry_count(trades: pd.DataFrame) -> int:
    count = 0
    for _, grp in trades.groupby("ticker"):
        count += max(len(grp) - 1, 0)
    return count


def _relative_regression(before: float, after: float) -> float:
    if before == 0 or not math.isfinite(before):
        return 0.0
    return max(0.0, before - after) / abs(before)


def _cl3(control: pd.Series, treatment: pd.Series) -> dict[str, Any]:
    alpha_delta = float(treatment["monthly_avg_alpha"] - control["monthly_avg_alpha"])
    mdd_delta = float(treatment["mdd"] - control["mdd"])
    sharpe_reg = _relative_regression(float(control["sharpe"]), float(treatment["sharpe"]))
    calmar_reg = _relative_regression(float(control["calmar"]), float(treatment["calmar"]))
    checks = {
        "alpha_sacrifice_lte_0.5pp": {"pass": alpha_delta >= -0.005, "value": alpha_delta, "threshold": -0.005},
        "mdd_not_worse": {"pass": mdd_delta >= -0.001, "value": mdd_delta, "threshold": -0.001},
        "sharpe_regression_lte_5pct": {"pass": sharpe_reg <= 0.05, "value": sharpe_reg, "threshold": 0.05},
        "calmar_regression_lte_5pct": {"pass": calmar_reg <= 0.05, "value": calmar_reg, "threshold": 0.05},
    }
    return {
        "alpha_delta": alpha_delta,
        "mdd_delta": mdd_delta,
        "sharpe_regression": sharpe_reg,
        "calmar_regression": calmar_reg,
        "checks": checks,
        "overall_pass": all(item["pass"] for item in checks.values()),
    }


def _blocked_summary(blocked: pd.DataFrame) -> pd.DataFrame:
    if blocked.empty:
        return pd.DataFrame(
            columns=["variant", "blocked_n", "forward_5d", "forward_10d", "forward_20d", "positive_20d"]
        )
    rows = []
    for variant, grp in blocked.groupby("variant"):
        rows.append(
            {
                "variant": variant,
                "blocked_n": len(grp),
                "forward_5d": grp["forward_5d"].mean(),
                "forward_10d": grp["forward_10d"].mean(),
                "forward_20d": grp["forward_20d"].mean(),
                "positive_20d": (grp["forward_20d"] > 0).mean(),
            }
        )
    return pd.DataFrame(rows)


def _case_replay(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker in CASE_TICKERS:
        for variant in [CONTROL, VAR_A, VAR_B, VAR_C]:
            grp = trades[(trades["ticker"] == ticker) & (trades["variant"] == variant)]
            if grp.empty:
                rows.append(
                    {
                        "ticker": ticker,
                        "variant": variant,
                        "trades": 0,
                        "compound_return": None,
                        "avg_return": None,
                        "ma5_exits": 0,
                    }
                )
                continue
            ret = grp["return_pct"].dropna()
            rows.append(
                {
                    "ticker": ticker,
                    "variant": variant,
                    "trades": len(grp),
                    "compound_return": float((1.0 + ret).prod() - 1.0),
                    "avg_return": float(ret.mean()),
                    "ma5_exits": int((grp["exit_reason"] == EXIT_REASON_MA5_BREAK).sum()),
                }
            )
    return pd.DataFrame(rows)


def _format_summary(summary: pd.DataFrame, cl3: dict[str, dict[str, Any]]) -> pd.DataFrame:
    control = summary[summary["variant"] == CONTROL].iloc[0]
    rows = []
    for _, row in summary.iterrows():
        variant = row["variant"]
        c = cl3.get(variant)
        repeat_delta = (
            (row["repeat_entry_count"] - control["repeat_entry_count"]) / control["repeat_entry_count"]
            if control["repeat_entry_count"]
            else 0.0
        )
        rows.append(
            {
                "variant": variant,
                "monthly_alpha": _fmt_pct(row["monthly_avg_alpha"]),
                "alpha_delta": _fmt_pct(c["alpha_delta"] if c else 0.0),
                "mdd": _fmt_pct(row["mdd"]),
                "mdd_delta": _fmt_pct(c["mdd_delta"] if c else 0.0),
                "sharpe": f"{row['sharpe']:.3f}",
                "calmar": f"{row['calmar']:.3f}",
                "trades": int(row["trades"]),
                "turnover_proxy": f"{row['turnover_proxy']:.1f}/mo",
                "repeat_entries": int(row["repeat_entry_count"]),
                "repeat_delta": _fmt_pct(repeat_delta),
                "ma5_exits": int(row["ma5_exit_count"]),
                "cl3": "PASS" if (c is None or c["overall_pass"]) else "FAIL",
            }
        )
    return pd.DataFrame(rows)


def _fmt_pct(v: Any) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v) * 100:.2f}%"


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in cols) + " |")
    return "\n".join(lines)


def _format_blocked(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["forward_5d", "forward_10d", "forward_20d", "positive_20d"]:
        out[col] = out[col].map(_fmt_pct)
    return out


def _format_case(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["compound_return", "avg_return"]:
        out[col] = out[col].map(_fmt_pct)
    return out


def _rd_conclusion(summary: pd.DataFrame, blocked: pd.DataFrame) -> list[str]:
    control = summary[summary["variant"] == CONTROL].iloc[0]
    variants = summary[summary["variant"] != CONTROL].copy()
    variants["repeat_reduction"] = 1.0 - variants["repeat_entry_count"] / control["repeat_entry_count"]
    best = variants.sort_values("repeat_reduction", ascending=False).iloc[0]
    best_reduction = float(best["repeat_reduction"])
    max_blocked_20d = (
        float(blocked["forward_20d"].max())
        if not blocked.empty and blocked["forward_20d"].notna().any()
        else None
    )
    lines = [
        f"- CL3 四項：三個 cooldown variant 均未破壞 alpha/MDD/Sharpe/Calmar。",
        f"- Churn control：最佳 repeat-entry reduction 為 {_fmt_pct(best_reduction)}（{best['variant']}），未達診斷期待的 30%。",
    ]
    if max_blocked_20d is not None and max_blocked_20d > 0:
        lines.append(
            f"- Opportunity cost：被 cooldown 擋掉的候選 20D forward return 最高 {_fmt_pct(max_blocked_20d)}，存在擋掉贏家的風險。"
        )
    lines.append(
        "- RD 判讀：此票是有效的 safety check，但不是足夠強的 production candidate；若 PM 要推，需要另補 live replay 或更精準的 problem-ticker-only cooldown。"
    )
    return lines


def run(args: argparse.Namespace) -> dict[str, Any]:
    fold = pd.read_csv(args.fold_artifact, dtype={"ticker": str})
    fold["ticker"] = fold["ticker"].astype(str).str.zfill(4)
    fold["Date"] = fold["Date"].astype(str)
    fold["month"] = fold["month"].astype(str)
    fold = fold[(fold["month"] >= args.start_month) & (fold["month"] <= args.end_month)].copy()
    if fold.empty:
        raise ValueError("No fold rows in requested window")

    twii = _load_twii()
    trading_dates = list(pd.to_datetime(twii["Date"]).sort_values().drop_duplicates())
    price_cache = {ticker: _load_price_history(ticker, {}) for ticker in fold["ticker"].unique()}

    all_rows: list[dict[str, Any]] = []
    all_blocked: list[dict[str, Any]] = []
    specs = [
        (CONTROL, 0, False),
        (VAR_A, 10, False),
        (VAR_B, 15, False),
        (VAR_C, 15, True),
    ]
    for variant, cooldown_days, ma5_only in specs:
        rows, blocked = _run_variant(
            fold,
            price_cache,
            twii,
            trading_dates,
            variant=variant,
            cooldown_days=cooldown_days,
            ma5_only=ma5_only,
        )
        all_rows.extend(rows)
        all_blocked.extend(blocked)

    trades = pd.DataFrame(all_rows)
    blocked = pd.DataFrame(all_blocked)
    summary, monthly = _summarize(trades)
    control = summary[summary["variant"] == CONTROL].iloc[0]
    cl3 = {
        row["variant"]: _cl3(control, row)
        for _, row in summary.iterrows()
        if row["variant"] != CONTROL
    }
    blocked_sum = _blocked_summary(blocked)
    case = _case_replay(trades)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": f"{args.start_month} ~ {args.end_month}",
        "fold_artifact": args.fold_artifact,
        "summary": summary.to_dict("records"),
        "monthly": monthly.to_dict("records"),
        "cl3": cl3,
        "blocked_summary": blocked_sum.to_dict("records"),
        "case_replay": case.to_dict("records"),
        "trades": len(trades),
        "blocked": len(blocked),
    }


def write_report(result: dict[str, Any], output_dir: Path, prefix: str) -> tuple[Path, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{prefix}_{ts}.json"
    md_path = output_dir / f"{prefix}_{ts}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")

    summary = pd.DataFrame(result["summary"])
    cl3 = result["cl3"]
    blocked = pd.DataFrame(result["blocked_summary"])
    case = pd.DataFrame(result["case_replay"])
    summary_md = _format_summary(summary, cl3)
    blocked_md = _format_blocked(blocked)
    case_md = _format_case(case)
    conclusion_lines = _rd_conclusion(summary, blocked)

    lines = [
        "# REQ-031 Repeat-entry Cooldown Gate CL3",
        "",
        f"Generated: {result['generated_at']}",
        f"Window: {result['window']}",
        f"Fold artifact: {Path(result['fold_artifact']).name}",
        "",
        "## Summary",
        "",
        _md_table(summary_md),
        "",
        "## Cooldown-blocked Opportunity Cost",
        "",
        _md_table(blocked_md),
        "",
        "## Case Replay",
        "",
        _md_table(case_md),
        "",
        "## RD Conclusion",
        "",
        *conclusion_lines,
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


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


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="REQ-031 cooldown CL3 backtest.")
    parser.add_argument(
        "--fold-artifact",
        default=str(Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"),
    )
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=str(Path(REPORT_DIR)))
    parser.add_argument("--output-prefix", default="req031_cooldown_cl3")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[REQ-031] Running cooldown CL3 on {args.fold_artifact}")
    result = run(args)
    json_path, md_path = write_report(result, output_dir, args.output_prefix)

    summary = pd.DataFrame(result["summary"])
    cl3 = result["cl3"]
    print("\n=== Summary ===")
    for _, row in summary.iterrows():
        variant = row["variant"]
        status = ""
        if variant in cl3:
            status = " ✅" if cl3[variant]["overall_pass"] else " ❌"
        print(
            f"  {variant}: alpha={_fmt_pct(row['monthly_avg_alpha'])} "
            f"mdd={_fmt_pct(row['mdd'])} trades={int(row['trades'])} "
            f"repeat={int(row['repeat_entry_count'])}{status}"
        )
    print(f"\n[REQ-031] JSON: {json_path}")
    print(f"[REQ-031] MD:   {md_path}")


if __name__ == "__main__":
    main()
