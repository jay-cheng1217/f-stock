# -*- coding: utf-8 -*-
"""Update official label and price-continuity corporate-action factors.

The exchanges publish two reference prices that are usually equal but are not
interchangeable:

* ``label_factor`` uses the official ex-right/dividend or resume reference and
  measures the shareholder-return basis used by training labels.
* ``price_factor`` uses the official opening/starting-trade basis and removes
  only mechanical price-series discontinuities for technical indicators.

Rights offerings such as TWSE 2429 on 2024-07-02 prove why the distinction is
required: the economic ex-right reference was 29.15, while the opening auction
continued from 38.90.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "ml" / "data" / "corporate_action_price_factors.csv"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.official_daily_prices import normalize_official_ticker  # noqa: E402
from scripts.update_ex_dividend_calendar import _get_json, _roc_to_iso  # noqa: E402


TWSE_URLS = {
    "ex_right_dividend": "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
    "capital_reduction": "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU",
    "par_value_change": "https://www.twse.com.tw/rwd/zh/change/TWTB8U",
    "etf_split": "https://www.twse.com.tw/rwd/zh/split/TWTCAU",
}
TPEX_URLS = {
    "ex_right_dividend": "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
    "capital_reduction": "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt",
    "par_value_change": "https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgRslt",
    "etf_split": "https://www.tpex.org.tw/www/zh-tw/bulletin/etfSplitRslt",
}

OUTPUT_COLUMNS = [
    "stock_id",
    "date",
    "market",
    "event_type",
    "before_price",
    "label_reference_price",
    "price_reference_price",
    "label_factor",
    "price_factor",
    "official_source",
]


def _number(value: object) -> float | None:
    text = str(value).strip().replace(",", "")
    if text in {"", "--", "---", "nan", "None", "NA"}:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _make_action(
    *,
    stock_id: object,
    action_date: object,
    market: str,
    event_type: str,
    before_price: object,
    label_reference_price: object,
    price_reference_price: object,
    official_source: str,
) -> dict | None:
    ticker = normalize_official_ticker(stock_id)
    iso_date = _roc_to_iso(str(action_date))
    before = _number(before_price)
    label_reference = _number(label_reference_price)
    price_reference = _number(price_reference_price)
    if (
        ticker is None
        or iso_date is None
        or before is None
        or label_reference is None
        or price_reference is None
        or before <= 0
        or label_reference <= 0
        or price_reference <= 0
    ):
        return None
    return {
        "stock_id": ticker,
        "date": iso_date,
        "market": market,
        "event_type": event_type,
        "before_price": before,
        "label_reference_price": label_reference,
        "price_reference_price": price_reference,
        "label_factor": label_reference / before,
        "price_factor": price_reference / before,
        "official_source": official_source,
    }


def _parse_rows(
    rows: list,
    *,
    market: str,
    event_type: str,
    indexes: tuple[int, int, int, int, int],
    official_source: str,
) -> list[dict]:
    date_idx, ticker_idx, before_idx, label_idx, price_idx = indexes
    parsed: list[dict] = []
    for row in rows:
        try:
            action = _make_action(
                stock_id=row[ticker_idx],
                action_date=row[date_idx],
                market=market,
                event_type=event_type,
                before_price=row[before_idx],
                label_reference_price=row[label_idx],
                price_reference_price=row[price_idx],
                official_source=official_source,
            )
        except IndexError:
            action = None
        if action is not None:
            parsed.append(action)
    return parsed


def parse_twse_exdiv(payload: dict) -> list[dict]:
    return _parse_rows(
        payload.get("data") or [],
        market="TWSE",
        event_type="ex_right_dividend",
        indexes=(0, 1, 3, 4, 9),
        official_source=TWSE_URLS["ex_right_dividend"],
    )


def parse_tpex_exdiv(payload: dict) -> list[dict]:
    tables = payload.get("tables") or []
    rows = tables[0].get("data") or [] if tables else []
    return _parse_rows(
        rows,
        market="TPEX",
        event_type="ex_right_dividend",
        indexes=(0, 1, 3, 4, 11),
        official_source=TPEX_URLS["ex_right_dividend"],
    )


def parse_twse_reduction(payload: dict) -> list[dict]:
    return _parse_rows(
        payload.get("data") or [],
        market="TWSE",
        event_type="capital_reduction",
        indexes=(0, 1, 3, 4, 7),
        official_source=TWSE_URLS["capital_reduction"],
    )


def parse_tpex_reduction(payload: dict) -> list[dict]:
    tables = payload.get("tables") or []
    rows = tables[0].get("data") or [] if tables else []
    return _parse_rows(
        rows,
        market="TPEX",
        event_type="capital_reduction",
        indexes=(0, 1, 3, 4, 7),
        official_source=TPEX_URLS["capital_reduction"],
    )


def parse_twse_par_value(payload: dict) -> list[dict]:
    return _parse_rows(
        payload.get("data") or [],
        market="TWSE",
        event_type="par_value_change",
        indexes=(0, 1, 3, 4, 7),
        official_source=TWSE_URLS["par_value_change"],
    )


def parse_tpex_par_value(payload: dict) -> list[dict]:
    tables = payload.get("tables") or []
    rows = tables[0].get("data") or [] if tables else []
    return _parse_rows(
        rows,
        market="TPEX",
        event_type="par_value_change",
        indexes=(0, 1, 3, 4, 7),
        official_source=TPEX_URLS["par_value_change"],
    )


def parse_twse_etf_split(payload: dict) -> list[dict]:
    return _parse_rows(
        payload.get("data") or [],
        market="TWSE",
        event_type="etf_split",
        indexes=(0, 1, 4, 5, 8),
        official_source=TWSE_URLS["etf_split"],
    )


def parse_tpex_etf_split(payload: dict) -> list[dict]:
    tables = payload.get("tables") or []
    rows = tables[0].get("data") or [] if tables else []
    return _parse_rows(
        rows,
        market="TPEX",
        event_type="etf_split",
        indexes=(0, 1, 3, 4, 7),
        official_source=TPEX_URLS["etf_split"],
    )


def _twse_url(base: str, start: date, end: date) -> str:
    return f"{base}?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&response=json"


def _tpex_url(base: str, start: date, end: date) -> str:
    return f"{base}?startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}&response=json"


def fetch_period(start: date, end: date) -> tuple[list[dict], dict[str, int]]:
    specs: list[tuple[str, str, Callable[[dict], list[dict]]]] = [
        ("twse_exdiv", _twse_url(TWSE_URLS["ex_right_dividend"], start, end), parse_twse_exdiv),
        ("tpex_exdiv", _tpex_url(TPEX_URLS["ex_right_dividend"], start, end), parse_tpex_exdiv),
        ("twse_reduction", _twse_url(TWSE_URLS["capital_reduction"], start, end), parse_twse_reduction),
        ("tpex_reduction", _tpex_url(TPEX_URLS["capital_reduction"], start, end), parse_tpex_reduction),
        ("twse_par_value", _twse_url(TWSE_URLS["par_value_change"], start, end), parse_twse_par_value),
        ("tpex_par_value", _tpex_url(TPEX_URLS["par_value_change"], start, end), parse_tpex_par_value),
        ("twse_etf_split", _twse_url(TWSE_URLS["etf_split"], start, end), parse_twse_etf_split),
        ("tpex_etf_split", _tpex_url(TPEX_URLS["etf_split"], start, end), parse_tpex_etf_split),
    ]
    rows: list[dict] = []
    counts: dict[str, int] = {}
    for name, url, parser in specs:
        payload = _get_json(url)
        parsed = parser(payload)
        rows.extend(parsed)
        counts[name] = len(parsed)
    return rows, counts


def fetch_range(start: date, end: date) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict] = []
    counts: dict[str, int] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year, 12, 31), end)
        chunk_rows, chunk_counts = fetch_period(cursor, chunk_end)
        rows.extend(chunk_rows)
        for name, count in chunk_counts.items():
            counts[name] = counts.get(name, 0) + count
        cursor = date(cursor.year + 1, 1, 1)
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    # A fully successful query can legitimately contain no actions. Network or
    # schema failures have already raised inside fetch_period/_get_json; an empty
    # event set must not turn the nightly pipeline red.
    frame = validate_factor_frame(frame)
    return frame, counts


def validate_factor_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(OUTPUT_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"corporate-action factor frame missing columns: {missing}")
    out = frame.loc[:, OUTPUT_COLUMNS].copy()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in (
        "before_price",
        "label_reference_price",
        "price_reference_price",
        "label_factor",
        "price_factor",
    ):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    invalid = (
        out["date"].isna()
        | out["stock_id"].eq("")
        | out[["before_price", "label_reference_price", "price_reference_price", "label_factor", "price_factor"]]
        .le(0)
        .any(axis=1)
        | ~np.isfinite(out[["label_factor", "price_factor"]]).all(axis=1)
    )
    if invalid.any():
        raise ValueError(
            "invalid official corporate-action rows: "
            f"{out.loc[invalid].head(5).to_dict('records')}"
        )
    out = out.drop_duplicates(
        ["stock_id", "date", "market", "event_type"], keep="last"
    )
    return out.sort_values(["date", "stock_id", "event_type"]).reset_index(drop=True)


def merge_save(new_rows: pd.DataFrame, start: date, end: date, path: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    if path.exists():
        old = pd.read_csv(path, dtype={"stock_id": str}, encoding="utf-8-sig")
        if "label_factor" not in old.columns and "factor" in old.columns:
            old = old.rename(columns={"factor": "label_factor"})
            old["price_factor"] = old["label_factor"]
            old["market"] = old.get("market", "UNKNOWN")
            old["event_type"] = old.get("event_type", "legacy_supplement")
            old["before_price"] = 1.0
            old["label_reference_price"] = old["label_factor"]
            old["price_reference_price"] = old["price_factor"]
        old["official_source"] = old.get("official_source", "legacy")
        old = validate_factor_frame(old)
        old_dates = pd.to_datetime(old["date"], errors="coerce").dt.date
        parts.append(old.loc[(old_dates < start) | (old_dates > end)])
    parts.append(new_rows)
    combined = validate_factor_frame(pd.concat(parts, ignore_index=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    combined.to_csv(temp, index=False, encoding="utf-8-sig")
    os.replace(temp, path)
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", nargs=2, metavar=("START", "END"))
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    if args.backfill:
        start = date.fromisoformat(args.backfill[0])
        end = date.fromisoformat(args.backfill[1])
    else:
        today = date.today()
        start = today - timedelta(days=14)
        end = today + timedelta(days=45)
    if start > end:
        raise ValueError("start date must not be after end date")

    rows, counts = fetch_range(start, end)
    combined = merge_save(rows, start, end, args.output)
    print(f"corporate action factors -> {args.output} ({len(combined)} rows)")
    print(f"updated range: {start}..{end}; source counts: {counts}")
    print(
        "factor semantics: label=official economic reference/prior close; "
        "price=official opening basis/prior close"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
