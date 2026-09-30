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
