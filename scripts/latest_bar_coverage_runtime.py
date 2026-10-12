"""Bounded live diagnostics for old stock-list / prediction row dates."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd

from scripts.latest_bar_coverage import build_latest_bar_coverage

REDUCTION_HALT_MAX_CALENDAR_DAYS = 45   # 減資停止買賣到恢復買賣通常 1~3 週;更久的不自動認定
REDUCTION_PRICE_TOLERANCE = 0.005       # 官方「停止買賣前收盤價」須與本地最後收盤一致


def explain_capital_reduction_halts(result, actions, last_close):
    """把「官方行情沒有、名冊也沒登錄」但有官方減資恢復買賣紀錄的股票,改標為減資停牌(2026-10-12)。

    actions:ml/data/corporate_action_price_factors.csv(官方 TWSE TWTAUU / TPEx revivt 每日增量),
    其 date=恢復買賣日、before_price=停止買賣前收盤。條件:恢復日晚於本地最後K棒、相距不超過
    REDUCTION_HALT_MAX_CALENDAR_DAYS、停牌前收盤與本地最後收盤一致。只改診斷分類,不動任何資料或訊號。
    """
    if actions is None or actions.empty or not result.get("rows"):
        return result
    reductions = actions[actions["event_type"].astype(str) == "capital_reduction"].copy()
    reductions["stock_id"] = reductions["stock_id"].astype(str).str.strip()
    reductions["date"] = pd.to_datetime(reductions["date"], errors="coerce")
    explained = []
    for row in result["rows"]:
        if row.get("classification") != "unknown_official_absence" or not row.get("csv_date"):
            continue
        last_day = pd.Timestamp(row["csv_date"])
        close = last_close.get(row["ticker"])
        cand = reductions[(reductions["stock_id"] == row["ticker"]) & (reductions["date"] > last_day)
                          & ((reductions["date"] - last_day).dt.days <= REDUCTION_HALT_MAX_CALENDAR_DAYS)]
        if close is not None and len(cand):
            before = pd.to_numeric(cand["before_price"], errors="coerce")
            cand = cand[(before - close).abs() <= abs(close) * REDUCTION_PRICE_TOLERANCE]
        else:
            cand = cand.iloc[0:0]
        if cand.empty:
            continue
        hit = cand.sort_values("date").iloc[0]
        row["classification"] = "official_capital_reduction_halt"
        row["event"] = {"status": "suspended", "reason": "減資停止買賣(官方恢復買賣資料)",
                        "scheduled_resumption": hit["date"].strftime("%Y-%m-%d"),
                        "before_price": float(hit["before_price"]), "source_url": str(hit.get("official_source", ""))}
        explained.append(row["ticker"])
    if explained:
        counts = {}
        for row in result["rows"]:
            counts[row["classification"]] = counts.get(row["classification"], 0) + 1
        result["counts"] = counts
        result["unknown_tickers"] = [t for t in result.get("unknown_tickers", []) if t not in explained]
        result["status"] = "fail" if result.get("error_tickers") else "warn" if result["unknown_tickers"] else "ok"
        result["explained_by_capital_reduction"] = explained
    return result


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
    last_close = {}
    for ticker in sorted(tickers):
        source = root / "日K資料" / f"{ticker}.csv"
        csv_date = None
        if source.exists():
            frame = pd.read_csv(source, usecols=["Date", "Close"])
            values = pd.to_datetime(frame.Date, errors="raise")
            if not frame.empty:
                last_close[ticker] = float(pd.to_numeric(frame.Close, errors="coerce").iloc[values.argmax()])
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
    actions_path = root / "ml" / "data" / "corporate_action_price_factors.csv"
    actions = pd.read_csv(actions_path, dtype={"stock_id": str}) if actions_path.exists() else None
    result = explain_capital_reduction_halts(result, actions, last_close)
    result["runtime_scope"] = scope
    return result
