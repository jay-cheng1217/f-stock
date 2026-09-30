# -*- coding: utf-8 -*-
"""Official TWSE/TPEx historical daily OHLCV reader.

The historical endpoints are date-oriented and cover the full regular market.
Callers may supply a cache directory; cached payloads make reconciliation fully
repeatable without repeatedly hitting exchange endpoints.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
import urllib3


TWSE_HISTORY_URL = (
    "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    "?response=json&date={date8}&type=ALLBUT0999"
)
TPEX_HISTORY_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/"
    "stk_wn1430_result.php?l=zh-tw&d={roc_date}&se=EW"
)

OFFICIAL_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume", "Market"]
MIN_OFFICIAL_MARKET_ROWS = {"TWSE": 500, "TPEX": 300}


class OfficialDailyPriceError(RuntimeError):
    """Raised when an official daily source is absent or malformed."""


def _number(value: object) -> float | None:
    text = str(value).strip().replace(",", "")
    if text in {"", "--", "---", "nan", "None"}:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


_OFFICIAL_TICKER_PATTERN = re.compile(r"^(?:\d{4}|\d{5,6}[A-Z]?)$")


def normalize_official_ticker(value: object) -> str | None:
    """Return a TWSE/TPEx listing symbol supported by the local source tree."""
    ticker = str(value).strip()
    return ticker if _OFFICIAL_TICKER_PATTERN.fullmatch(ticker) else None


def parse_twse_history(payload: dict, day: str | pd.Timestamp) -> pd.DataFrame:
    """Parse TWSE MI_INDEX full-market rows by the exchange's stable positions."""
    date = pd.Timestamp(day).normalize()
    rows: list[dict] = []
    tables = payload.get("tables") or []
    if not tables and payload.get("data9"):
        tables = [{"data": payload.get("data9")}]

    for table in tables:
        for row in table.get("data") or []:
            if len(row) < 9:
                continue
            ticker = normalize_official_ticker(row[0])
            if ticker is None:
                continue
            values = {
                "Ticker": ticker,
                "Date": date,
                "Volume": _number(row[2]),
                "Open": _number(row[5]),
                "High": _number(row[6]),
                "Low": _number(row[7]),
                "Close": _number(row[8]),
                "Market": "TWSE",
            }
            if all(values[column] is not None for column in ("Open", "High", "Low", "Close", "Volume")):
                rows.append(values)
    if payload.get("stat") != "OK" or not rows:
        raise OfficialDailyPriceError(
            f"TWSE MI_INDEX unavailable for {date.date()}: stat={payload.get('stat')!r}"
        )
    return pd.DataFrame(rows, columns=OFFICIAL_COLUMNS)


def parse_tpex_history(payload: dict, day: str | pd.Timestamp) -> pd.DataFrame:
    """Parse TPEx regular-board daily rows by the exchange's stable positions."""
    date = pd.Timestamp(day).normalize()
    rows: list[dict] = []
    tables = payload.get("tables") or []
    source_rows = (tables[0].get("data") or []) if tables else (payload.get("aaData") or [])
    for row in source_rows:
        if len(row) < 10:
            continue
        ticker = normalize_official_ticker(row[0])
        if ticker is None:
            continue
        values = {
            "Ticker": ticker,
            "Date": date,
            "Close": _number(row[2]),
            "Open": _number(row[4]),
            "High": _number(row[5]),
            "Low": _number(row[6]),
            "Volume": _number(row[7]),
            "Market": "TPEX",
        }
        if all(values[column] is not None for column in ("Open", "High", "Low", "Close", "Volume")):
            rows.append(values)
    if str(payload.get("stat", "")).lower() != "ok" or not rows:
        raise OfficialDailyPriceError(
            f"TPEx regular-board history unavailable for {date.date()}: stat={payload.get('stat')!r}"
        )
    return pd.DataFrame(rows, columns=OFFICIAL_COLUMNS)


def _read_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _write_gzip_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with gzip.open(temp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, path)


def _download_json(
    url: str,
    *,
    cache_path: Path | None,
    verify_tls: bool,
    attempts: int = 4,
) -> dict:
    if cache_path is not None and cache_path.exists():
        return _read_gzip_json(cache_path)

    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (official-price-reconciliation)"},
                timeout=60,
                verify=verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise OfficialDailyPriceError("official endpoint returned non-object JSON")
            return payload
        except Exception as exc:  # network retry boundary
            error = exc
            time.sleep(min(8.0, 0.8 * (2**attempt)))
    raise OfficialDailyPriceError(f"official endpoint failed after {attempts} attempts: {url}") from error


def validate_official_daily_coverage(
    frame: pd.DataFrame,
    day: str | pd.Timestamp,
) -> dict[str, int]:
    """Fail closed when either exchange returns only a partial regular-market tape."""
    date = pd.Timestamp(day).normalize()
    counts = {
        str(market): int(count)
        for market, count in frame.groupby("Market", dropna=False).size().items()
    }
    insufficient = {
        market: counts.get(market, 0)
        for market, minimum in MIN_OFFICIAL_MARKET_ROWS.items()
        if counts.get(market, 0) < minimum
    }
    if insufficient:
        expected = ", ".join(
            f"{market}>={minimum}" for market, minimum in MIN_OFFICIAL_MARKET_ROWS.items()
        )
        observed = ", ".join(
            f"{market}={counts.get(market, 0)}" for market in MIN_OFFICIAL_MARKET_ROWS
        )
        raise OfficialDailyPriceError(
            f"official daily coverage incomplete for {date.date()}: "
            f"observed {observed}; expected {expected}"
        )
    return counts


def _discard_cache_files(*paths: Path | None) -> None:
    for path in paths:
        if path is None:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def fetch_official_daily_prices(
    day: str | pd.Timestamp,
    *,
    cache_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Fetch one trading day's complete regular-market OHLCV from both exchanges."""
    date = pd.Timestamp(day).normalize()
    date8 = date.strftime("%Y%m%d")
    roc_date = f"{date.year - 1911:03d}/{date.month:02d}/{date.day:02d}"
    cache_root = Path(cache_dir) if cache_dir is not None else None
    twse_cache = cache_root / "twse" / f"{date8}.json.gz" if cache_root else None
    tpex_cache = cache_root / "tpex" / f"{date8}.json.gz" if cache_root else None

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            twse_payload = _download_json(
                TWSE_HISTORY_URL.format(date8=date8),
                cache_path=twse_cache,
                verify_tls=True,
            )
            tpex_payload = _download_json(
                TPEX_HISTORY_URL.format(roc_date=roc_date),
                cache_path=tpex_cache,
                verify_tls=False,
            )
            result = pd.concat(
                [parse_twse_history(twse_payload, date), parse_tpex_history(tpex_payload, date)],
                ignore_index=True,
            )
            duplicates = result.duplicated(["Ticker", "Date"], keep=False)
            if duplicates.any():
                sample = result.loc[duplicates, ["Ticker", "Date", "Market"]].head(10)
                raise OfficialDailyPriceError(
                    f"official daily duplicate ticker/date rows: {sample.to_dict('records')}"
                )
            validate_official_daily_coverage(result, date)
        except Exception as exc:
            last_error = exc
            _discard_cache_files(twse_cache, tpex_cache)
            if attempt == 0:
                continue
            if isinstance(exc, OfficialDailyPriceError):
                raise
            raise OfficialDailyPriceError(
                f"official daily validation failed for {date.date()}"
            ) from exc

        if twse_cache is not None and not twse_cache.exists():
            _write_gzip_json(twse_cache, twse_payload)
        if tpex_cache is not None and not tpex_cache.exists():
            _write_gzip_json(tpex_cache, tpex_payload)
        return result

    raise OfficialDailyPriceError(
        f"official daily validation failed for {date.date()}"
    ) from last_error
