"""Independent validation for a strategy_profit_search result.

The validator intentionally re-implements the winning rule instead of importing
the search helper. It verifies selection, arithmetic, and target-label alignment
for the committed research artifact.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "ml" / "reports"

DEFAULT_SUMMARY = REPORT_DIR / "strategy_profit_search_20260610_2359.json"
DEFAULT_MONTHLY = REPORT_DIR / "strategy_profit_search_20260610_2359_best_monthly.csv"
DEFAULT_PICKS = REPORT_DIR / "strategy_profit_search_20260610_2359_best_picks.csv"
DEFAULT_RAW_K_DIR = ROOT / "日K資料"

EXPECTED_STRATEGY = "chip_momentum_high_top5_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all"
FRICTION = 0.004
HOLDOUT_START_MONTH = "2025-07"
TOP_N = 5
TOLERANCE = 1e-6

FEATURE_COLUMNS = [
    "Date",
    "ticker",
    "Open",
    "Close",
    "Volume",
    "AMOUNT_MA_20",
    "trade_return_20d",
    "trade_excess_return_20d",
    "inst_total_20d_norm",
    "foreign_cumsum_20d_norm",
    "trust_cumsum_20d_norm",
    "inst_buy_ratio_20d",
    "return_20d",
]
CHIP_COMPONENTS = [
    "inst_total_20d_norm",
    "foreign_cumsum_20d_norm",
    "trust_cumsum_20d_norm",
    "inst_buy_ratio_20d",
    "return_20d",
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def _curve_stats(monthly: pd.DataFrame) -> dict[str, Any]:
    equity = (1.0 + monthly["net_return"]).cumprod()
    excess_equity = (1.0 + monthly["net_excess_return"]).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "cum_return": float(equity.iloc[-1] - 1.0),
        "cum_excess": float(excess_equity.iloc[-1] - 1.0),
        "max_drawdown": float(drawdown.min()),
        "positive_months": int((monthly["net_return"] > 0).sum()),
        "months": int(len(monthly)),
        "avg_pick_win_rate": float(monthly["pick_win_rate"].mean()),
    }


def _build_monthly_frame(dataset_path: Path, start_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = pd.read_parquet(dataset_path, columns=FEATURE_COLUMNS)
    daily["Date"] = pd.to_datetime(daily["Date"])
    daily["ticker"] = daily["ticker"].astype(str)

    usable = daily[daily["Date"] >= pd.Timestamp(start_date)].dropna(subset=["trade_return_20d"]).copy()
    usable["month"] = usable["Date"].dt.to_period("M").astype(str)
    monthly = (
        usable.sort_values(["month", "ticker", "Date"])
        .groupby(["month", "ticker"], observed=True)
        .tail(1)
        .reset_index(drop=True)
    )
    return daily.sort_values(["ticker", "Date"]).reset_index(drop=True), monthly


def _select_chip_momentum(monthly: pd.DataFrame) -> pd.DataFrame:
    scored = monthly.copy()
    score = np.zeros(len(scored), dtype=float)
    for component in CHIP_COMPONENTS:
        score += (
            scored.groupby("month", observed=True)[component]
            .rank(pct=True, ascending=True)
            .fillna(0.5)
            .to_numpy()
        )
    scored["chip_momentum_verify"] = score / len(CHIP_COMPONENTS)
    eligible = scored[
        (scored["Open"] >= 10.0)
        & (scored["Volume"] >= 250_000.0)
        & (scored["AMOUNT_MA_20"] >= 20_000_000.0)
    ].copy()

    picks: list[pd.DataFrame] = []
    for month, group in eligible.groupby("month", observed=True):
        selected = (
            group.dropna(subset=["chip_momentum_verify", "trade_return_20d", "trade_excess_return_20d"])
            .sort_values(["chip_momentum_verify", "ticker"], ascending=[False, True])
            .head(TOP_N)
            .copy()
        )
        selected["selection_rank"] = np.arange(1, len(selected) + 1)
        picks.append(selected)
    out = pd.concat(picks, ignore_index=True)
    out["net_trade_return_20d"] = out["trade_return_20d"] - FRICTION
    out["net_trade_excess_return_20d"] = out["trade_excess_return_20d"] - FRICTION
    return out


def _monthly_from_picks(picks: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for month in months:
        selected = picks[picks["month"] == month]
        rows.append(
            {
                "month": month,
                "n_picks": int(len(selected)),
                "net_return": float(selected["net_trade_return_20d"].mean()),
                "net_excess_return": float(selected["net_trade_excess_return_20d"].mean()),
                "pick_win_rate": float((selected["net_trade_return_20d"] > 0).mean()),
                "avg_open": float(selected["Open"].mean()),
                "avg_volume": float(selected["Volume"].mean()),
                "avg_amount_ma20": float(selected["AMOUNT_MA_20"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _compare_picks(recomputed: pd.DataFrame, reported: pd.DataFrame) -> dict[str, Any]:
    key_cols = ["month", "selection_rank", "ticker"]
    left = recomputed[key_cols].sort_values(key_cols).reset_index(drop=True)
    right = reported[key_cols].assign(ticker=lambda x: x["ticker"].astype(str)).sort_values(key_cols).reset_index(drop=True)
    exact = left.equals(right)
    mismatches = []
    if not exact:
        merged = left.merge(right, on=key_cols, how="outer", indicator=True)
        mismatches = merged[merged["_merge"] != "both"].head(20).to_dict("records")
    return {
        "passed": exact,
        "recomputed_rows": int(len(left)),
        "reported_rows": int(len(right)),
        "mismatches_preview": mismatches,
    }


def _max_abs_diff(left: pd.Series, right: pd.Series) -> float:
    return float((pd.to_numeric(left, errors="coerce") - pd.to_numeric(right, errors="coerce")).abs().max())


def _compare_monthly(recomputed: pd.DataFrame, reported: pd.DataFrame) -> dict[str, Any]:
    joined = recomputed.merge(reported, on="month", suffixes=("_recomputed", "_reported"))
    diffs = {
        column: _max_abs_diff(joined[f"{column}_recomputed"], joined[f"{column}_reported"])
        for column in ["n_picks", "net_return", "net_excess_return", "pick_win_rate"]
    }
    return {
        "passed": all(value <= TOLERANCE for value in diffs.values()),
        "row_count_match": int(len(recomputed)) == int(len(reported)) == int(len(joined)),
        "max_abs_diffs": diffs,
    }


def _compare_summary(recomputed_monthly: pd.DataFrame, reported_summary: dict[str, Any]) -> dict[str, Any]:
    full = _curve_stats(recomputed_monthly)
    train = _curve_stats(recomputed_monthly[recomputed_monthly["month"] < HOLDOUT_START_MONTH])
    holdout = _curve_stats(recomputed_monthly[recomputed_monthly["month"] >= HOLDOUT_START_MONTH])
    best = reported_summary["best_strategy"]
    comparisons = {
        "full_cum_return_pct": (full["cum_return"] * 100.0, best["full_cum_return_pct"]),
        "full_cum_excess_pct": (full["cum_excess"] * 100.0, best["full_cum_excess_pct"]),
        "train_cum_return_pct": (train["cum_return"] * 100.0, best["train_cum_return_pct"]),
        "holdout_cum_return_pct": (holdout["cum_return"] * 100.0, best["holdout_cum_return_pct"]),
        "max_drawdown_pct": (full["max_drawdown"] * 100.0, best["max_drawdown_pct"]),
        "positive_months": (full["positive_months"], best["positive_months"]),
    }
    diffs = {key: abs(float(actual) - float(expected)) for key, (actual, expected) in comparisons.items()}
    return {
        "passed": all(value <= 1e-4 for value in diffs.values()),
        "recomputed": {"full": full, "train": train, "holdout": holdout},
        "diffs": diffs,
    }


def _load_raw_ticker(raw_k_dir: Path, ticker: str) -> pd.DataFrame | None:
    path = raw_k_dir / f"{ticker}.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, usecols=["Date", "Open", "Close"])
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.sort_values("Date").reset_index(drop=True)


def _target_group_for_row(
    *,
    daily_groups: dict[str, pd.DataFrame],
    raw_k_dir: Path,
    ticker: str,
    date: pd.Timestamp,
) -> tuple[pd.DataFrame | None, str]:
    group = daily_groups.get(ticker)
    if group is not None:
        matches = group.index[group["Date"] == date].tolist()
        if matches and matches[0] + 20 < len(group) and matches[0] + 1 < len(group):
            return group, "dataset_cache"

    raw = _load_raw_ticker(raw_k_dir, ticker)
    if raw is not None:
        return raw, "raw_k"
    return group, "dataset_cache"


def _verify_trade_targets(daily: pd.DataFrame, picks: pd.DataFrame, raw_k_dir: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    grouped = {ticker: group.reset_index(drop=True) for ticker, group in daily.groupby("ticker", observed=True)}
    for row in picks[["ticker", "Date", "trade_return_20d"]].itertuples(index=False):
        ticker = str(row.ticker)
        row_date = pd.Timestamp(row.Date)
        group, source = _target_group_for_row(
            daily_groups=grouped,
            raw_k_dir=raw_k_dir,
            ticker=ticker,
            date=row_date,
        )
        if group is None:
            checks.append({"ticker": ticker, "date": str(row.Date), "status": "missing_ticker"})
            continue
        matches = group.index[group["Date"] == row_date].tolist()
        if not matches:
            checks.append({"ticker": ticker, "date": str(row.Date), "status": "missing_date"})
            continue
        idx = matches[0]
        if idx + 20 >= len(group) or idx + 1 >= len(group):
            checks.append({"ticker": ticker, "date": str(row.Date), "status": "insufficient_forward_rows"})
            continue
        entry_open = float(group.loc[idx + 1, "Open"])
        exit_close = float(group.loc[idx + 20, "Close"])
        expected = exit_close / entry_open - 1.0 if entry_open else np.nan
        actual = float(row.trade_return_20d)
        checks.append(
            {
                "ticker": ticker,
                "date": pd.Timestamp(row.Date).date().isoformat(),
                "status": "ok",
                "source": source,
                "actual": actual,
                "expected": expected,
                "abs_diff": abs(actual - expected),
            }
        )
    ok = [item for item in checks if item["status"] == "ok"]
    bad = [item for item in checks if item["status"] != "ok" or item.get("abs_diff", 0.0) > TOLERANCE]
    return {
        "passed": not bad and len(ok) == len(picks),
        "checked_rows": int(len(checks)),
        "ok_rows": int(len(ok)),
        "max_abs_diff": max((item.get("abs_diff", 0.0) for item in ok), default=None),
        "sources": {
            str(key): int(value)
            for key, value in pd.Series([item.get("source", "unavailable") for item in ok])
            .value_counts()
            .items()
        },
        "failures_preview": bad[:20],
    }


def _write_markdown(path: Path, result: dict[str, Any]) -> None:
    full = result["summary_check"]["recomputed"]["full"]
    train = result["summary_check"]["recomputed"]["train"]
    holdout = result["summary_check"]["recomputed"]["holdout"]
    lines = [
        "# Strategy Profit Search Validation",
        "",
        "## Verdict",
        f"- Overall validation: `{'PASS' if result['passed'] else 'FAIL'}`",
        f"- Strategy: `{result['strategy']}`",
        f"- Recomputed full cumulative net return: `{_pct(full['cum_return'])}`",
        f"- Recomputed train cumulative net return: `{_pct(train['cum_return'])}`",
        f"- Recomputed holdout cumulative net return: `{_pct(holdout['cum_return'])}`",
        f"- Recomputed full cumulative net excess return: `{_pct(full['cum_excess'])}`",
        f"- Recomputed max drawdown: `{_pct(full['max_drawdown'])}`",
        "",
        "## Checks",
        f"- Independent picks match committed best picks: `{'PASS' if result['pick_check']['passed'] else 'FAIL'}`",
        f"- Monthly arithmetic matches committed monthly report: `{'PASS' if result['monthly_check']['passed'] else 'FAIL'}`",
        f"- Summary compounding matches committed summary JSON: `{'PASS' if result['summary_check']['passed'] else 'FAIL'}`",
        f"- Trade target formula Close[t+20] / Open[t+1] - 1 matches selected rows: `{'PASS' if result['target_check']['passed'] else 'FAIL'}`",
        f"- Liquidity/filter constraints pass: `{'PASS' if result['filter_check']['passed'] else 'FAIL'}`",
        "",
        "## Inputs",
        f"- Dataset: `{result['dataset_path']}`",
        f"- Summary JSON: `{result['summary_path']}`",
        f"- Monthly CSV: `{result['monthly_path']}`",
        f"- Picks CSV: `{result['picks_path']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate(args: argparse.Namespace) -> dict[str, Any]:
    summary_path = Path(args.summary).resolve()
    monthly_path = Path(args.monthly).resolve()
    picks_path = Path(args.picks).resolve()
    raw_k_dir = Path(args.raw_k_dir).resolve()
    summary = _load_json(summary_path)
    dataset_path = Path(summary["dataset_path"]).resolve()
    if summary["best_strategy"]["strategy"] != EXPECTED_STRATEGY:
        raise ValueError(f"Unexpected best strategy: {summary['best_strategy']['strategy']}")

    daily, monthly = _build_monthly_frame(dataset_path, summary["start_date"])
    recomputed_picks = _select_chip_momentum(monthly)
    months = sorted(monthly["month"].drop_duplicates())
    recomputed_monthly = _monthly_from_picks(recomputed_picks, months)

    reported_monthly = pd.read_csv(monthly_path)
    reported_picks = pd.read_csv(picks_path, dtype={"ticker": str})
    pick_check = _compare_picks(recomputed_picks, reported_picks)
    monthly_check = _compare_monthly(recomputed_monthly, reported_monthly)
    summary_check = _compare_summary(recomputed_monthly, summary)
    target_check = _verify_trade_targets(daily, recomputed_picks, raw_k_dir)
    filter_check = {
        "passed": bool(
            (recomputed_picks["Open"] >= 10.0).all()
            and (recomputed_picks["Volume"] >= 250_000.0).all()
            and (recomputed_picks["AMOUNT_MA_20"] >= 20_000_000.0).all()
            and (recomputed_picks.groupby("month", observed=True).size() == TOP_N).all()
        ),
        "months": int(recomputed_picks["month"].nunique()),
        "rows": int(len(recomputed_picks)),
        "min_open": float(recomputed_picks["Open"].min()),
        "min_volume": float(recomputed_picks["Volume"].min()),
        "min_amount_ma20": float(recomputed_picks["AMOUNT_MA_20"].min()),
    }

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": EXPECTED_STRATEGY,
        "dataset_path": str(dataset_path),
        "summary_path": str(summary_path),
        "monthly_path": str(monthly_path),
        "picks_path": str(picks_path),
        "raw_k_dir": str(raw_k_dir),
        "pick_check": pick_check,
        "monthly_check": monthly_check,
        "summary_check": summary_check,
        "target_check": target_check,
        "filter_check": filter_check,
    }
    result["passed"] = all(
        check["passed"]
        for check in [
            pick_check,
            monthly_check,
            summary_check,
            target_check,
            filter_check,
        ]
    )

    out_prefix = args.output_prefix or datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"strategy_profit_validation_{out_prefix}.json"
    md_path = REPORT_DIR / f"strategy_profit_validation_{out_prefix}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, result)
    print(f"[strategy-validation] passed={result['passed']}")
    print(f"[strategy-validation] full_cum={result['summary_check']['recomputed']['full']['cum_return'] * 100.0:.2f}%")
    print(f"[strategy-validation] holdout_cum={result['summary_check']['recomputed']['holdout']['cum_return'] * 100.0:.2f}%")
    print(f"[strategy-validation] report={md_path}")
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate committed strategy profit search artifacts.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--monthly", default=str(DEFAULT_MONTHLY))
    parser.add_argument("--picks", default=str(DEFAULT_PICKS))
    parser.add_argument("--raw-k-dir", default=str(DEFAULT_RAW_K_DIR))
    parser.add_argument("--output-prefix", default=None)
    return parser.parse_args()


def main() -> int:
    result = validate(_parse_args())
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
