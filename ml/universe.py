"""Shared market-universe helpers for model pipelines."""
from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ETF_UNIVERSE_PATH = BASE_DIR / "ml" / "data" / "etf_universe.csv"
RETIRED_TICKERS_PATH = BASE_DIR / "config" / "retired_tickers.csv"
# 官方停止買賣/終止上市櫃事件名冊(2026-09-06 建立,含公告來源與雜湊;scripts/latest_bar_coverage.py 驗證規格)
TRADING_STATUS_EVENTS_PATH = BASE_DIR / "config" / "trading_status_events.json"

STOCK = "stock"
ETF_PASSIVE = "etf_passive"
ETF_ACTIVE = "etf_active"
ETF_BOND = "etf_bond"
ETF_FUTURES = "etf_futures"
ETF_LEVERAGED = "etf_leveraged"
ETF_INVERSE = "etf_inverse"

ETF_TYPES = frozenset(
    {
        ETF_PASSIVE,
        ETF_ACTIVE,
        ETF_BOND,
        ETF_FUTURES,
        ETF_LEVERAGED,
        ETF_INVERSE,
    }
)


def normalize_ticker(ticker: object) -> str:
    return str(ticker).strip().upper()


def _suffix(ticker: str) -> str:
    match = re.search(r"([A-Z]+)$", ticker)
    return match.group(1) if match else ""


def infer_etf_type(
    ticker: object,
    *,
    name: object = "",
    asset_class: object = "",
    strategy: object = "",
    leverage_type: object = "",
) -> str:
    """Infer Taiwan ETF handling class from official metadata and ticker suffix."""
    ticker_norm = normalize_ticker(ticker)
    suffix = _suffix(ticker_norm)
    name_text = str(name or "")
    asset_text = str(asset_class or "").lower()
    strategy_text = str(strategy or "").lower()
    leverage_text = str(leverage_type or "").lower()

    if suffix.endswith("R") or "inverse" in leverage_text or "反向" in name_text:
        return ETF_INVERSE
    if suffix.endswith("L") or "leveraged" in leverage_text or "槓桿" in name_text or "正2" in name_text:
        return ETF_LEVERAGED
    if suffix.endswith("U") or "futures" in asset_text or "期貨" in name_text:
        return ETF_FUTURES
    if (
        suffix.endswith("B")
        or suffix.endswith("D")
        or "bond" in asset_text
        or "fixed_income" in asset_text
        or "債" in name_text
    ):
        return ETF_BOND
    if suffix.endswith("A") or "active" in strategy_text or "主動" in name_text:
        return ETF_ACTIVE
    return ETF_PASSIVE


def _read_etf_universe(path: Path = ETF_UNIVERSE_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = csv.DictReader(fh)
        records: dict[str, dict[str, str]] = {}
        for row in rows:
            ticker = normalize_ticker(row.get("ticker", ""))
            if not ticker:
                continue
            row = {str(key): "" if value is None else str(value) for key, value in row.items()}
            row["ticker"] = ticker
            row["universe_type"] = row.get("universe_type") or infer_etf_type(
                ticker,
                name=row.get("name", ""),
                asset_class=row.get("asset_class", ""),
                strategy=row.get("strategy", ""),
                leverage_type=row.get("leverage_type", ""),
            )
            records[ticker] = row
        return records


@lru_cache(maxsize=1)
def load_etf_universe() -> dict[str, dict[str, str]]:
    return _read_etf_universe()


def reload_etf_universe() -> dict[str, dict[str, str]]:
    load_etf_universe.cache_clear()
    return load_etf_universe()


def _read_retired_tickers(path: Path = RETIRED_TICKERS_PATH) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = csv.DictReader(fh)
        return frozenset(
            normalize_ticker(row.get("ticker", ""))
            for row in rows
            if normalize_ticker(row.get("ticker", ""))
        )


@lru_cache(maxsize=1)
def load_retired_tickers() -> frozenset[str]:
    return _read_retired_tickers()


def reload_retired_tickers() -> frozenset[str]:
    load_retired_tickers.cache_clear()
    return load_retired_tickers()


def is_retired_ticker(ticker: object) -> bool:
    return normalize_ticker(ticker) in load_retired_tickers()


def get_etf_metadata(ticker: object) -> dict[str, str] | None:
    return load_etf_universe().get(normalize_ticker(ticker))


def classify_ticker(ticker: object) -> str:
    ticker_norm = normalize_ticker(ticker)
    metadata = get_etf_metadata(ticker_norm)
    if metadata:
        return metadata.get("universe_type") or infer_etf_type(
            ticker_norm,
            name=metadata.get("name", ""),
            asset_class=metadata.get("asset_class", ""),
            strategy=metadata.get("strategy", ""),
            leverage_type=metadata.get("leverage_type", ""),
        )

    # Suffix fallback covers newly listed Taiwan ETF classes before the CSV is refreshed.
    if re.match(r"^00[0-9A-Z]{2,6}[A-Z]$", ticker_norm):
        return infer_etf_type(ticker_norm)
    return STOCK


def is_etf_ticker(ticker: object) -> bool:
    return classify_ticker(ticker) in ETF_TYPES


def load_trading_status_events(path: Path | None = None) -> list[dict]:
    """讀官方事件名冊;格式不符直接報錯(名冊入版控,壞掉要在測試或排程第一時間發現)。"""
    import json

    path = Path(path) if path is not None else TRADING_STATUS_EVENTS_PATH
    if not path.exists():
        return []
    registry = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or registry.get("schema_version") != 1 or not isinstance(registry.get("events"), list):
        raise ValueError(f"Unsupported trading status registry: {path}")
    events = []
    for event in registry["events"]:
        if not {"ticker", "status", "effective_from", "available_on"} <= set(event):
            raise ValueError(f"Incomplete trading status event: {event}")
        if event["status"] not in {"suspended", "terminated"}:
            raise ValueError(f"Unknown trading status: {event['status']}")
        for key in ("effective_from", "available_on", "effective_until"):
            if event.get(key):
                date.fromisoformat(str(event[key]))
        events.append(event)
    return events


def active_trading_status(as_of: date, status: str, path: Path | None = None) -> dict[str, dict]:
    """as_of 當天已生效、且當時已可得知(available_on)的事件:ticker → 事件。

    只供「即時推論」判斷這檔現在能不能交易;訓練/回測用的歷史資料不得據此刪除(會造成倖存者偏差)。
    """
    day = as_of.isoformat()
    active = {}
    for event in load_trading_status_events(path):
        if event["status"] != status or str(event["available_on"]) > day or str(event["effective_from"]) > day:
            continue
        if event.get("effective_until") and day >= str(event["effective_until"]):
            continue
        active[normalize_ticker(event["ticker"])] = event
    return active


def terminated_tickers_as_of(as_of: date, path: Path | None = None) -> dict[str, dict]:
    """已終止上市櫃(官方名冊)的代號。"""
    return active_trading_status(as_of, "terminated", path)


def filter_stock_universe(tickers: Iterable[object]) -> list[str]:
    return [
        normalize_ticker(ticker)
        for ticker in tickers
        if not is_etf_ticker(ticker) and not is_retired_ticker(ticker)
    ]


def filter_out_etfs_df(df, ticker_col: str = "ticker"):
    if df is None or ticker_col not in df.columns:
        return df
    mask = ~df[ticker_col].map(is_etf_ticker)
    return df.loc[mask].copy()


def get_etf_listing_start_dates() -> dict[str, date]:
    out: dict[str, date] = {}
    for ticker, row in load_etf_universe().items():
        raw = (row.get("listing_date") or "").replace("/", "-").strip()
        if not raw:
            continue
        try:
            out[ticker] = date.fromisoformat(raw)
        except ValueError:
            continue
    return out


ETF_TICKERS = frozenset(load_etf_universe())
