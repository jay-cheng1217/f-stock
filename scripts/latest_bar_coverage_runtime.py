"""Bounded live diagnostics for old stock-list / prediction row dates."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd

from scripts.latest_bar_coverage import build_latest_bar_coverage


def build_stale_bar_coverage(connection, *, base_dir, model_dir, target_date, knowledge_date):
    root = Path(base_dir)
    stale = connection.execute(
        "SELECT Ticker FROM stock_list WHERE Last_Date < ? OR Last_Date IS NULL", [target_date]
    ).fetchall()
    tickers = {str(row[0]) for row in stale}
    prediction = Path(model_dir) / f"predictions_{target_date}.csv"
    model_dates = {}
    if prediction.exists():
        pred = pd.read_csv(prediction, dtype={"ticker": str}, usecols=["ticker", "date"])
        if pred.ticker.duplicated().any():
            raise ValueError("Ambiguous model ticker dates")
        parsed = pd.to_datetime(pred["date"], errors="raise")
        if parsed.isna().any():
            raise ValueError("Missing model row date")
        model_dates = dict(zip(pred.ticker, parsed.dt.strftime("%Y-%m-%d")))
        tickers.update(ticker for ticker, day in model_dates.items() if day < target_date)
    scope = "Stock-list rows older than expected or stale prediction rows; current-row price equality is outside this check."
    if not tickers:
        return {"status": "ok", "target_date": target_date, "classified_tickers": 0,
                "counts": {}, "rows": [], "runtime_scope": scope}
    dates = dict(connection.execute("SELECT Ticker, MAX(Date) FROM daily_k GROUP BY Ticker").fetchall())
    layers = []
    for ticker in sorted(tickers):
        source = root / "日K資料" / f"{ticker}.csv"
        csv_date = None
        if source.exists():
            values = pd.to_datetime(pd.read_csv(source, usecols=["Date"]).Date, errors="raise")
            if not values.empty:
                csv_date = values.max().strftime("%Y-%m-%d")
        item = {"ticker": ticker, "csv_date": csv_date, "db_date": dates.get(ticker)}
        if ticker in model_dates:
            item.update(ml_date=model_dates[ticker], ml_expected=True)
        layers.append(item)
    payloads = {}
    for market in ("twse", "tpex"):
        path = root / "logs/official_daily_history_cache" / market / f"{target_date.replace('-', '')}.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payloads[market] = json.load(stream)
    registry = json.loads((root / "config/trading_status_events.json").read_text(encoding="utf-8"))
    result = build_latest_bar_coverage(target_date=target_date, knowledge_date=knowledge_date,
                                      twse_payload=payloads["twse"], tpex_payload=payloads["tpex"],
                                      layer_dates=layers, event_registry=registry)
    result["runtime_scope"] = scope
    return result
