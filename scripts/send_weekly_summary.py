"""Render the weekly Champion summary with dual benchmarks.

The report intentionally separates:
- TWII: opportunity cost versus the market-cap weighted index.
- Strategy-fit: equal-weight return of the model's own V2 alpha universe.
- Residual: TWII minus strategy-fit, mainly the large-cap exposure gap.

This script only changes report output. It does not touch model inference,
guardrails, paper-book syncing, or production portfolio state.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import INDEX_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.universe import filter_out_etfs_df  # noqa: E402

DEFAULT_STOCK_DB_PATH = BASE_DIR / "stock.duckdb"
DEFAULT_CHAMPION_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
PREDICTION_FILE_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
REPORT_NOTE = "Residual 主要反映大市值半導體 exposure gap，非系統缺陷。"
STRUCTURAL_BEHAVIOR_NOTE = (
    "大市值主導型牛市期間，Champion 系統可能出現現金等待期"
    "（中小型股流動性不足），入場點通常落在多頭擴散後。"
    "此現象為策略結構性特徵，非流動性門檻設定錯誤。"
)


@dataclass(frozen=True)
class ReturnMetric:
    return_pct: float | None
    start_date: str
    end_date: str
    observations: int
    source: str
    method: str
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "return_pct": self.return_pct,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "observations": self.observations,
            "source": self.source,
            "method": self.method,
            **self.extra,
        }


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _coerce_date(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value).normalize()
    if pd.isna(result):
        raise ValueError(f"Invalid date: {value}")
    return result


def _prediction_paths_between(start_date: str, end_date: str, model_dir: str | Path = MODEL_DIR) -> list[Path]:
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    paths: list[tuple[pd.Timestamp, Path]] = []
    for raw in glob.glob(str(Path(model_dir) / "predictions_*.csv")):
        path = Path(raw)
        match = PREDICTION_FILE_RE.match(path.name)
        if not match:
            continue
        date_value = _coerce_date(match.group(1))
        if start <= date_value <= end:
            paths.append((date_value, path))
    return [path for _, path in sorted(paths)]


def _load_prediction_universe(
    start_date: str,
    end_date: str,
    *,
    model_dir: str | Path = MODEL_DIR,
) -> tuple[list[str], list[str]]:
    paths = _prediction_paths_between(start_date, end_date, model_dir)
    tickers: set[str] = set()
    prediction_days: list[str] = []
    for path in paths:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
        if "ticker" not in df.columns:
            continue
        df = filter_out_etfs_df(df)
        normalized = (
            df["ticker"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
        )
        tickers.update(ticker for ticker in normalized if ticker)
        match = PREDICTION_FILE_RE.match(path.name)
        prediction_days.append(match.group(1) if match else path.stem)
    return sorted(tickers), prediction_days


def _compound_return(daily_returns: pd.Series) -> float | None:
    daily = pd.to_numeric(daily_returns, errors="coerce").dropna()
    if daily.empty:
        return None
    return float((1.0 + daily).prod() - 1.0)


def _twii_trading_dates(
    start_date: str,
    end_date: str,
    *,
    index_path: str | Path = Path(INDEX_DIR) / "index_TWII.csv",
    include_start: bool = False,
) -> list[pd.Timestamp]:
    path = Path(index_path)
    if not path.exists():
        return []
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    df = pd.read_csv(path, encoding="utf-8-sig")
    date_col = "Date" if "Date" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    if include_start:
        mask = (df[date_col] >= start) & (df[date_col] <= end)
    else:
        mask = (df[date_col] > start) & (df[date_col] <= end)
    return [pd.Timestamp(value).normalize() for value in df.loc[mask, date_col]]


def compute_strategy_fit_benchmark(
    start_date: str,
    end_date: str,
    *,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
) -> dict[str, Any]:
    """Return equal-weight V2 alpha-universe benchmark return for a period.

    The universe is the union of all production prediction CSV tickers in the
    window. Daily benchmark return is the equal-weight close-to-close return of
    available universe constituents after ``start_date``; the period return is
    daily compounded from start close to end close.
    """

    tickers, prediction_days = _load_prediction_universe(start_date, end_date, model_dir=model_dir)
    if not tickers:
        return ReturnMetric(
            return_pct=None,
            start_date=start_date,
            end_date=end_date,
            observations=0,
            source="prediction_csv_union",
            method="daily equal-weight start-close to end-close compounded",
            extra={"universe_size": 0, "prediction_days": prediction_days, "daily_return_days": 0},
        ).to_dict()

    import duckdb

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    lookback_start = start - pd.Timedelta(days=14)
    stock_db = Path(stock_db_path)
    if not stock_db.exists():
        raise FileNotFoundError(f"DuckDB not found: {stock_db}")

    con = duckdb.connect(str(stock_db), read_only=True)
    try:
        con.register("ticker_filter", pd.DataFrame({"Ticker": tickers}))
        prices = con.execute(
            """
            SELECT d.Ticker, d.Date, d.Close
            FROM daily_k d
            JOIN ticker_filter f ON d.Ticker = f.Ticker
            WHERE d.Date BETWEEN ? AND ?
              AND d.Close IS NOT NULL
            ORDER BY d.Ticker, d.Date
            """,
            [lookback_start.date(), end.date()],
        ).fetchdf()
    finally:
        con.close()

    if prices.empty:
        daily_returns = pd.Series(dtype=float)
    else:
        prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
        prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
        prices = prices.dropna(subset=["Date", "Close"]).sort_values(["Ticker", "Date"])
        prices["daily_return"] = prices.groupby("Ticker")["Close"].pct_change()
        in_window = prices[(prices["Date"] > start) & (prices["Date"] <= end)].copy()
        daily_returns = in_window.groupby("Date")["daily_return"].mean().dropna()

    result = ReturnMetric(
        return_pct=_compound_return(daily_returns),
        start_date=start_date,
        end_date=end_date,
        observations=int(prices["Ticker"].nunique()) if not prices.empty else 0,
        source=str(stock_db),
        method="prediction-union daily equal-weight start-close to end-close compounded",
        extra={
            "universe_size": len(tickers),
            "prediction_days": prediction_days,
            "daily_return_days": int(daily_returns.shape[0]),
            "daily_observations": int(prices.shape[0]) if not prices.empty else 0,
        },
    )
    return result.to_dict()


def compute_twii_benchmark(
    start_date: str,
    end_date: str,
    *,
    index_path: str | Path = Path(INDEX_DIR) / "index_TWII.csv",
) -> dict[str, Any]:
    """Return TWII start-close to end-close compounded return for the period."""

    path = Path(index_path)
    if not path.exists():
        return ReturnMetric(
            return_pct=None,
            start_date=start_date,
            end_date=end_date,
            observations=0,
            source=str(path),
            method="TWII daily start-close to end-close compounded",
            extra={"daily_return_days": 0},
        ).to_dict()

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    lookback_start = start - pd.Timedelta(days=14)
    df = pd.read_csv(path, encoding="utf-8-sig")
    date_col = "Date" if "Date" in df.columns else "date"
    close_col = "Close" if "Close" in df.columns else "close"
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
    df = df.dropna(subset=[date_col, close_col]).sort_values(date_col)
    df = df[(df[date_col] >= lookback_start) & (df[date_col] <= end)].copy()
    df["daily_return"] = df[close_col].pct_change()
    daily_returns = df[(df[date_col] > start) & (df[date_col] <= end)]["daily_return"].dropna()

    return ReturnMetric(
        return_pct=_compound_return(daily_returns),
        start_date=start_date,
        end_date=end_date,
        observations=int(df.shape[0]),
        source=str(path),
        method="TWII daily start-close to end-close compounded",
        extra={"daily_return_days": int(daily_returns.shape[0])},
    ).to_dict()


def compute_champion_period_return(
    start_date: str,
    end_date: str,
    *,
    champion_db_path: str | Path = DEFAULT_CHAMPION_DB_PATH,
    index_path: str | Path = Path(INDEX_DIR) / "index_TWII.csv",
) -> dict[str, Any]:
    """Compute target-weight-normalized Champion mark-to-market return.

    Marks store cumulative return from each position's entry. For each mark day,
    this function converts cumulative position returns into daily deltas and
    target-weight-normalizes active marked positions.

    Missing trading days are then filled as 0% cash return so the report exposes
    continuous NAV coverage. This matters for early windows where the Champion
    paper book had no positions before its first deployed signal.
    """

    path = Path(champion_db_path)
    if not path.exists():
        return ReturnMetric(
            return_pct=None,
            start_date=start_date,
            end_date=end_date,
            observations=0,
            source=str(path),
            method="Champion target-weight-normalized mark daily compounded",
            extra={"daily_return_days": 0, "positions": 0},
        ).to_dict()

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    with sqlite3.connect(path) as con:
        marks = pd.read_sql_query(
            """
            SELECT
                p.id AS position_id,
                p.ticker,
                p.target_weight,
                m.mark_date,
                m.close_return_pct
            FROM unified_marks m
            JOIN unified_positions p ON p.id = m.position_id
            WHERE m.mark_date <= ?
            ORDER BY p.id, m.mark_date
            """,
            con,
            params=(end_date,),
        )

    calendar = _twii_trading_dates(start_date, end_date, index_path=index_path, include_start=False)

    if marks.empty:
        marked_book = pd.Series(dtype=float)
        positions = 0
    else:
        marks["mark_date"] = pd.to_datetime(marks["mark_date"], errors="coerce")
        marks["target_weight"] = pd.to_numeric(marks["target_weight"], errors="coerce").fillna(0.0)
        marks["close_return_pct"] = pd.to_numeric(marks["close_return_pct"], errors="coerce").fillna(0.0)
        marks = marks.dropna(subset=["mark_date"]).sort_values(["position_id", "mark_date"])
        marks["prev_return"] = marks.groupby("position_id")["close_return_pct"].shift(1).fillna(0.0)
        marks["daily_position_return"] = marks["close_return_pct"] - marks["prev_return"]
        in_window = marks[(marks["mark_date"] > start) & (marks["mark_date"] <= end)].copy()
        positions = int(in_window["position_id"].nunique())
        if in_window.empty:
            marked_book = pd.Series(dtype=float)
        else:
            in_window["effective_weight"] = in_window["target_weight"].clip(lower=0.0)
            in_window["weighted_return"] = (
                in_window["daily_position_return"] * in_window["effective_weight"]
            )
            grouped = in_window.groupby("mark_date").agg(
                weighted_return=("weighted_return", "sum"),
                effective_weight=("effective_weight", "sum"),
                fallback_return=("daily_position_return", "mean"),
            )
            marked_book = grouped["weighted_return"] / grouped["effective_weight"]
            zero_weight = grouped["effective_weight"] <= 0
            marked_book.loc[zero_weight] = grouped.loc[zero_weight, "fallback_return"]

    marked_book.index = pd.to_datetime(marked_book.index).normalize()
    if calendar:
        daily_book = marked_book.reindex(pd.DatetimeIndex(calendar), fill_value=0.0)
    else:
        daily_book = marked_book
    marked_return_days = int(marked_book.dropna().shape[0])
    cash_filled_days = max(0, int(daily_book.shape[0]) - marked_return_days)

    return ReturnMetric(
        return_pct=_compound_return(daily_book),
        start_date=start_date,
        end_date=end_date,
        observations=positions,
        source=str(path),
        method="Champion target-weight-normalized mark daily compounded",
        extra={
            "daily_return_days": int(daily_book.shape[0]),
            "marked_return_days": marked_return_days,
            "cash_filled_days": cash_filled_days,
            "positions": positions,
        },
    ).to_dict()


def compute_dual_benchmark_summary(
    start_date: str,
    end_date: str,
    *,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
    champion_db_path: str | Path = DEFAULT_CHAMPION_DB_PATH,
) -> dict[str, Any]:
    strategy_fit = compute_strategy_fit_benchmark(
        start_date,
        end_date,
        stock_db_path=stock_db_path,
        model_dir=model_dir,
    )
    twii = compute_twii_benchmark(start_date, end_date)
    champion = compute_champion_period_return(start_date, end_date, champion_db_path=champion_db_path)

    strategy_return = strategy_fit.get("return_pct")
    twii_return = twii.get("return_pct")
    champion_return = champion.get("return_pct")
    residual = (
        float(twii_return) - float(strategy_return)
        if twii_return is not None and strategy_return is not None
        else None
    )
    champion_alpha = (
        float(champion_return) - float(strategy_return)
        if champion_return is not None and strategy_return is not None
        else None
    )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": REPORT_NOTE,
        "structural_behavior_note": STRUCTURAL_BEHAVIOR_NOTE,
        "twii": twii,
        "strategy_fit": strategy_fit,
        "champion": champion,
        "residual_pct": residual,
        "champion_alpha_vs_strategy_fit_pct": champion_alpha,
    }


def build_weekly_summary_text(summary: dict[str, Any]) -> str:
    lines = [
        f"Champion 雙 Benchmark 週報 ({summary['start_date']} → {summary['end_date']})",
        REPORT_NOTE,
        STRUCTURAL_BEHAVIOR_NOTE,
        "",
        f"TWII         : {_fmt_pct(summary['twii'].get('return_pct'))}  （機會成本）",
        f"Strategy-fit : {_fmt_pct(summary['strategy_fit'].get('return_pct'))}  （模型獵場等權）",
        f"Residual     : {_fmt_pct(summary.get('residual_pct'))}  （大市值 exposure gap）",
        "",
        (
            "Champion alpha: "
            f"{_fmt_pct(summary.get('champion_alpha_vs_strategy_fit_pct'))} vs strategy-fit"
        ),
        "",
        (
            "Strategy-fit universe: "
            f"{summary['strategy_fit'].get('universe_size', 0)} tickers / "
            f"{len(summary['strategy_fit'].get('prediction_days', []))} prediction days"
        ),
        (
            "Champion sample: "
            f"{summary['champion'].get('positions', 0)} positions / "
            f"{summary['champion'].get('daily_return_days', 0)} NAV days "
            f"({summary['champion'].get('marked_return_days', 0)} marked / "
            f"{summary['champion'].get('cash_filled_days', 0)} cash)"
        ),
    ]
    return "\n".join(lines)


def build_weekly_summary_html(summary: dict[str, Any]) -> str:
    rows = [
        ("TWII", _fmt_pct(summary["twii"].get("return_pct")), "機會成本"),
        ("Strategy-fit", _fmt_pct(summary["strategy_fit"].get("return_pct")), "模型獵場等權"),
        ("Residual", _fmt_pct(summary.get("residual_pct")), "大市值 exposure gap"),
        (
            "Champion alpha",
            _fmt_pct(summary.get("champion_alpha_vs_strategy_fit_pct")),
            "vs strategy-fit",
        ),
    ]
    table_rows = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td><td>{html.escape(note)}</td></tr>"
        for label, value, note in rows
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>Champion 雙 Benchmark 週報</title>
  <style>
    body {{ font-family: "Microsoft JhengHei", Arial, sans-serif; color: #172033; margin: 24px; }}
    .note {{ color: #475569; font-size: 14px; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; min-width: 520px; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; padding: 10px 12px; text-align: left; }}
    th {{ width: 150px; color: #334155; }}
    td:nth-child(2) {{ font-weight: 700; }}
    .meta {{ color: #64748b; font-size: 12px; margin-top: 16px; line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>Champion 雙 Benchmark 週報</h1>
  <div class="note">
    {html.escape(summary['start_date'])} → {html.escape(summary['end_date'])}。{html.escape(REPORT_NOTE)}<br>
    {html.escape(STRUCTURAL_BEHAVIOR_NOTE)}
  </div>
  <table>
    <tbody>
      {table_rows}
    </tbody>
  </table>
    <div class="meta">
    Strategy-fit universe: {html.escape(str(summary['strategy_fit'].get('universe_size', 0)))} tickers /
    {html.escape(str(len(summary['strategy_fit'].get('prediction_days', []))))} prediction days<br>
    Champion sample: {html.escape(str(summary['champion'].get('positions', 0)))} positions /
    {html.escape(str(summary['champion'].get('daily_return_days', 0)))} NAV days
    ({html.escape(str(summary['champion'].get('marked_return_days', 0)))} marked /
    {html.escape(str(summary['champion'].get('cash_filled_days', 0)))} cash)<br>
    Generated at: {html.escape(str(summary.get('generated_at', '-')))}
  </div>
</body>
</html>
"""


def render_weekly_summary(
    start_date: str,
    end_date: str,
    *,
    report_dir: str | Path = REPORT_DIR,
    stock_db_path: str | Path = DEFAULT_STOCK_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
    champion_db_path: str | Path = DEFAULT_CHAMPION_DB_PATH,
) -> dict[str, Any]:
    summary = compute_dual_benchmark_summary(
        start_date,
        end_date,
        stock_db_path=stock_db_path,
        model_dir=model_dir,
        champion_db_path=champion_db_path,
    )
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start_key = start_date.replace("-", "")
    end_key = end_date.replace("-", "")
    stem = f"weekly_dual_benchmark_{start_key}_{end_key}"
    text_path = out_dir / f"{stem}.txt"
    html_path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"

    text_body = build_weekly_summary_text(summary)
    html_body = build_weekly_summary_html(summary)
    text_path.write_text(text_body, encoding="utf-8")
    html_path.write_text(html_body, encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary": summary,
        "text_path": str(text_path),
        "html_path": str(html_path),
        "json_path": str(json_path),
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Render weekly Champion dual-benchmark summary.")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--report-dir", default=REPORT_DIR)
    parser.add_argument("--stock-db-path", default=str(DEFAULT_STOCK_DB_PATH))
    parser.add_argument("--champion-db-path", default=str(DEFAULT_CHAMPION_DB_PATH))
    args = parser.parse_args(argv)

    result = render_weekly_summary(
        args.start,
        args.end,
        report_dir=args.report_dir,
        stock_db_path=args.stock_db_path,
        champion_db_path=args.champion_db_path,
    )
    summary = result["summary"]
    print(
        "[weekly-summary] "
        f"twii={_fmt_pct(summary['twii'].get('return_pct'))} "
        f"strategy_fit={_fmt_pct(summary['strategy_fit'].get('return_pct'))} "
        f"residual={_fmt_pct(summary.get('residual_pct'))} "
        f"champion_alpha={_fmt_pct(summary.get('champion_alpha_vs_strategy_fit_pct'))}"
    )
    print(f"[weekly-summary] text={result['text_path']}")
    print(f"[weekly-summary] html={result['html_path']}")
    print(f"[weekly-summary] json={result['json_path']}")
    return result


if __name__ == "__main__":
    main()
