"""DIAG-005 institutional-flow strategy diagnostic.

Research-only. This script evaluates a simple Taiwan institutional flow rule:

    foreign net buy for 3 consecutive trading days
    AND investment trust net buy on signal day
    AND close > MA20

It does not touch production models, signals, or ledgers.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR

DAILY_K_DIR = ROOT / "\u65e5K\u8cc7\u6599"
INDEX_DIR = ROOT / "\u5927\u76e4\u6307\u6578"
TWII_PATH = INDEX_DIR / "index_TWII.csv"
CHAMPION_SUMMARY_PATH = Path(REPORT_DIR) / "two_stage_cl3_backtest_20260509_summary.csv"


@dataclass(frozen=True)
class Trade:
    ticker: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    twii_return_pct: float
    alpha_pct: float
    exit_reason: str
    hold_days: int
    foreign_buy_3d_sum: float
    trust_buy_signal: float
    close_vs_ma20_signal: float
    avg_20d_amount_signal: float | None


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def _read_twii() -> pd.DataFrame:
    df = pd.read_csv(TWII_PATH, encoding="utf-8-sig")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    return df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)


def _twii_return(twii: pd.DataFrame, entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> float | None:
    entry = twii.loc[twii["Date"] <= entry_date]
    exit_ = twii.loc[twii["Date"] <= exit_date]
    if entry.empty or exit_.empty:
        return None
    start = _safe_float(entry.iloc[-1]["Close"])
    end = _safe_float(exit_.iloc[-1]["Close"])
    if start is None or end is None or start == 0:
        return None
    return end / start - 1.0


def _read_daily_k(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None
    required = {"Date", "Open", "Close", "Foreign_BuySell", "Trust_BuySell"}
    if not required <= set(df.columns):
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Open", "Close", "MA_20", "Foreign_BuySell", "Trust_BuySell", "VOL_MA_20"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "MA_20" not in df.columns:
        df["MA_20"] = df["Close"].rolling(20).mean()
    else:
        df["MA_20"] = df["MA_20"].fillna(df["Close"].rolling(20).mean())
    if "VOL_MA_20" in df.columns:
        df["avg_20d_amount"] = df["VOL_MA_20"] * df["Close"]
    else:
        df["avg_20d_amount"] = pd.NA
    return df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)


def _mdd(monthly_return: pd.Series) -> float:
    if monthly_return.empty:
        return math.nan
    equity = (1.0 + monthly_return.fillna(0.0)).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def _generate_trades(
    *,
    start: str,
    end: str,
    hold_days: int,
    exit_mode: str,
    max_tickers: int | None = None,
) -> list[Trade]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    twii = _read_twii()
    trades: list[Trade] = []
    files = sorted(DAILY_K_DIR.glob("*.csv"))
    if max_tickers:
        files = files[:max_tickers]

    for path in files:
        ticker = path.stem.zfill(4) if path.stem.isdigit() else path.stem
        if not ticker.isdigit():
            continue
        df = _read_daily_k(path)
        if df is None or len(df) < hold_days + 25:
            continue
        df["foreign_buy"] = df["Foreign_BuySell"].gt(0)
        df["foreign_buy_3d"] = df["foreign_buy"].rolling(3).sum().eq(3)
        df["foreign_buy_3d_sum"] = df["Foreign_BuySell"].rolling(3).sum()
        df["trust_same_direction"] = df["Trust_BuySell"].gt(0)
        df["close_gt_ma20"] = df["Close"].gt(df["MA_20"])
        signal = df["foreign_buy_3d"] & df["trust_same_direction"] & df["close_gt_ma20"]

        active_until_idx = -1
        for idx, row in df.loc[signal].iterrows():
            signal_date = row["Date"]
            if signal_date < start_ts or signal_date > end_ts:
                continue
            if idx <= active_until_idx:
                continue
            entry_idx = idx + 1
            max_exit_idx = entry_idx + hold_days - 1
            if entry_idx >= len(df) or max_exit_idx >= len(df):
                continue

            exit_idx = max_exit_idx
            exit_reason = f"{hold_days}D_TIMEOUT"
            if exit_mode == "foreign_flip":
                future = df.iloc[entry_idx : max_exit_idx + 1]
                flip = future.index[future["Foreign_BuySell"].lt(0)]
                if len(flip) > 0:
                    exit_idx = int(flip[0])
                    exit_reason = "FOREIGN_FLIP_SELL"

            entry_row = df.iloc[entry_idx]
            exit_row = df.iloc[exit_idx]
            entry_price = _safe_float(entry_row["Open"])
            exit_price = _safe_float(exit_row["Close"])
            if entry_price is None or exit_price is None or entry_price == 0:
                continue
            twii_ret = _twii_return(twii, entry_row["Date"], exit_row["Date"])
            if twii_ret is None:
                continue
            ret = exit_price / entry_price - 1.0
            alpha = ret - twii_ret
            ma20 = _safe_float(row.get("MA_20"))
            close = _safe_float(row.get("Close"))
            close_vs_ma20 = close / ma20 - 1.0 if close is not None and ma20 not in (None, 0) else math.nan
            trades.append(
                Trade(
                    ticker=ticker,
                    signal_date=str(signal_date.date()),
                    entry_date=str(entry_row["Date"].date()),
                    exit_date=str(exit_row["Date"].date()),
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                    return_pct=float(ret),
                    twii_return_pct=float(twii_ret),
                    alpha_pct=float(alpha),
                    exit_reason=exit_reason,
                    hold_days=int(exit_idx - entry_idx + 1),
                    foreign_buy_3d_sum=float(row["foreign_buy_3d_sum"]),
                    trust_buy_signal=float(row["Trust_BuySell"]),
                    close_vs_ma20_signal=float(close_vs_ma20),
                    avg_20d_amount_signal=_safe_float(row.get("avg_20d_amount")),
                )
            )
            active_until_idx = exit_idx
    return trades


def _monthly_stats(trades: pd.DataFrame, label: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return pd.DataFrame(), {
            "variant": label,
            "trades": 0,
            "months": 0,
            "monthly_return": math.nan,
            "monthly_twii": math.nan,
            "monthly_alpha": math.nan,
            "position_win_rate": math.nan,
            "position_beat_twii_rate": math.nan,
            "mdd": math.nan,
        }
    trades = trades.copy()
    trades["entry_month"] = pd.to_datetime(trades["entry_date"]).dt.to_period("M").astype(str)
    monthly = (
        trades.groupby("entry_month")
        .agg(
            trades=("ticker", "count"),
            return_pct=("return_pct", "mean"),
            twii_return_pct=("twii_return_pct", "mean"),
            alpha_pct=("alpha_pct", "mean"),
            win_rate=("return_pct", lambda s: float((s > 0).mean())),
            beat_twii_rate=("alpha_pct", lambda s: float((s > 0).mean())),
            avg_hold_days=("hold_days", "mean"),
        )
        .reset_index()
    )
    summary = {
        "variant": label,
        "trades": int(len(trades)),
        "months": int(len(monthly)),
        "monthly_return": float(monthly["return_pct"].mean()),
        "monthly_twii": float(monthly["twii_return_pct"].mean()),
        "monthly_alpha": float(monthly["alpha_pct"].mean()),
        "position_win_rate": float((trades["return_pct"] > 0).mean()),
        "position_beat_twii_rate": float((trades["alpha_pct"] > 0).mean()),
        "mdd": _mdd(monthly["return_pct"]),
        "avg_hold_days": float(trades["hold_days"].mean()),
    }
    return monthly, summary


def _liquidity_bucket_stats(trades: pd.DataFrame, label: str) -> pd.DataFrame:
    if trades.empty or "avg_20d_amount_signal" not in trades.columns:
        return pd.DataFrame()
    amount = pd.to_numeric(trades["avg_20d_amount_signal"], errors="coerce")
    rows: list[dict[str, Any]] = []
    for bucket, threshold in [
        ("all", 0.0),
        (">=100m", 100_000_000.0),
        (">=300m", 300_000_000.0),
        (">=500m", 500_000_000.0),
        (">=1bn", 1_000_000_000.0),
        (">=3bn", 3_000_000_000.0),
    ]:
        sub = trades.loc[amount.ge(threshold)].copy()
        rows.append(
            {
                "variant": label,
                "bucket": bucket,
                "threshold_amount": threshold,
                "trades": int(len(sub)),
                "avg_return": float(sub["return_pct"].mean()) if not sub.empty else math.nan,
                "avg_alpha": float(sub["alpha_pct"].mean()) if not sub.empty else math.nan,
                "win_rate": float((sub["return_pct"] > 0).mean()) if not sub.empty else math.nan,
                "beat_twii_rate": float((sub["alpha_pct"] > 0).mean()) if not sub.empty else math.nan,
            }
        )
    return pd.DataFrame(rows)


def _load_champion_reference() -> dict[str, Any]:
    if not CHAMPION_SUMMARY_PATH.exists():
        return {}
    try:
        df = pd.read_csv(CHAMPION_SUMMARY_PATH)
    except Exception:
        return {}
    if df.empty:
        return {}
    if "variant" in df.columns and df["variant"].astype(str).eq("Treatment_TwoStage_N75").any():
        row = df.loc[df["variant"].astype(str).eq("Treatment_TwoStage_N75")].iloc[0].to_dict()
    else:
        row = df.iloc[-1].to_dict()
    return {k: row.get(k) for k in row}


def _fmt_pct(value: object) -> str:
    try:
        value = float(value)
    except Exception:
        return "n/a"
    if not math.isfinite(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def _write_report(
    *,
    output_prefix: str,
    summaries: list[dict[str, Any]],
    monthly_paths: dict[str, str],
    trade_paths: dict[str, str],
    bucket_path: str,
    champion_reference: dict[str, Any],
    start: str,
    end: str,
) -> Path:
    report_dir = Path(REPORT_DIR)
    md_path = report_dir / f"{output_prefix}.md"
    json_path = report_dir / f"{output_prefix}.json"
    payload = {
        "start": start,
        "end": end,
        "summaries": summaries,
        "monthly_paths": monthly_paths,
        "trade_paths": trade_paths,
        "liquidity_bucket_path": bucket_path,
        "champion_reference": champion_reference,
        "method": {
            "rule": "Foreign_BuySell > 0 for 3 consecutive ticker trading days AND Trust_BuySell > 0 AND Close > MA20",
            "entry": "next trading day open",
            "primary_exit": "20 trading-day timeout",
            "secondary_exit": "foreign flip sell or 20 trading-day timeout",
            "maturity": "signals without full forward holding window are excluded",
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# DIAG-005 Institutional Flow Backtest",
        "",
        "## Rule",
        "",
        "- Foreign net buy for 3 consecutive ticker trading days",
        "- Investment trust net buy on signal day",
        "- Close above MA20 on signal day",
        "- Entry: next trading day open",
        "- Primary exit: 20 trading-day timeout",
        "- Secondary diagnostic exit: foreign net sell, otherwise 20D timeout",
        "- Immature signals without a full forward window are excluded.",
        "",
        "## Summary",
        "",
        "| Variant | Trades | Months | Monthly return | TWII same-window | Monthly alpha | Position win rate | Beat TWII rate | MDD | Avg hold days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['variant']} | {item['trades']} | {item['months']} | "
            f"{_fmt_pct(item['monthly_return'])} | {_fmt_pct(item['monthly_twii'])} | "
            f"{_fmt_pct(item['monthly_alpha'])} | {_fmt_pct(item['position_win_rate'])} | "
            f"{_fmt_pct(item['position_beat_twii_rate'])} | {_fmt_pct(item['mdd'])} | "
            f"{item.get('avg_hold_days', math.nan):.2f} |"
        )
    if champion_reference:
        lines.extend(
            [
                "",
                "## Champion Reference",
                "",
                "Existing Two-Stage CL3 summary is included for context only; this DIAG is not a production proposal.",
                "",
                f"- Source: `{CHAMPION_SUMMARY_PATH}`",
            ]
        )
        for key in ["monthly_alpha", "mdd", "sharpe", "calmar"]:
            if key in champion_reference:
                value = champion_reference[key]
                text = _fmt_pct(value) if key in {"monthly_alpha", "mdd"} else str(value)
                lines.append(f"- {key}: {text}")
    lines.extend(
        [
            "",
            "## Liquidity Bucket Read",
            "",
            "The full-universe rule is diluted by many small/mid names. "
            "Use the bucket CSV to judge whether the signal has edge specifically in large-ticket names.",
        ]
    )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- JSON: `{json_path}`",
            f"- Liquidity bucket CSV: `{bucket_path}`",
        ]
    )
    for label, path in monthly_paths.items():
        lines.append(f"- {label} monthly CSV: `{path}`")
    for label, path in trade_paths.items():
        lines.append(f"- {label} trades CSV: `{path}`")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def run(args: argparse.Namespace) -> dict[str, Any]:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix or f"diag005_institutional_flow_{datetime.now().strftime('%Y%m%d')}"
    variants = {
        "fixed20": "fixed20",
        "foreign_flip": "foreign_flip",
    }
    summaries: list[dict[str, Any]] = []
    monthly_paths: dict[str, str] = {}
    trade_paths: dict[str, str] = {}
    bucket_frames: list[pd.DataFrame] = []

    for label, mode in variants.items():
        trades = _generate_trades(
            start=args.start,
            end=args.end,
            hold_days=args.hold_days,
            exit_mode=mode,
            max_tickers=args.max_tickers,
        )
        trades_df = pd.DataFrame([trade.__dict__ for trade in trades])
        monthly, summary = _monthly_stats(trades_df, label)
        summaries.append(summary)
        trade_path = report_dir / f"{prefix}_{label}_trades.csv"
        monthly_path = report_dir / f"{prefix}_{label}_monthly.csv"
        trades_df.to_csv(trade_path, index=False, encoding="utf-8-sig")
        monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
        bucket_frames.append(_liquidity_bucket_stats(trades_df, label))
        trade_paths[label] = str(trade_path)
        monthly_paths[label] = str(monthly_path)

    champion_reference = _load_champion_reference()
    bucket_df = pd.concat(bucket_frames, ignore_index=True) if bucket_frames else pd.DataFrame()
    bucket_path = report_dir / f"{prefix}_liquidity_buckets.csv"
    bucket_df.to_csv(bucket_path, index=False, encoding="utf-8-sig")
    md_path = _write_report(
        output_prefix=prefix,
        summaries=summaries,
        monthly_paths=monthly_paths,
        trade_paths=trade_paths,
        bucket_path=str(bucket_path),
        champion_reference=champion_reference,
        start=args.start,
        end=args.end,
    )
    print(f"[diag005] wrote {md_path}")
    for summary in summaries:
        print(
            f"[diag005] {summary['variant']} trades={summary['trades']} "
            f"monthly_alpha={_fmt_pct(summary['monthly_alpha'])} "
            f"win_rate={_fmt_pct(summary['position_win_rate'])}"
        )
    return {"md_path": str(md_path), "summaries": summaries}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DIAG-005 institutional flow backtest.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-30")
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--max-tickers", type=int, default=0, help="debug limit; 0 means all")
    parser.add_argument("--output-prefix", default="")
    args = parser.parse_args()
    if args.max_tickers <= 0:
        args.max_tickers = None
    return args


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
