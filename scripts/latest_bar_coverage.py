"""Read-only, date-aware daily-bar completeness diagnostics.

No fetching, price filling, ticker retirement or model-universe mutation occurs
here. Consumers must supply both complete official tapes and actual layer dates.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
import csv
import math
import re


class CoverageContractError(ValueError):
    """Official evidence is unavailable, incomplete or internally inconsistent."""


@lru_cache(maxsize=8192)
def _cached_csv_max_date(path, mtime_ns, size):
    import pandas as pd

    frame = pd.read_csv(path, usecols=["Date"])
    dates = pd.to_datetime(frame["Date"], errors="raise")
    if dates.empty or dates.isna().any():
        raise CoverageContractError(f"Empty/invalid Date column: {path}")
    after = Path(path).stat()
    if (after.st_mtime_ns, after.st_size) != (mtime_ns, size):
        raise CoverageContractError(f"CSV changed while reading: {path}")
    return dates.max().date().isoformat()


def load_local_latest_dates(daily_dir, *, retired_path=None, as_of=None):
    """Read all numeric CSV max dates; cache each file by path/mtime_ns/size.

    Existing retired registry is used only for this current operational
    diagnostic, optionally respecting retired_as_of. This is not an as-of
    history loader: later bars remain visible and are reported by the classifier.
    Raises on malformed/empty CSVs instead of silently losing coverage.
    """
    directory = Path(daily_dir)
    if not directory.is_dir():
        raise CoverageContractError(f"Daily CSV directory absent: {directory}")
    retired = set()
    if retired_path is not None:
        with Path(retired_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"ticker", "retired_as_of"} <= set(reader.fieldnames or []):
                raise CoverageContractError("Retired registry schema mismatch")
            for row in reader:
                effective = _day(row["retired_as_of"])
                if as_of is None or effective <= _day(as_of):
                    retired.add(row["ticker"])
    result = []
    for path in sorted(directory.glob("*.csv")):
        if not path.stem.isdigit() or path.stem in retired:
            continue
        stat = path.stat()
        result.append({"ticker": path.stem, "csv_date": _cached_csv_max_date(
            str(path.resolve()), stat.st_mtime_ns, stat.st_size)})
    if not result:
        raise CoverageContractError("No numeric daily CSVs found")
    return result


def _day(value):
    if value is None:
        return None
    return date.fromisoformat(str(value)[:10]).isoformat()


def _field(value):
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", str(value)))


def _number(value):
    text = str(value).strip().replace(",", "")
    if not text or set(text) <= {"-"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise CoverageContractError(f"Invalid official numeric value: {text!r}") from exc
    if not math.isfinite(number):
        raise CoverageContractError("Non-finite official numeric value")
    return number


def parse_official_tape(payload, market, target_date, *, minimum_rows=None):
    """Validate date/schema/coverage and retain official rows with no OHLC."""
    target = _day(target_date)
    if market not in {"twse", "tpex"}:
        raise CoverageContractError(f"Unsupported market: {market}")
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise CoverageContractError(f"{market}: missing/failed source status")
    if str(payload.get("date", "")) != target.replace("-", ""):
        raise CoverageContractError(f"{market}: source date does not match {target}")
    names = (["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
             if market == "twse" else ["代號", "名稱", "開盤", "最高", "最低", "收盤", "成交股數"])
    tables = list(payload.get("tables") or [])
    if "fields" in payload:
        tables.append(payload)
    matches = [t for t in tables if isinstance(t, dict)
               and set(names) <= {_field(f) for f in t.get("fields", [])}]
    if len(matches) != 1:
        raise CoverageContractError(f"{market}: expected one recognized OHLCV table, got {len(matches)}")
    table = matches[0]
    fields = [_field(f) for f in table["fields"]]
    if len(set(fields)) != len(fields):
        raise CoverageContractError(f"{market}: duplicate field names")
    indices = [fields.index(f) for f in names]
    rows = {}
    for raw in table.get("data", []):
        if not isinstance(raw, list) or len(raw) != len(fields):
            raise CoverageContractError(f"{market}: row width differs from schema")
        ticker, name, *values = [raw[i] for i in indices]
        ticker = str(ticker).strip()
        if ticker in rows:
            raise CoverageContractError(f"{market}: duplicate ticker {ticker}")
        o, h, l, c, volume = [_number(v) for v in values]
        if volume is None or volume < 0:
            raise CoverageContractError(f"{market}/{ticker}: invalid volume")
        prices = (o, h, l, c)
        present = sum(p is not None for p in prices)
        if present == 0:
            state = "official_no_ohlc_zero_volume" if volume == 0 else "official_no_ohlc_reported_volume"
        elif present == 4 and all(p > 0 for p in prices) and l <= min(o, c) <= max(o, c) <= h:
            state = "official_bar"
        else:
            state = "official_invalid_ohlc"
        rows[ticker] = {"ticker": ticker, "name": str(name), "market": market,
                        "source_date": target, "state": state, "open": o, "high": h,
                        "low": l, "close": c, "volume": volume}
    minimum = minimum_rows if minimum_rows is not None else {"twse": 500, "tpex": 300}[market]
    stock_rows = sum(bool(re.fullmatch(r"[1-9]\d{3}", t)) for t in rows)
    if stock_rows < minimum:
        raise CoverageContractError(f"{market}: only {stock_rows} stock rows; minimum={minimum}")
    return rows


def validate_events(registry):
    """Reject duplicate/overlapping versions; intervals use exclusive resume/end."""
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise CoverageContractError("Unsupported event registry schema")
    events = registry.get("events")
    if not isinstance(events, list):
        raise CoverageContractError("Missing event list")
    ids = set()
    by_ticker = {}
    for event in events:
        required = {"id", "ticker", "status", "effective_from", "available_on", "source_url", "source_sha256"}
        if not isinstance(event, dict) or not required <= event.keys():
            raise CoverageContractError("Incomplete event provenance")
        if event["id"] in ids or event["status"] not in {"suspended", "terminated"}:
            raise CoverageContractError("Duplicate event ID or unknown status")
        ids.add(event["id"])
        start, end = _day(event["effective_from"]), _day(event.get("effective_until"))
        _day(event["available_on"])
        if end and end <= start:
            raise CoverageContractError("Empty/reversed event interval")
        if not re.fullmatch(r"[a-f0-9]{64}", event["source_sha256"]):
            raise CoverageContractError("Invalid event source hash")
        for old_start, old_end in by_ticker.get(event["ticker"], []):
            if start < (old_end or "9999-12-31") and old_start < (end or "9999-12-31"):
                raise CoverageContractError(f"Overlapping event periods for {event['ticker']}")
        by_ticker.setdefault(event["ticker"], []).append((start, end))
    return events


def build_latest_bar_coverage(*, target_date, knowledge_date, twse_payload, tpex_payload,
                              layer_dates, event_registry, minimum_rows=None):
    """Return an auditable health summary without changing any data or signal.

    layer_dates: iterable of {ticker, csv_date, db_date?, ml_date?, ml_expected?}.
    Optional downstream keys mean checked; omission means not checked. A stale
    ML row is retained but never called current, even during a legitimate halt.
    minimum_rows is for small fixtures; production callers should omit it.
    """
    target, known = _day(target_date), _day(knowledge_date)
    if known < target:
        raise CoverageContractError("Knowledge date precedes the official target tape")
    tapes = {m: parse_official_tape(p, m, target, minimum_rows=(minimum_rows or {}).get(m))
             for m, p in [("twse", twse_payload), ("tpex", tpex_payload)]}
    if set(tapes["twse"]) & set(tapes["tpex"]):
        raise CoverageContractError("Ticker occurs in both official markets")
    tape = {**tapes["twse"], **tapes["tpex"]}
    events = validate_events(event_registry)
    active = {e["ticker"]: e for e in events
              if _day(e["available_on"]) <= known and _day(e["effective_from"]) <= target
              and (not e.get("effective_until") or target < _day(e["effective_until"]))}
    results, seen = [], set()
    for item in layer_dates:
        ticker = str(item["ticker"])
        if ticker in seen:
            raise CoverageContractError(f"Duplicate layer ticker {ticker}")
        seen.add(ticker)
        csv_day = _day(item.get("csv_date"))
        source, event = tape.get(ticker), active.get(ticker)
        issues = []
        if source and source["state"] == "official_bar":
            status = "current" if csv_day == target else "source_gap"
            if event:
                status = "official_event_conflict"
        elif source:
            status = source["state"]
            if csv_day == target:
                issues.append("csv_bar_without_valid_official_ohlc")
        elif event:
            status = "official_" + event["status"]
            if csv_day == target:
                issues.append("csv_bar_during_official_stop")
        else:
            status = "unknown_official_absence"
        if csv_day and csv_day > target:
            issues.append("csv_date_after_target")
        if "db_date" in item:
            db_day = _day(item.get("db_date"))
            if db_day != csv_day:
                issues.append("db_lag" if csv_day and (not db_day or db_day < csv_day) else "db_source_date_mismatch")
        if "ml_date" in item:
            ml_day = _day(item.get("ml_date"))
            if ml_day and csv_day and ml_day != csv_day:
                issues.append("ml_lag" if ml_day < csv_day else "ml_source_date_mismatch")
            elif not ml_day and item.get("ml_expected"):
                issues.append("ml_missing_expected_row")
        row = {"ticker": ticker, "classification": status, "csv_date": csv_day,
               "target_date": target, "source": source, "event": event, "layer_issues": issues,
               "ml_row_current": _day(item.get("ml_date")) == target if "ml_date" in item else None}
        for key in ["db_date", "ml_date"]:
            if key in item:
                row[key] = _day(item[key])
        results.append(row)
    counts = {}
    for row in results:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    errors = [r["ticker"] for r in results if r["layer_issues"] or r["classification"] in
              {"source_gap", "official_invalid_ohlc", "official_event_conflict"}]
    unknown = [r["ticker"] for r in results if r["classification"] == "unknown_official_absence"]
    return {"schema_version": 1, "scope": "latest_bar_coverage_only", "target_date": target,
            "knowledge_date": known, "status": "fail" if errors else "warn" if unknown else "ok",
            "official_market_rows": {m: len(v) for m, v in tapes.items()},
            "classified_tickers": len(results), "counts": counts, "error_tickers": errors,
            "unknown_tickers": unknown, "rows": results,
            "limitations": ["No historical gap or price-value audit is implied.",
                            "No-OHLC is a valid source absence, not a tradeable quote.",
                            "Scheduled resumption requires a new official bar to establish freshness."]}
