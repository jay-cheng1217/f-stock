"""Offline backtest for the short-wave entry overlay.

The backtest is deliberately file-based so it can run while stock.duckdb is
locked by the web app or schedulers.  It uses historical prediction CSV files
and daily OHLCV CSV files only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.entry_shortwave import compute_shortwave_overlay, summarize_shortwave_distribution  # noqa: E402

PRED_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
BUY_RECOMMENDATIONS = {"強力買進", "建議買進"}
TREATMENT_ACTIONS = {"NORMAL_ENTRY", "SMALL_ENTRY", "NO_CHASE"}
DEFAULT_START = "2026-05-01"
DEFAULT_HORIZON_DAYS = 5
DEFAULT_TOP_N = 10


@dataclass
class BasketMetrics:
    basket: str
    rows: int
    signal_days: int
    avg_return: float | None
    median_return: float | None
    win_rate: float | None
    zero_pick_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "basket": self.basket,
            "rows": self.rows,
            "signal_days": self.signal_days,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "win_rate": self.win_rate,
            "zero_pick_days": self.zero_pick_days,
        }


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.2f}%"


def _prediction_paths(start_date: str, end_date: str | None) -> list[Path]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp.max
    paths: list[tuple[pd.Timestamp, Path]] = []
    for path in Path(MODEL_DIR).glob("predictions_*.csv"):
        match = PRED_RE.match(path.name)
        if not match:
            continue
        dt = pd.Timestamp(match.group(1))
        if start <= dt <= end:
            paths.append((dt, path))
    return [path for _, path in sorted(paths)]


_PRICE_CACHE: dict[str, pd.DataFrame | None] = {}


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _add_minimal_shortwave_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = pd.to_numeric(out["Close"], errors="coerce")
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")
    out["MA_5"] = close.rolling(5, min_periods=5).mean()
    out["MA_20"] = close.rolling(20, min_periods=20).mean()
    out["MA_60"] = close.rolling(60, min_periods=60).mean()
    out["price_vs_ma5"] = close / out["MA_5"].replace(0, np.nan) - 1.0
    out["price_vs_ma20"] = close / out["MA_20"].replace(0, np.nan) - 1.0
    out["price_vs_ma60"] = close / out["MA_60"].replace(0, np.nan) - 1.0
    out["ma_slope_5"] = out["MA_5"].pct_change(5)
    out["ma_slope_20"] = out["MA_20"].pct_change(5)
    out["return_1d"] = close.pct_change(1)
    out["return_5d"] = close.pct_change(5)
    out["VOL_MA_20"] = volume.rolling(20, min_periods=20).mean()
    out["vol_ratio"] = volume / out["VOL_MA_20"].replace(0, np.nan)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = macd - signal
    out["macd_turn_positive"] = (out["macd_hist"].gt(0) & out["macd_hist"].shift(1).le(0)).astype(int)
    out["macd_bearish_div"] = 0
    out["macd_bullish_div"] = 0
    out["rsi_14"] = _rsi(close, 14)
    lowest = low.rolling(9, min_periods=9).min()
    highest = high.rolling(9, min_periods=9).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
    out["kd_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["kd_d"] = out["kd_k"].ewm(alpha=1 / 3, adjust=False).mean()
    return out


def _load_price_frame(ticker: str) -> pd.DataFrame | None:
    key = str(ticker).strip().zfill(4)
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    path = Path(DAILY_K_DIR) / f"{key}.csv"
    if not path.exists():
        _PRICE_CACHE[key] = None
        return None
    try:
        df = pd.read_csv(path, dtype={"Date": str})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        df = _add_minimal_shortwave_features(df)
    except Exception:
        _PRICE_CACHE[key] = None
        return None
    _PRICE_CACHE[key] = df
    return df


def _technical_row(ticker: str, signal_date: str) -> dict[str, Any]:
    df = _load_price_frame(ticker)
    if df is None or df.empty:
        return {}
    dt = pd.Timestamp(signal_date)
    subset = df[df["Date"] <= dt]
    if subset.empty:
        return {}
    row = subset.iloc[-1]
    cols = [
        "MA_5",
        "MA_20",
        "MA_60",
        "price_vs_ma5",
        "price_vs_ma20",
        "price_vs_ma60",
        "ma_slope_5",
        "ma_slope_20",
        "vol_ratio",
        "return_1d",
        "return_5d",
        "rsi_14",
        "macd_hist",
        "macd_turn_positive",
        "macd_bearish_div",
        "macd_bullish_div",
        "kd_k",
        "kd_d",
    ]
    return {col: row[col] for col in cols if col in row.index}


def _future_return(ticker: str, signal_date: str, horizon_days: int) -> dict[str, Any]:
    df = _load_price_frame(ticker)
    if df is None or df.empty:
        return {"has_return": False}
    dt = pd.Timestamp(signal_date)
    idx_values = df.index[df["Date"] <= dt].tolist()
    if not idx_values:
        return {"has_return": False}
    signal_idx = idx_values[-1]
    entry_idx = signal_idx + 1
    exit_idx = entry_idx + max(1, int(horizon_days)) - 1
    if entry_idx >= len(df) or exit_idx >= len(df):
        return {"has_return": False}
    entry_open = float(df.loc[entry_idx, "Open"])
    exit_close = float(df.loc[exit_idx, "Close"])
    if entry_open <= 0:
        return {"has_return": False}
    return {
        "has_return": True,
        "entry_date": str(pd.Timestamp(df.loc[entry_idx, "Date"]).date()),
        "exit_date": str(pd.Timestamp(df.loc[exit_idx, "Date"]).date()),
        "entry_open": entry_open,
        "exit_close": exit_close,
        "return": exit_close / entry_open - 1.0,
    }


def _read_prediction(path: Path) -> tuple[str, pd.DataFrame]:
    match = PRED_RE.match(path.name)
    if not match:
        raise ValueError(f"not a production prediction file: {path}")
    signal_date = match.group(1)
    df = pd.read_csv(path, dtype={"ticker": str})
    if df.empty:
        return signal_date, df
    df["ticker"] = df["ticker"].astype(str).str.strip().str.zfill(4)
    return signal_date, df


def _candidate_frame(path: Path) -> tuple[str, pd.DataFrame]:
    signal_date, pred = _read_prediction(path)
    if pred.empty:
        return signal_date, pred
    eligible = pred[pred.get("recommendation", "").isin(BUY_RECOMMENDATIONS)].copy()
    if eligible.empty:
        return signal_date, eligible

    technical_rows = [_technical_row(ticker, signal_date) for ticker in eligible["ticker"].tolist()]
    technical = pd.DataFrame(technical_rows, index=eligible.index)
    for col in technical.columns:
        eligible[col] = technical[col]

    overlay = compute_shortwave_overlay(eligible, snapshot=eligible, chipk_df=None, rerank_enabled=False)
    returns = [_future_return(ticker, signal_date, DEFAULT_HORIZON_DAYS) for ticker in overlay["ticker"].tolist()]
    ret_df = pd.DataFrame(returns, index=overlay.index)
    for col in ret_df.columns:
        overlay[col] = ret_df[col]
    overlay["signal_date"] = signal_date
    return signal_date, overlay


def _select_control(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    sort_cols = [col for col in ["leaderboard_score", "risk_adjusted_return", "pred_return_20d"] if col in frame.columns]
    if not sort_cols:
        return frame.head(top_n).copy()
    return frame.sort_values(sort_cols, ascending=[False] * len(sort_cols)).head(top_n).copy()


def _select_treatment(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    allowed = frame[frame["shortwave_action"].isin(TREATMENT_ACTIONS)].copy()
    if allowed.empty:
        return allowed
    sort_cols = ["shortwave_score"]
    ascending = [False]
    if "leaderboard_score" in allowed.columns:
        sort_cols.append("leaderboard_score")
        ascending.append(False)
    return allowed.sort_values(sort_cols, ascending=ascending).head(top_n).copy()


def _metrics(name: str, rows: pd.DataFrame, signal_days: int, zero_pick_days: int) -> BasketMetrics:
    usable = rows[rows.get("has_return", False).fillna(False).astype(bool)].copy() if not rows.empty else rows
    returns = pd.to_numeric(usable.get("return"), errors="coerce").dropna() if not usable.empty else pd.Series(dtype=float)
    if returns.empty:
        return BasketMetrics(name, 0, signal_days, None, None, None, zero_pick_days)
    return BasketMetrics(
        basket=name,
        rows=int(len(returns)),
        signal_days=int(signal_days),
        avg_return=float(returns.mean()),
        median_return=float(returns.median()),
        win_rate=float((returns > 0).mean()),
        zero_pick_days=int(zero_pick_days),
    )


def run_backtest(start_date: str, end_date: str | None, horizon_days: int, top_n: int) -> dict[str, Any]:
    global DEFAULT_HORIZON_DAYS
    DEFAULT_HORIZON_DAYS = int(horizon_days)

    paths = _prediction_paths(start_date, end_date)
    control_rows: list[pd.DataFrame] = []
    treatment_rows: list[pd.DataFrame] = []
    daily_summaries: list[dict[str, Any]] = []
    control_zero = 0
    treatment_zero = 0
    signal_days = 0

    for path in paths:
        signal_date, frame = _candidate_frame(path)
        signal_days += 1
        control = _select_control(frame, top_n)
        treatment = _select_treatment(frame, top_n)
        if control.empty:
            control_zero += 1
        if treatment.empty:
            treatment_zero += 1
        if not control.empty:
            control_rows.append(control.assign(basket="control"))
        if not treatment.empty:
            treatment_rows.append(treatment.assign(basket="shortwave_treatment"))
        daily_summaries.append(
            {
                "signal_date": signal_date,
                "eligible": int(len(frame)),
                "control": int(len(control)),
                "treatment": int(len(treatment)),
                "shortwave_distribution": summarize_shortwave_distribution(frame),
            }
        )

    control_df = pd.concat(control_rows, ignore_index=True) if control_rows else pd.DataFrame()
    treatment_df = pd.concat(treatment_rows, ignore_index=True) if treatment_rows else pd.DataFrame()
    control_metrics = _metrics("control_current_buy_topn", control_df, signal_days, control_zero)
    treatment_metrics = _metrics("shortwave_treatment_topn", treatment_df, signal_days, treatment_zero)

    failures: list[str] = []
    warnings: list[str] = []
    if signal_days < 5:
        failures.append("too_few_signal_days")
    if treatment_metrics.rows < max(20, int(control_metrics.rows * 0.5)):
        failures.append("treatment_sample_too_small")
    if treatment_metrics.avg_return is None or control_metrics.avg_return is None:
        failures.append("missing_return_metrics")
    elif treatment_metrics.avg_return <= 0:
        failures.append("treatment_avg_return_not_positive")
    elif treatment_metrics.avg_return < control_metrics.avg_return:
        failures.append("treatment_avg_return_below_control")
    if treatment_metrics.win_rate is not None and control_metrics.win_rate is not None:
        if treatment_metrics.win_rate + 0.02 < control_metrics.win_rate:
            failures.append("treatment_win_rate_materially_below_control")
    if treatment_zero > control_zero:
        warnings.append("treatment_has_more_zero_pick_days")

    production_decision = "hold"
    if not failures:
        production_decision = "eligible_for_shortwave_rerank_without_chipk"
        warnings.append("chipk_snapshot_has_no_historical_backtest_do_not_use_chipk_in_gate")

    report = {
        "status": "pass" if not failures else "fail",
        "production_decision": production_decision,
        "start_date": start_date,
        "end_date": end_date,
        "horizon_days": int(horizon_days),
        "top_n": int(top_n),
        "prediction_files": len(paths),
        "control": control_metrics.to_dict(),
        "treatment": treatment_metrics.to_dict(),
        "avg_return_delta": (
            None
            if control_metrics.avg_return is None or treatment_metrics.avg_return is None
            else treatment_metrics.avg_return - control_metrics.avg_return
        ),
        "failures": failures,
        "warnings": warnings,
        "daily": daily_summaries,
    }
    return report


def write_report(report: dict[str, Any], output_prefix: str) -> tuple[Path, Path]:
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{output_prefix}.json"
    md_path = out_dir / f"{output_prefix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# ChipK Shortwave Entry Backtest",
        "",
        f"- status: `{report['status']}`",
        f"- production_decision: `{report['production_decision']}`",
        f"- window: {report['start_date']} to {report.get('end_date') or 'latest'}",
        f"- horizon_days: {report['horizon_days']}",
        f"- top_n: {report['top_n']}",
        f"- prediction_files: {report['prediction_files']}",
        "",
        "| Basket | Rows | Signal days | Zero-pick days | Avg return | Median | Win rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ["control", "treatment"]:
        item = report[key]
        lines.append(
            f"| {item['basket']} | {item['rows']} | {item['signal_days']} | {item['zero_pick_days']} | "
            f"{_fmt_pct(item['avg_return'])} | {_fmt_pct(item['median_return'])} | {_fmt_pct(item['win_rate'])} |"
        )
    lines.extend(
        [
            "",
            f"- avg_return_delta: {_fmt_pct(report.get('avg_return_delta'))}",
            f"- failures: {', '.join(report['failures']) if report['failures'] else 'none'}",
            f"- warnings: {', '.join(report['warnings']) if report['warnings'] else 'none'}",
            "",
            "Note: current CMoney ChipK local file is a latest snapshot, not a historical series. "
            "This report does not allow ChipK snapshot values to alter production gates.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest shortwave entry overlay")
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--output-prefix", default="chipk_shortwave_entry_backtest_latest")
    args = parser.parse_args()

    report = run_backtest(args.start_date, args.end_date, args.horizon_days, args.top_n)
    md_path, json_path = write_report(report, args.output_prefix)
    print(f"status: {report['status']}")
    print(f"production_decision: {report['production_decision']}")
    print(f"control_avg: {_fmt_pct(report['control']['avg_return'])}")
    print(f"treatment_avg: {_fmt_pct(report['treatment']['avg_return'])}")
    print(f"delta: {_fmt_pct(report['avg_return_delta'])}")
    print(f"report_md: {md_path}")
    print(f"report_json: {json_path}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
