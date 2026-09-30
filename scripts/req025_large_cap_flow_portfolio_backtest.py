"""REQ-025 large-cap institutional flow portfolio backtest.

Research-only portfolio replay for the >= 3bn TWD institutional-flow signal.
No production pipeline, model, or ledger code is modified.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from ml.features.sector import load_sector_mapping
from ml.universe import is_etf_ticker

DAILY_K_DIR = ROOT / "\u65e5K\u8cc7\u6599"
INDEX_DIR = ROOT / "\u5927\u76e4\u6307\u6578"
TWII_PATH = INDEX_DIR / "index_TWII.csv"
CHAMPION_MONTHLY_PATH = Path(REPORT_DIR) / "two_stage_cl3_backtest_20260509_monthly.csv"


@dataclass
class Position:
    ticker: str
    sector: str
    entry_date: pd.Timestamp
    entry_price: float
    high_close: float
    hold_days: int = 0
    foreign_sell_streak: int = 0


@dataclass
class ClosedTrade:
    ticker: str
    sector: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str
    hold_days: int


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
    df = df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)
    df["foreign_buy_3d"] = df["Foreign_BuySell"].gt(0).rolling(3).sum().eq(3)
    df["foreign_buy_3d_sum"] = df["Foreign_BuySell"].rolling(3).sum()
    df["flow_signal"] = (
        df["avg_20d_amount"].ge(3_000_000_000.0)
        & df["foreign_buy_3d"]
        & df["Trust_BuySell"].gt(0)
        & df["Close"].gt(df["MA_20"])
    )
    return df


def _load_price_panel(start: str, end: str) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    start_ts = pd.Timestamp(start) - pd.Timedelta(days=90)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=60)
    panel: dict[str, pd.DataFrame] = {}
    for path in sorted(DAILY_K_DIR.glob("*.csv")):
        ticker = path.stem.zfill(4) if path.stem.isdigit() else path.stem
        if not ticker.isdigit() or is_etf_ticker(ticker):
            continue
        df = _read_daily_k(path)
        if df is None or df.empty:
            continue
        df = df.loc[df["Date"].between(start_ts, end_ts)].copy()
        if df.empty:
            continue
        panel[ticker] = df.reset_index(drop=True)
    twii = _read_twii()
    twii = twii.loc[twii["Date"].between(start_ts, end_ts)].reset_index(drop=True)
    return panel, twii


def _date_calendar(panel: dict[str, pd.DataFrame], start: str, end: str) -> list[pd.Timestamp]:
    dates = sorted({date for df in panel.values() for date in df["Date"]})
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    return [date for date in dates if start_ts <= date <= end_ts]


def _row_on_or_before(df: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    rows = df.loc[df["Date"] <= date]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _exact_row(df: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    rows = df.loc[df["Date"].eq(date)]
    if rows.empty:
        return None
    return rows.iloc[0]


def _mdd(monthly_return: pd.Series) -> float:
    if monthly_return.empty:
        return math.nan
    equity = (1.0 + monthly_return.fillna(0.0)).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def _summary(monthly: pd.DataFrame) -> dict[str, Any]:
    ret = monthly["portfolio_ret"].fillna(0.0)
    alpha = monthly["alpha_vs_twii"].fillna(0.0)
    alpha_strategy_fit = (
        monthly["alpha_vs_strategy_fit"].fillna(0.0)
        if "alpha_vs_strategy_fit" in monthly.columns
        else pd.Series(dtype=float)
    )
    avg_ret = float(ret.mean()) if not ret.empty else math.nan
    avg_alpha = float(alpha.mean()) if not alpha.empty else math.nan
    std = float(ret.std(ddof=1)) if len(ret) > 1 else math.nan
    sharpe = avg_ret / std * math.sqrt(12) if std and math.isfinite(std) and std > 0 else math.nan
    mdd = _mdd(ret)
    calmar = avg_ret * 12 / abs(mdd) if mdd and math.isfinite(mdd) and mdd < 0 else math.nan
    return {
        "months": int(len(monthly)),
        "monthly_return": avg_ret,
        "monthly_alpha_vs_twii": avg_alpha,
        "monthly_alpha_vs_strategy_fit": float(alpha_strategy_fit.mean()) if not alpha_strategy_fit.empty else math.nan,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "positive_months": int((ret > 0).sum()),
        "avg_positions_count": float(monthly["positions_count"].mean()) if not monthly.empty else math.nan,
    }


def _twii_monthly(twii: pd.DataFrame, calendar: list[pd.Timestamp]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    by_month: dict[str, list[pd.Timestamp]] = {}
    for date in calendar:
        by_month.setdefault(date.to_period("M").strftime("%Y-%m"), []).append(date)
    for month, dates in by_month.items():
        start_row = _row_on_or_before(twii, dates[0])
        end_row = _row_on_or_before(twii, dates[-1])
        if start_row is None or end_row is None:
            twii_ret = math.nan
        else:
            start = _safe_float(start_row["Close"])
            end = _safe_float(end_row["Close"])
            twii_ret = end / start - 1.0 if start not in (None, 0) and end is not None else math.nan
        rows.append({"month": month, "twii_ret": twii_ret})
    return pd.DataFrame(rows)


def _champion_monthly() -> pd.DataFrame:
    if not CHAMPION_MONTHLY_PATH.exists():
        return pd.DataFrame(columns=["month", "champion_ret"])
    df = pd.read_csv(CHAMPION_MONTHLY_PATH, encoding="utf-8-sig")
    if {"month", "treatment_return"} <= set(df.columns):
        return df[["month", "treatment_return"]].rename(columns={"treatment_return": "champion_ret"})
    return pd.DataFrame(columns=["month", "champion_ret"])


def _strategy_fit_monthly(panel: dict[str, pd.DataFrame], calendar: list[pd.Timestamp]) -> pd.DataFrame:
    """Return a historical strategy-fit proxy using the loaded stock universe.

    REQ-023 uses prediction CSV unions for live weekly reports. Historical OOF
    months do not have daily production prediction CSVs, so this research report
    uses the same stock daily-K universe loaded for the replay as a stable
    equal-weight proxy for the model hunting ground.
    """

    if not panel or not calendar:
        return pd.DataFrame(columns=["month", "strategy_fit_ret"])
    calendar_set = set(calendar)
    frames: list[pd.DataFrame] = []
    for ticker, df in panel.items():
        if "Date" not in df.columns or "Close" not in df.columns:
            continue
        item = df[["Date", "Close"]].copy()
        item["ticker"] = ticker
        item["Close"] = pd.to_numeric(item["Close"], errors="coerce")
        item = item.dropna(subset=["Date", "Close"]).sort_values("Date")
        item["daily_return"] = item["Close"].pct_change()
        item = item[item["Date"].isin(calendar_set)]
        if not item.empty:
            frames.append(item[["Date", "ticker", "daily_return"]])
    if not frames:
        return pd.DataFrame(columns=["month", "strategy_fit_ret"])
    returns = pd.concat(frames, ignore_index=True)
    daily = returns.groupby("Date")["daily_return"].mean().dropna()
    if daily.empty:
        return pd.DataFrame(columns=["month", "strategy_fit_ret"])
    monthly = (
        daily.reset_index(name="daily_return")
        .assign(month=lambda d: d["Date"].dt.to_period("M").astype(str))
        .groupby("month")["daily_return"]
        .apply(lambda s: float((1.0 + s).prod() - 1.0))
        .reset_index(name="strategy_fit_ret")
    )
    return monthly


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    panel, twii = _load_price_panel(args.start, args.end)
    calendar = _date_calendar(panel, args.start, args.end)
    sector_df = load_sector_mapping()
    sector_lookup = {}
    if sector_df is not None:
        sector_lookup = dict(zip(sector_df["Ticker"].astype(str), sector_df["Sector"].astype(str)))

    positions: dict[str, Position] = {}
    closed: list[ClosedTrade] = []
    daily_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    equity = 1.0
    prev_prices: dict[str, float] = {}

    for date in calendar:
        active_returns: list[float] = []
        for ticker, pos in list(positions.items()):
            row = _exact_row(panel[ticker], date)
            if row is None:
                continue
            close = _safe_float(row["Close"])
            prev_close = prev_prices.get(ticker)
            if close is not None and prev_close not in (None, 0):
                active_returns.append(close / prev_close - 1.0)
            if close is not None:
                prev_prices[ticker] = close
                pos.high_close = max(pos.high_close, close)
                pos.hold_days += 1

        day_ret = sum(active_returns) / args.max_positions if active_returns else 0.0
        equity *= 1.0 + day_ret

        # Exits after mark-to-market for the day.
        for ticker, pos in list(positions.items()):
            row = _exact_row(panel[ticker], date)
            if row is None:
                continue
            close = _safe_float(row["Close"])
            foreign = _safe_float(row.get("Foreign_BuySell"))
            if close is None:
                continue
            if foreign is not None and foreign < 0:
                pos.foreign_sell_streak += 1
            elif foreign is not None:
                pos.foreign_sell_streak = 0
            exit_reason = ""
            if pos.foreign_sell_streak >= args.foreign_sell_days:
                exit_reason = f"FOREIGN_SELL_{args.foreign_sell_days}D"
            elif close <= pos.high_close * (1.0 - args.hwm_drawdown):
                exit_reason = "HWM_8PCT_TRAIL"
            elif pos.hold_days >= args.hold_days:
                exit_reason = "20D_TIMEOUT"
            if exit_reason:
                closed.append(
                    ClosedTrade(
                        ticker=ticker,
                        sector=pos.sector,
                        entry_date=str(pos.entry_date.date()),
                        exit_date=str(date.date()),
                        entry_price=pos.entry_price,
                        exit_price=close,
                        return_pct=close / pos.entry_price - 1.0,
                        exit_reason=exit_reason,
                        hold_days=pos.hold_days,
                    )
                )
                del positions[ticker]
                prev_prices.pop(ticker, None)

        sector_counts = {}
        for pos in positions.values():
            sector_counts[pos.sector] = sector_counts.get(pos.sector, 0) + 1

        candidates: list[dict[str, Any]] = []
        for ticker, df in panel.items():
            if ticker in positions:
                continue
            row = _exact_row(df, date)
            if row is None or not bool(row.get("flow_signal", False)):
                continue
            sector = sector_lookup.get(ticker, "其他")
            if sector_counts.get(sector, 0) >= args.max_per_sector:
                continue
            close = _safe_float(row["Close"])
            if close is None or close <= 0:
                continue
            candidates.append(
                {
                    "ticker": ticker,
                    "sector": sector,
                    "close": close,
                    "foreign_buy_3d_sum": _safe_float(row.get("foreign_buy_3d_sum")) or 0.0,
                    "trust_buy": _safe_float(row.get("Trust_BuySell")) or 0.0,
                    "avg_20d_amount": _safe_float(row.get("avg_20d_amount")) or 0.0,
                }
            )
        candidates = sorted(
            candidates,
            key=lambda item: (item["avg_20d_amount"], item["foreign_buy_3d_sum"], item["ticker"]),
            reverse=True,
        )
        for item in candidates:
            if len(positions) >= args.max_positions:
                break
            sector = item["sector"]
            if sector_counts.get(sector, 0) >= args.max_per_sector:
                continue
            ticker = item["ticker"]
            positions[ticker] = Position(
                ticker=ticker,
                sector=sector,
                entry_date=date,
                entry_price=item["close"],
                high_close=item["close"],
                hold_days=0,
            )
            prev_prices[ticker] = item["close"]
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            signal_rows.append({"date": str(date.date()), **item})

        daily_rows.append(
            {
                "date": str(date.date()),
                "equity": equity,
                "daily_return": day_ret,
                "positions_count": len(positions),
                "positions": ",".join(sorted(positions)),
            }
        )

    daily = pd.DataFrame(daily_rows)
    daily["month"] = pd.to_datetime(daily["date"]).dt.to_period("M").astype(str)
    monthly = (
        daily.groupby("month")
        .agg(
            first_equity=("equity", "first"),
            last_equity=("equity", "last"),
            positions_count=("positions_count", "mean"),
        )
        .reset_index()
    )
    monthly["portfolio_ret"] = monthly["last_equity"] / monthly["first_equity"] - 1.0
    monthly = monthly.merge(_twii_monthly(twii, calendar), on="month", how="left")
    monthly = monthly.merge(_strategy_fit_monthly(panel, calendar), on="month", how="left")
    monthly = monthly.merge(_champion_monthly(), on="month", how="left")
    monthly["alpha_vs_twii"] = monthly["portfolio_ret"] - monthly["twii_ret"]
    monthly["alpha_vs_strategy_fit"] = monthly["portfolio_ret"] - monthly["strategy_fit_ret"]
    monthly["alpha_vs_champion"] = monthly["portfolio_ret"] - monthly["champion_ret"]
    monthly = monthly[
        [
            "month",
            "portfolio_ret",
            "twii_ret",
            "alpha_vs_twii",
            "strategy_fit_ret",
            "alpha_vs_strategy_fit",
            "champion_ret",
            "alpha_vs_champion",
            "positions_count",
        ]
    ]

    closed_df = pd.DataFrame([asdict(item) for item in closed])
    as_of_date = calendar[-1] if calendar else pd.NaT
    open_positions = []
    for ticker, pos in sorted(positions.items()):
        current_price = prev_prices.get(ticker, pos.entry_price)
        open_positions.append(
            {
                "ticker": ticker,
                "sector": pos.sector,
                "entry_date": str(pos.entry_date.date()),
                "as_of_date": str(as_of_date.date()) if pd.notna(as_of_date) else "",
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "return_pct": current_price / pos.entry_price - 1.0 if pos.entry_price else math.nan,
                "hold_days": pos.hold_days,
                "status": "open",
            }
        )
    open_df = pd.DataFrame(open_positions)
    signals_df = pd.DataFrame(signal_rows)
    summary = _summary(monthly)
    signal_analysis = {
        "signals": int(len(signals_df)),
        "avg_monthly_signals": float(signals_df.assign(month=pd.to_datetime(signals_df["date"]).dt.to_period("M").astype(str)).groupby("month").size().mean()) if not signals_df.empty else 0.0,
        "top_tickers": signals_df["ticker"].value_counts().head(10).to_dict() if not signals_df.empty else {},
        "sector_distribution": signals_df["sector"].value_counts().to_dict() if not signals_df.empty else {},
        "exit_reasons": closed_df["exit_reason"].value_counts().to_dict() if not closed_df.empty else {},
    }

    april_start = pd.Timestamp("2026-04-01")
    april_end = pd.Timestamp("2026-05-08")
    april = closed_df.loc[
        pd.to_datetime(closed_df.get("entry_date", pd.Series(dtype=object)), errors="coerce").between(april_start, april_end)
    ].copy() if not closed_df.empty else pd.DataFrame()

    return {
        "daily": daily,
        "monthly": monthly,
        "closed_trades": closed_df,
        "open_positions": open_df,
        "signals": signals_df,
        "summary": summary,
        "signal_analysis": signal_analysis,
        "april_replay": april,
    }


def _fmt_pct(value: object) -> str:
    try:
        value = float(value)
    except Exception:
        return "n/a"
    if not math.isfinite(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def write_outputs(result: dict[str, Any], args: argparse.Namespace) -> Path:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix or f"req025_large_cap_flow_{datetime.now().strftime('%Y%m%d')}"
    paths = {
        "daily": report_dir / f"{prefix}_daily.csv",
        "monthly": report_dir / f"{prefix}_monthly.csv",
        "trades": report_dir / f"{prefix}_trades.csv",
        "signals": report_dir / f"{prefix}_signals.csv",
        "april": report_dir / f"{prefix}_april2026.csv",
        "json": report_dir / f"{prefix}.json",
        "md": report_dir / f"{prefix}.md",
    }
    result["daily"].to_csv(paths["daily"], index=False, encoding="utf-8-sig")
    result["monthly"].to_csv(paths["monthly"], index=False, encoding="utf-8-sig")
    result["closed_trades"].to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    result["signals"].to_csv(paths["signals"], index=False, encoding="utf-8-sig")
    result["april_replay"].to_csv(paths["april"], index=False, encoding="utf-8-sig")

    payload = {
        "summary": result["summary"],
        "signal_analysis": result["signal_analysis"],
        "paths": {key: str(value) for key, value in paths.items() if key != "md"},
        "method": {
            "entry": "T+0 close",
            "universe": "avg_20d_amount >= 3bn TWD on signal day",
            "signal": "foreign 3-day consecutive buy + trust same-direction buy + close > MA20",
            "portfolio": f"equal-weight mark-to-market using max {args.max_positions} slots; sector cap {args.max_per_sector} names",
            "exit": f"foreign consecutive sell >= {args.foreign_sell_days}D OR HWM -8% OR 20 trading days",
            "strategy_fit_benchmark": "ETF-excluded daily-K stock universe equal-weight proxy for historical OOF months",
        },
    }
    summary = result["summary"]
    april = result["april_replay"]
    april_focus = april.loc[april["ticker"].isin(["2330", "2454"])] if not april.empty and "ticker" in april.columns else pd.DataFrame()
    mainline_checks = {
        "monthly_alpha_vs_twii_gt_1pp": bool(summary["monthly_alpha_vs_twii"] > 0.01),
        "mdd_not_worse_than_minus_15pct": bool(summary["mdd"] >= -0.15),
        "calmar_gt_1": bool(summary["calmar"] > 1.0),
    }
    mainline_pass = all(mainline_checks.values())
    payload["decision_checks"] = mainline_checks | {"mainline_pass": mainline_pass}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# REQ-025 Large-Cap Institutional Flow Portfolio Backtest",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Monthly return | {_fmt_pct(summary['monthly_return'])} |",
        f"| Monthly alpha vs TWII | {_fmt_pct(summary['monthly_alpha_vs_twii'])} |",
        f"| Monthly alpha vs strategy-fit proxy | {_fmt_pct(summary['monthly_alpha_vs_strategy_fit'])} |",
        f"| MDD | {_fmt_pct(summary['mdd'])} |",
        f"| Sharpe | {summary['sharpe']:.3f} |",
        f"| Calmar | {summary['calmar']:.3f} |",
        f"| Positive months | {summary['positive_months']}/{summary['months']} |",
        f"| Avg positions | {summary['avg_positions_count']:.2f} |",
        f"| Mainline gate | {'PASS' if mainline_pass else 'FAIL'} |",
        "",
        "## Decision Checks",
        "",
        "| Gate | Result |",
        "|---|---:|",
        f"| Monthly alpha vs TWII > +1pp | {'PASS' if mainline_checks['monthly_alpha_vs_twii_gt_1pp'] else 'FAIL'} |",
        f"| MDD not worse than -15% | {'PASS' if mainline_checks['mdd_not_worse_than_minus_15pct'] else 'FAIL'} |",
        f"| Calmar > 1.0 | {'PASS' if mainline_checks['calmar_gt_1'] else 'FAIL'} |",
        "",
        "## Signal Analysis",
        "",
        f"- Signals: {result['signal_analysis']['signals']}",
        f"- Avg monthly signals: {result['signal_analysis']['avg_monthly_signals']:.2f}",
        f"- Top tickers: `{json.dumps(result['signal_analysis']['top_tickers'], ensure_ascii=False)}`",
        f"- Sector distribution: `{json.dumps(result['signal_analysis']['sector_distribution'], ensure_ascii=False)}`",
        f"- Exit reasons: `{json.dumps(result['signal_analysis']['exit_reasons'], ensure_ascii=False)}`",
        "- Strategy-fit benchmark: historical OOF daily-K stock-universe equal-weight proxy; live weekly reports use prediction CSV union.",
        "",
        "## April 2026 Replay",
        "",
        f"- Closed trades with entry 2026-04-01 to 2026-05-08: {len(april)}",
        f"- 2330/2454 trades: {len(april_focus)}",
    ]
    if not april_focus.empty:
        lines.extend(["", "| ticker | entry | exit | return | reason |", "|---|---|---|---:|---|"])
        for _, row in april_focus.iterrows():
            lines.append(
                f"| {row['ticker']} | {row['entry_date']} | {row['exit_date']} | {_fmt_pct(row['return_pct'])} | {row['exit_reason']} |"
            )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Monthly CSV: `{paths['monthly']}`",
            f"- Daily CSV: `{paths['daily']}`",
            f"- Trades CSV: `{paths['trades']}`",
            f"- Signals CSV: `{paths['signals']}`",
            f"- April replay CSV: `{paths['april']}`",
            f"- JSON: `{paths['json']}`",
        ]
    )
    paths["md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths["md"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-025 large-cap institutional flow portfolio backtest.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-30")
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--max-per-sector", type=int, default=2)
    parser.add_argument("--foreign-sell-days", type=int, default=1)
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--hwm-drawdown", type=float, default=0.08)
    parser.add_argument("--output-prefix", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_backtest(args)
    md_path = write_outputs(result, args)
    summary = result["summary"]
    print(f"[req025] wrote {md_path}")
    print(
        f"[req025] monthly_alpha={_fmt_pct(summary['monthly_alpha_vs_twii'])} "
        f"mdd={_fmt_pct(summary['mdd'])} calmar={summary['calmar']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
