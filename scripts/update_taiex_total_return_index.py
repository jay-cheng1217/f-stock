"""Fetch the official TAIEX total-return index (TWSE MFI94U).

The historical endpoint returns one ROC-calendar month per request. Training
labels require this series so adjusted stock returns are never compared with
the price-only TAIEX.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import truststore

    truststore.inject_into_ssl()
except Exception as exc:  # pragma: no cover - environment-specific dependency
    raise RuntimeError(
        "truststore is required for verified TWSE TLS on Windows"
    ) from exc


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BASE_DIR / "大盤指數" / "index_TWII_total_return.csv"
PRICE_INDEX_PATH = BASE_DIR / "大盤指數" / "index_TWII.csv"
HISTORICAL_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MFI94U"


def _build_session() -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=5,
                backoff_factor=0.8,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
        ),
    )
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.twse.com.tw/zh/indices/taiex/mfi94u.html",
        }
    )
    return session


SESSION = _build_session()


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start.to_period("M").start_time, end, freq="MS"))


def _parse_roc_date(value: str) -> pd.Timestamp:
    year, month, day = (int(part) for part in str(value).strip().split("/"))
    return pd.Timestamp(year=year + 1911, month=month, day=day)


def _fetch_month(month: pd.Timestamp, retries: int = 3) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(
                HISTORICAL_URL,
                params={"date": month.strftime("%Y%m01"), "response": "json"},
                timeout=30,
            )
            response.raise_for_status()
            payload = json.loads(response.content.decode("utf-8"))
            if payload.get("stat") != "OK":
                raise RuntimeError(
                    f"TWSE MFI94U {month:%Y-%m} stat={payload.get('stat')!r}"
                )
            rows = payload.get("data") or []
            frame = pd.DataFrame(rows, columns=["Date", "Close"])
            if frame.empty:
                return frame
            frame["Date"] = frame["Date"].map(_parse_roc_date)
            frame["Close"] = pd.to_numeric(
                frame["Close"].astype(str).str.replace(",", "", regex=False),
                errors="raise",
            )
            return frame
        except Exception as exc:  # pragma: no cover - retry branch is network-only
            last_error = exc
            if attempt < retries:
                time.sleep(float(attempt))
    raise RuntimeError(f"failed TWSE MFI94U month {month:%Y-%m}: {last_error}")


def _price_index_dates() -> pd.DatetimeIndex:
    if not PRICE_INDEX_PATH.exists():
        raise FileNotFoundError(f"price index missing: {PRICE_INDEX_PATH}")
    price = pd.read_csv(PRICE_INDEX_PATH, usecols=["Date"], dtype={"Date": str})
    dates = pd.to_datetime(price["Date"], errors="coerce").dropna().drop_duplicates()
    if dates.empty:
        raise ValueError(f"price index has no valid dates: {PRICE_INDEX_PATH}")
    return pd.DatetimeIndex(dates.sort_values())


def _validate_coverage(frame: pd.DataFrame) -> None:
    if frame.empty or frame["Close"].isna().any() or frame["Date"].duplicated().any():
        raise ValueError("TAIEX total-return output is empty, duplicated, or contains nulls")
    price_dates = _price_index_dates()
    start = max(frame["Date"].min(), price_dates.min())
    end = min(frame["Date"].max(), price_dates.max())
    expected = set(price_dates[(price_dates >= start) & (price_dates <= end)])
    actual = set(frame.loc[frame["Date"].between(start, end), "Date"])
    missing = sorted(expected - actual)
    if missing:
        sample = ", ".join(pd.Timestamp(value).strftime("%Y-%m-%d") for value in missing[:5])
        raise ValueError(
            f"TAIEX total-return index missing {len(missing)} price-index dates "
            f"between {start:%Y-%m-%d} and {end:%Y-%m-%d}; sample={sample}"
        )


def update_index(
    output: Path = DEFAULT_OUTPUT,
    *,
    backfill: bool = False,
    start: str | None = None,
    end: str | None = None,
    pause_seconds: float = 0.05,
) -> pd.DataFrame:
    output = Path(output)
    existing = pd.DataFrame(columns=["Date", "Close"])
    if output.exists():
        existing = pd.read_csv(output, dtype={"Date": str})
        existing["Date"] = pd.to_datetime(existing["Date"], errors="coerce")
        existing["Close"] = pd.to_numeric(existing["Close"], errors="coerce")
        existing = existing.dropna(subset=["Date", "Close"])
    elif not backfill and start is None:
        raise RuntimeError(
            f"{output} does not exist; initialize with --backfill before daily updates"
        )

    end_date = pd.Timestamp(end or date.today()).normalize()
    if start:
        start_date = pd.Timestamp(start).normalize()
    elif backfill:
        start_date = _price_index_dates().min().normalize()
    else:
        start_date = existing["Date"].max().to_period("M").start_time

    frames = []
    for month in _month_starts(start_date, end_date):
        frames.append(_fetch_month(month))
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    fetched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if fetched.empty:
        raise RuntimeError(
            f"TWSE MFI94U returned no rows for {start_date:%Y-%m-%d}..{end_date:%Y-%m-%d}"
        )

    combined = (
        fetched.copy()
        if existing.empty
        else pd.concat([existing, fetched], ignore_index=True)
    )
    combined = (
        combined.dropna(subset=["Date", "Close"])
        .drop_duplicates("Date", keep="last")
        .sort_values("Date")
        .reset_index(drop=True)
    )
    _validate_coverage(combined)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    export = combined.copy()
    export["Date"] = export["Date"].dt.strftime("%Y-%m-%d")
    export.to_csv(temp, index=False, encoding="utf-8-sig")
    os.replace(temp, output)
    return combined


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--pause-seconds", type=float, default=0.05)
    args = parser.parse_args()
    result = update_index(
        args.output,
        backfill=args.backfill,
        start=args.start,
        end=args.end,
        pause_seconds=args.pause_seconds,
    )
    print(
        "[taiex-total-return] "
        f"rows={len(result)} range={result['Date'].min():%Y-%m-%d}.."
        f"{result['Date'].max():%Y-%m-%d} output={args.output}"
    )


if __name__ == "__main__":
    main()
