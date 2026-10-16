"""REQ-021 market-regime exit overlay CL3 backtest.

This research artifact keeps the Two-Stage N=75 OOF Top30 entries fixed and
changes only the exit route:

- Control: current asymmetric_v2
- Variant A: TWII price_vs_ma20 > 3% routes all positions to stop_only_20d
- Variant B: TWII price_vs_ma20 > 5% routes all positions to stop_only_20d
- Variant C: TWII 20D return > 8% routes all positions to stop_only_20d
- Variant D: TWII price_vs_ma20 > 5% disables MA5_BREAK but keeps HWM 8%

Production defaults are not changed by this script.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import INDEX_DIR, REPORT_DIR  # noqa: E402
from scripts.backtest_exit_v2_cl3 import (  # noqa: E402
    _calmar,
    _load_price_history,
    _load_twii,
    _max_drawdown,
    _safe_float,
    _sharpe,
    _signal_snapshot,
    _simulate_trade,
    _summarize,
)
from scripts.exit_policies import (  # noqa: E402
    EXIT_POLICY_ASYMMETRIC_V2,
    REGIME_EXIT_VARIANT_20D_RETURN_8PCT,
    REGIME_EXIT_VARIANT_MA20_3PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT_HWM,
    REGIME_EXIT_VARIANT_NONE,
    is_regime_exit_overlay_active,
    is_asymmetric_v2_strong_trend,
    resolve_regime_exit_route,
)

CONTROL_VARIANT = "Control_AsymmetricV2"
VARIANTS = [
    ("Variant_A_regime_ma20_3pct", REGIME_EXIT_VARIANT_MA20_3PCT),
    ("Variant_B_regime_ma20_5pct", REGIME_EXIT_VARIANT_MA20_5PCT),
    ("Variant_C_regime_20d_return_8pct", REGIME_EXIT_VARIANT_20D_RETURN_8PCT),
    ("Variant_D_regime_ma20_5pct_hwm", REGIME_EXIT_VARIANT_MA20_5PCT_HWM),
]


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _relative_regression(before: float, after: float) -> float:
    if before == 0 or not math.isfinite(before):
        return 0.0
    return max(0.0, before - after) / abs(before)


def _entry_date(price_history: pd.DataFrame | None, prediction_date: str) -> str | None:
    if price_history is None or price_history.empty:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None
    return future.iloc[0]["Date"].date().isoformat()


def _load_twii_from_duckdb() -> pd.DataFrame | None:
    try:
        from backend.db.engine import query_df
    except Exception:
        return None
    queries = [
        """
        SELECT date AS Date, open AS Open, close AS Close
        FROM index_prices
        WHERE ticker = '^TWII'
        ORDER BY date
        """,
        """
        SELECT Date, Open, Close
        FROM index_prices
        WHERE ticker = '^TWII'
        ORDER BY Date
        """,
    ]
    for sql in queries:
        try:
            df = query_df(sql)
        except Exception:
            continue
        if df is not None and not df.empty and {"Date", "Close"} <= set(df.columns):
            return df
    return None


def _load_twii_regime() -> tuple[pd.DataFrame, str]:
    twii = _load_twii_from_duckdb()
    source = "duckdb:index_prices"
    if twii is None or twii.empty:
        twii = _load_twii()
        source = f"csv:{Path(INDEX_DIR) / 'index_TWII.csv'}"

    twii = twii.copy()
    twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
    twii = twii.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    if "Open" not in twii.columns:
        twii["Open"] = twii["Close"]
    for col in ["Open", "Close"]:
        twii[col] = pd.to_numeric(twii[col], errors="coerce")
    twii["MA20"] = twii["Close"].rolling(window=20, min_periods=1).mean()
    twii["price_vs_ma20"] = twii["Close"] / twii["MA20"] - 1.0
    twii["return_20d"] = twii["Close"] / twii["Close"].shift(20) - 1.0
    return twii, source


def get_twii_regime_signal(entry_date: str | None, twii_regime: pd.DataFrame) -> dict[str, float | str | None]:
    """Return entry-day TWII regime metrics using the latest available close."""

    if not entry_date:
        return {
            "twii_signal_date": None,
            "twii_price_vs_ma20": None,
            "twii_return_20d": None,
        }
    rows = twii_regime[twii_regime["Date"] <= pd.Timestamp(entry_date)]
    if rows.empty:
        return {
            "twii_signal_date": None,
            "twii_price_vs_ma20": None,
            "twii_return_20d": None,
        }
    row = rows.iloc[-1]
    return {
        "twii_signal_date": row["Date"].date().isoformat(),
        "twii_price_vs_ma20": _safe_float(row.get("price_vs_ma20")),
        "twii_return_20d": _safe_float(row.get("return_20d")),
    }


def _twii_monthly_returns(twii: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    monthly = twii.dropna(subset=["Date"]).copy()
    monthly["month"] = monthly["Date"].dt.strftime("%Y-%m")
    for month, group in monthly.groupby("month"):
        first_open = _safe_float(group.iloc[0].get("Open"))
        last_close = _safe_float(group.iloc[-1].get("Close"))
        if first_open is None or first_open <= 0 or last_close is None:
            continue
        rows.append({"month": month, "twii_month_return": last_close / first_open - 1.0})
    return pd.DataFrame(rows)


def _cl3_for_variants(summary: pd.DataFrame) -> pd.DataFrame:
    control = summary[summary["variant"].eq(CONTROL_VARIANT)].iloc[0]
    rows: list[dict[str, Any]] = []
    for row in summary.to_dict("records"):
        if row["variant"] == CONTROL_VARIANT:
            continue
        alpha_sacrifice = max(0.0, float(control["monthly_avg_alpha"] - row["monthly_avg_alpha"]))
        mdd_delta = float(row["mdd"] - control["mdd"])
        sharpe_regression = _relative_regression(float(control["sharpe"]), float(row["sharpe"]))
        calmar_regression = _relative_regression(float(control["calmar"]), float(row["calmar"]))
        rows.append(
            {
                "variant": row["variant"],
                "alpha_sacrifice": alpha_sacrifice,
                "alpha_pass": alpha_sacrifice <= 0.005,
                "mdd_delta": mdd_delta,
                "mdd_pass": mdd_delta >= 0.001,
                "sharpe_regression": sharpe_regression,
                "sharpe_pass": sharpe_regression <= 0.05,
                "calmar_regression": calmar_regression,
                "calmar_pass": calmar_regression <= 0.05,
            }
        )
    cl3 = pd.DataFrame(rows)
    cl3["overall_pass"] = cl3[["alpha_pass", "mdd_pass", "sharpe_pass", "calmar_pass"]].all(axis=1)
    return cl3


def _subset_analysis(monthly: pd.DataFrame, twii_monthly: pd.DataFrame) -> pd.DataFrame:
    merged = monthly.merge(twii_monthly, on="month", how="left")
    rows: list[dict[str, Any]] = []
    for variant, group in merged.groupby("variant", sort=False):
        strong = group[group["twii_month_return"] > 0.03]
        weak = group[group["twii_month_return"] < -0.02]
        weak_returns = weak["basket_return"]
        rows.append(
            {
                "variant": variant,
                "strong_months": int(strong["month"].nunique()),
                "strong_month_avg_alpha": float(strong["basket_alpha"].mean()) if not strong.empty else None,
                "weak_months": int(weak["month"].nunique()),
                "weak_month_mdd": _max_drawdown(weak_returns) if not weak_returns.empty else None,
                "weak_month_avg_return": float(weak_returns.mean()) if not weak_returns.empty else None,
            }
        )
    return pd.DataFrame(rows)


def _april_replay(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    april = trades[trades["month"].eq("2026-04")].copy()
    control = april[april["variant"].eq(CONTROL_VARIANT)].copy()
    rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for variant, variant_group in april[~april["variant"].eq(CONTROL_VARIANT)].groupby("variant", sort=False):
        merged = control.merge(
            variant_group,
            on=["ticker", "prediction_date"],
            suffixes=("_control", "_variant"),
        )
        control_ma = merged["exit_reason_control"].astype(str).str.contains("MA5_BREAK")
        variant_not_ma = ~merged["exit_reason_variant"].astype(str).str.contains("MA5_BREAK")
        longer_hold = merged["hold_days_variant"] > merged["hold_days_control"]
        rescued = merged[control_ma & (variant_not_ma | longer_hold)].copy()
        summary_rows.append(
            {
                "variant": variant,
                "april_trades": int(len(merged)),
                "control_ma5_exits": int(control_ma.sum()),
                "rescued_count": int(len(rescued)),
                "rescued_avg_delta_return": float(
                    (rescued["return_pct_variant"] - rescued["return_pct_control"]).mean()
                )
                if not rescued.empty
                else 0.0,
            }
        )
        for row in rescued.to_dict("records"):
            rows.append(
                {
                    "variant": variant,
                    "ticker": row["ticker"],
                    "two_stage_rank": row["two_stage_rank_control"],
                    "control_exit_date": row["exit_date_control"],
                    "control_exit_reason": row["exit_reason_control"],
                    "control_return": row["return_pct_control"],
                    "control_hold_days": row["hold_days_control"],
                    "variant_exit_date": row["exit_date_variant"],
                    "variant_exit_reason": row["exit_reason_variant"],
                    "variant_return": row["return_pct_variant"],
                    "variant_hold_days": row["hold_days_variant"],
                    "delta_return": row["return_pct_variant"] - row["return_pct_control"],
                    "route_reason": row.get("regime_route_reason_variant"),
                    "twii_price_vs_ma20_at_entry": row.get("twii_price_vs_ma20_at_entry_variant"),
                    "twii_return_20d_at_entry": row.get("twii_return_20d_at_entry_variant"),
                }
            )
    return pd.DataFrame(summary_rows), pd.DataFrame(rows)


def _write_md(
    path: Path,
    *,
    summary: pd.DataFrame,
    cl3: pd.DataFrame,
    subsets: pd.DataFrame,
    april_summary: pd.DataFrame,
    april_rescued: pd.DataFrame,
    metadata: dict[str, Any],
) -> None:
    lines = [
        "# REQ-021 Market-Regime Exit Overlay CL3",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Fold artifact: `{metadata['fold_artifact']}`",
        f"- Window: `{metadata['actual_start']}` to `{metadata['actual_end']}`",
        f"- TWII regime source: `{metadata['twii_source']}`",
        f"- Simulated trades: `{metadata['simulated_trades']}`; skipped entries: `{metadata['skipped_entries']}`",
        "- Control: current `asymmetric_v2`.",
        "- Variant D keeps HWM 8% while disabling MA5_BREAK in strong TWII regime.",
        "",
        "## CL3 Summary",
        "",
        "| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | Regime routes | MA exits | HWM exits | Stops | 20D timeout |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['monthly_avg_return'])} | {_fmt_pct(row['monthly_avg_alpha'])} | "
            f"{_fmt_pct(row['mdd'])} | {row['sharpe']:.3f} | {row['calmar']:.3f} | "
            f"{row['avg_hold_days']:.2f} | {row.get('regime_overlay_count', 0)} | {row['ma_exit_count']} | "
            f"{row['hwm_exit_count']} | {row['stop_exit_count']} | {row['timeout_count']} |"
        )

    lines.extend(
        [
            "",
            "## CL3 Acceptance",
            "",
            "| Variant | Alpha sacrifice | MDD delta | Sharpe regression | Calmar regression | Overall |",
            "|---|---:|---:|---:|---:|:---:|",
        ]
    )
    for row in cl3.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['alpha_sacrifice'])} {'PASS' if row['alpha_pass'] else 'FAIL'} | "
            f"{_fmt_pct(row['mdd_delta'])} {'PASS' if row['mdd_pass'] else 'FAIL'} | "
            f"{_fmt_pct(row['sharpe_regression'])} {'PASS' if row['sharpe_pass'] else 'FAIL'} | "
            f"{_fmt_pct(row['calmar_regression'])} {'PASS' if row['calmar_pass'] else 'FAIL'} | "
            f"{'PASS' if row['overall_pass'] else 'FAIL'} |"
        )

    lines.extend(
        [
            "",
            "## Strong / Weak Month Subsets",
            "",
            "| Variant | Strong months | Strong month avg alpha | Weak months | Weak month MDD | Weak month avg return |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in subsets.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {row['strong_months']} | {_fmt_pct(row['strong_month_avg_alpha'])} | "
            f"{row['weak_months']} | {_fmt_pct(row['weak_month_mdd'])} | {_fmt_pct(row['weak_month_avg_return'])} |"
        )

    lines.extend(
        [
            "",
            "## 2026-04 Replay",
            "",
            "| Variant | April trades | Control MA5 exits | Rescued count | Rescued avg delta return |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in april_summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {row['april_trades']} | {row['control_ma5_exits']} | "
            f"{row['rescued_count']} | {_fmt_pct(row['rescued_avg_delta_return'])} |"
        )

    if not april_rescued.empty:
        lines.extend(
            [
                "",
                "### Top Rescued Positions",
                "",
                "| Variant | Ticker | Rank | Control exit | Control return | Variant exit | Variant return | Delta | Route |",
                "|---|---:|---:|---|---:|---|---:|---:|---|",
            ]
        )
        top_rows = april_rescued.sort_values("delta_return", ascending=False).head(20)
        for row in top_rows.to_dict("records"):
            lines.append(
                f"| {row['variant']} | {row['ticker']} | {int(row['two_stage_rank'])} | "
                f"{row['control_exit_date']} {row['control_exit_reason']} | {_fmt_pct(row['control_return'])} | "
                f"{row['variant_exit_date']} {row['variant_exit_reason']} | {_fmt_pct(row['variant_return'])} | "
                f"{_fmt_pct(row['delta_return'])} | {row['route_reason']} |"
            )

    passed = cl3[cl3["overall_pass"]]
    if passed.empty:
        verdict = "No variant passed all CL3 gates; regime overlay should remain research-only."
    else:
        verdict = "CL3-passing variants: " + ", ".join(passed["variant"].tolist())
    lines.extend(["", "## Verdict", "", verdict, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    fold = pd.read_csv(args.fold_artifact, dtype={"ticker": str})
    fold["month"] = fold["month"].astype(str)
    fold = fold[(fold["month"] >= args.start_month) & (fold["month"] <= args.end_month)].copy()
    if fold.empty:
        raise ValueError("No fold rows in requested window")

    twii_regime, twii_source = _load_twii_regime()
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    skipped_entries = 0

    for fold_row in fold.sort_values(["month", "rank"]).to_dict("records"):
        ticker = str(fold_row["ticker"]).zfill(4)
        prediction_date = str(fold_row.get("date") or fold_row.get("Date"))
        rank = int(fold_row["rank"])
        price_history = _load_price_history(ticker, price_cache)
        _, price_vs_ma20 = _signal_snapshot(price_history, prediction_date) if price_history is not None else (None, None)
        base_strong = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
        entry_date = _entry_date(price_history, prediction_date)
        twii_signal = get_twii_regime_signal(entry_date, twii_regime)

        trade_specs = [(CONTROL_VARIANT, REGIME_EXIT_VARIANT_NONE)]
        trade_specs.extend(VARIANTS)
        simulated_for_entry: list[dict[str, Any]] = []
        for variant_name, regime_variant in trade_specs:
            policy_name, disable_ma_break, route_reason = resolve_regime_exit_route(
                regime_variant,
                base_strong_trend_no_ma5=base_strong,
                twii_price_vs_ma20=twii_signal["twii_price_vs_ma20"],
                twii_return_20d=twii_signal["twii_return_20d"],
            )
            trade = _simulate_trade(
                ticker=ticker,
                prediction_date=prediction_date,
                month=str(fold_row["month"]),
                rank=rank,
                variant=variant_name,
                policy_name=policy_name,
                disable_ma_break_for_position=disable_ma_break,
                price_vs_ma20=price_vs_ma20,
                price_history=price_history,
                twii=twii_regime,
            )
            if trade is None:
                continue
            overlay_active = is_regime_exit_overlay_active(
                regime_variant,
                twii_signal["twii_price_vs_ma20"],
                twii_signal["twii_return_20d"],
            )
            trade.update(
                {
                    "regime_exit_variant": regime_variant,
                    "regime_route_reason": route_reason,
                    "regime_overlay_active": bool(overlay_active),
                    "twii_signal_date": twii_signal["twii_signal_date"],
                    "twii_price_vs_ma20_at_entry": twii_signal["twii_price_vs_ma20"],
                    "twii_return_20d_at_entry": twii_signal["twii_return_20d"],
                    "base_strong_trend_no_ma5": bool(base_strong),
                }
            )
            simulated_for_entry.append(trade)
        if len(simulated_for_entry) != len(trade_specs):
            skipped_entries += 1
            continue
        rows.extend(simulated_for_entry)

    trades = pd.DataFrame(rows)
    if trades.empty:
        raise ValueError("No simulated trades were produced")
    summary, monthly = _summarize(trades)
    route_counts = trades.groupby("variant")["regime_overlay_active"].sum().to_dict()
    summary["regime_overlay_count"] = summary["variant"].map(route_counts).fillna(0).astype(int)
    cl3 = _cl3_for_variants(summary)
    subsets = _subset_analysis(monthly, _twii_monthly_returns(twii_regime))
    april_summary, april_rescued = _april_replay(trades)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    paths = {
        "trades_csv": output_dir / f"{prefix}_trades.csv",
        "monthly_csv": output_dir / f"{prefix}_monthly.csv",
        "summary_csv": output_dir / f"{prefix}_summary.csv",
        "cl3_csv": output_dir / f"{prefix}_cl3.csv",
        "subsets_csv": output_dir / f"{prefix}_subsets.csv",
        "april_summary_csv": output_dir / f"{prefix}_april_replay_summary.csv",
        "april_rescued_csv": output_dir / f"{prefix}_april_rescued.csv",
        "json": output_dir / f"{prefix}.json",
        "md": output_dir / f"{prefix}.md",
    }
    trades.to_csv(paths["trades_csv"], index=False, encoding="utf-8-sig")
    monthly.to_csv(paths["monthly_csv"], index=False, encoding="utf-8-sig")
    summary.to_csv(paths["summary_csv"], index=False, encoding="utf-8-sig")
    cl3.to_csv(paths["cl3_csv"], index=False, encoding="utf-8-sig")
    subsets.to_csv(paths["subsets_csv"], index=False, encoding="utf-8-sig")
    april_summary.to_csv(paths["april_summary_csv"], index=False, encoding="utf-8-sig")
    april_rescued.to_csv(paths["april_rescued_csv"], index=False, encoding="utf-8-sig")

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fold_artifact": args.fold_artifact,
        "actual_start": str(fold["month"].min()),
        "actual_end": str(fold["month"].max()),
        "twii_source": twii_source,
        "entries": int(len(fold)),
        "simulated_trades": int(len(trades)),
        "skipped_entries": int(skipped_entries),
        **{key: str(value) for key, value in paths.items() if key not in {"json", "md"}},
    }
    payload = {
        "metadata": metadata,
        "summary": summary.to_dict("records"),
        "cl3": cl3.to_dict("records"),
        "subsets": subsets.to_dict("records"),
        "april_replay_summary": april_summary.to_dict("records"),
    }
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(
        paths["md"],
        summary=summary,
        cl3=cl3,
        subsets=subsets,
        april_summary=april_summary,
        april_rescued=april_rescued,
        metadata=metadata,
    )
    print(summary.to_string(index=False))
    print(cl3.to_string(index=False))
    print(f"[regime-exit-overlay-cl3] wrote {paths['md']}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-021 market-regime exit overlay CL3 backtest.")
    parser.add_argument(
        "--fold-artifact",
        default=str(Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"),
    )
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="regime_exit_overlay_cl3_20260509")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
