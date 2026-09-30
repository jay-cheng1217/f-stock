"""Fetch TW stock disposition periods and write special-status health.

This script intentionally stores the full point-in-time period history instead
of only the current active snapshot.  The active CSV is kept for legacy callers,
while the period file and health JSON are the source for tradability gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests
import urllib3

if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream: Any) -> Any:
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.disposition_contract import resolve_disposition_periods, validate_periods
from scripts.taiwan_trading_calendar import next_taiwan_trading_day
OUTPUT_PATH = os.path.join(BASE_DIR, "disposition_active.csv")
DATA_DIR = os.path.join(BASE_DIR, "ml", "data")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
PERIODS_PATH = os.path.join(DATA_DIR, "disposition_periods.csv")
STATUS_REPORT_PATH = os.path.join(REPORT_DIR, "special_stock_status_latest.json")

TWSE_PUNISH_URL = "https://www.twse.com.tw/rwd/zh/announcement/punish"
TPEX_DISPOSAL_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"
DEFAULT_LOOKBACK_DAYS = 730
EMPTY_COLUMNS = ["stock_id", "period_start", "period_end", "source", "announcement_date"]
SOURCE_NAMES = ("TWSE", "TPEx")
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> date:
    return date.today()


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)


def _atomic_write_csv(path: str, df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, path)


def _empty_periods() -> pd.DataFrame:
    return pd.DataFrame(columns=EMPTY_COLUMNS)


def _load_previous_report() -> dict[str, Any] | None:
    if not os.path.exists(STATUS_REPORT_PATH):
        return None
    try:
        with open(STATUS_REPORT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _roc_date_to_iso(value: str) -> str | None:
    # TWSE prefixes same-day announcements with "*" (observed 2026-09-08: "*115/09/08").
    value = str(value or "").strip().lstrip("*＊").strip()
    parts = value.replace(".", "/").replace("-", "/").split("/")
    if len(parts) != 3:
        return None
    try:
        year = int(parts[0])
        if year < 1911:
            year += 1911
        month = int(parts[1])
        day = int(parts[2])
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _split_period(value: str) -> tuple[str | None, str | None]:
    text = str(value or "").strip().replace("\uFF5E", "~")
    if "~" not in text:
        return None, None
    start, end = [part.strip() for part in text.split("~", 1)]
    return _roc_date_to_iso(start), _roc_date_to_iso(end)


def _normalize_periods(df: pd.DataFrame) -> pd.DataFrame:
    return validate_periods(df)


def _parse_official_table(table: dict[str, Any], source: str) -> list[dict[str, str]]:
    fields, data = table.get("fields"), table.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise RuntimeError(f"{source} missing fields/data arrays")
    aliases = {"stock_id": ("證券代號",), "announcement_date": ("公布日期",),
               "period": ("處置起迄時間", "處置起訖時間")}
    indexes = {}
    for key, names in aliases.items():
        matches = [i for i, field in enumerate(fields) if field in names]
        if len(matches) != 1:
            raise RuntimeError(f"{source} missing or duplicate named field: {key}")
        indexes[key] = matches[0]
    # TWSE `count` is distinct securities, `total` is announcement rows.
    # Do not reject repeated announcements by equating those two quantities.
    counts = [table["total"]] if "total" in table else []
    if any(not isinstance(count, int) or count != len(data) for count in counts):
        raise RuntimeError(f"{source} declared count does not match rows")
    if not data and not counts and not (type(table.get("count")) is int and table["count"] == 0):
        raise RuntimeError(f"{source} empty response without explicit zero count")
    rows = []
    for index, raw in enumerate(data):
        if not isinstance(raw, list) or len(raw) != len(fields):
            raise RuntimeError(f"{source} malformed row {index + 1}")
        start, end = _split_period(raw[indexes["period"]])
        announced = _roc_date_to_iso(raw[indexes["announcement_date"]])
        stock_id = str(raw[indexes["stock_id"]] or "").strip()
        if not stock_id or not start or not end or not announced:
            raise RuntimeError(f"{source} invalid required values in row {index + 1}")
        rows.append({"stock_id": stock_id, "period_start": start, "period_end": end,
                     "source": source, "announcement_date": announced})
    if rows:
        _normalize_periods(pd.DataFrame(rows))
    return rows


def _records_from_twse(payload: dict[str, Any]) -> list[dict[str, str]]:
    if str(payload.get("stat") or "").upper() != "OK":
        raise RuntimeError(str(payload.get("stat") or "TWSE response status is not OK"))
    return _parse_official_table(payload, "TWSE")


def _records_from_tpex(payload: dict[str, Any]) -> list[dict[str, str]]:
    if str(payload.get("stat") or "").upper() != "OK":
        raise RuntimeError(str(payload.get("stat") or "TPEx response status is not OK"))
    tables = list(payload.get("tables") or [])
    if len(tables) != 1:
        raise RuntimeError("TPEx response missing tables")
    return _parse_official_table(tables[0], "TPEx")


def _verify_query_range(payload: dict[str, Any], start: str, end: str, source: str) -> None:
    if source == "TWSE":
        # Title carries the queried range in parentheses plus a trailing
        # "資訊更新日期：<today>" line (observed 2026-09-07); only the
        # parenthesised "A 至 B" pair is the query range.
        match = re.search(r"\((\d{2,4}/\d{2}/\d{2})\s*至\s*(\d{2,4}/\d{2}/\d{2})\)", str(payload.get("title", "")))
        actual = [_roc_date_to_iso(value) for value in match.groups()] if match else []
    else:
        dates = str(payload.get("date", "")).split("~")
        actual = [f"{value[:4]}-{value[4:6]}-{value[6:8]}" for value in dates if len(value) == 8]
    if actual != [start, end]:
        raise RuntimeError(f"{source} response date range {actual} != requested {[start, end]}")


def _fetch_twse_periods(start: str, end: str, timeout_seconds: int) -> list[dict[str, str]]:
    response = requests.get(
        TWSE_PUNISH_URL,
        params={
            "response": "json",
            "startDate": start.replace("-", ""),
            "endDate": end.replace("-", ""),
            "querytype": "3",
            "sortKind": "STKNO",
        },
        headers=REQUEST_HEADERS,
        timeout=timeout_seconds,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    _verify_query_range(payload, start, end, "TWSE")
    return _records_from_twse(payload)


def _fetch_tpex_periods(start: str, end: str, timeout_seconds: int) -> list[dict[str, str]]:
    response = requests.get(
        TPEX_DISPOSAL_URL,
        params={
            "response": "json",
            "startDate": start.replace("-", "/"),
            "endDate": end.replace("-", "/"),
            "type": "all",
            "reason": "-1",
            "measure": "-1",
            "order": "code",
        },
        headers=REQUEST_HEADERS,
        timeout=timeout_seconds,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    _verify_query_range(payload, start, end, "TPEx")
    return _records_from_tpex(payload)


def fetch_disposition_periods(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    max_retries: int = 3,
    retry_delay_seconds: int = 5,
    timeout_seconds: int = 30,
) -> tuple[pd.DataFrame, str | None, int, dict[str, Any]]:
    """Fetch disposition periods from official TWSE and TPEx endpoints.

    Returns ``(periods, error_message, retry_count, source_status)``.
    ``retry_count`` is the number of failed attempts before the final outcome.
    """

    today = _today()
    start = start_date or (today - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    end = end_date or today.isoformat()
    if date.fromisoformat(start) > date.fromisoformat(end) or date.fromisoformat(end) > today:
        raise ValueError("disposition query must be ordered and cannot end in the future")
    # Official start/end filters disposition periods, not publication dates.
    # Query through the next session to include already-announced T+1 periods,
    # then enforce the original knowledge cutoff using announcement_date.
    coverage_end = next_taiwan_trading_day(date.fromisoformat(end)).isoformat()

    last_error: str | None = None
    last_source_status: dict[str, Any] = {}
    attempts = max(1, int(max_retries))
    for attempt in range(1, attempts + 1):
        rows = []
        source_status: dict[str, Any] = {}
        fetchers = {
            "TWSE": _fetch_twse_periods,
            "TPEx": _fetch_tpex_periods,
        }
        for source, fetcher in fetchers.items():
            try:
                source_rows = fetcher(start, coverage_end, timeout_seconds)
                if any(row["period_end"] < start for row in source_rows):
                    raise RuntimeError("official rows fall outside requested period coverage")
                source_rows = [row for row in source_rows if row["announcement_date"] <= end]
                rows.extend(source_rows)
                source_status[source] = {"status": "OK", "rows": len(source_rows)}
            except Exception as exc:
                source_status[source] = {"status": "FAILED", "error": str(exc)}

        failed_sources = [
            source
            for source, detail in source_status.items()
            if detail.get("status") != "OK"
        ]
        last_source_status = source_status
        if not failed_sources:
            frame = _normalize_periods(pd.DataFrame(rows)) if rows else _empty_periods()
            return frame, None, attempt - 1, source_status

        last_error = "incomplete official TWSE/TPEx disposition response"
        if failed_sources:
            last_error += ": " + "; ".join(
                f"{source}: {source_status[source].get('error')}"
                for source in failed_sources
            )
        if attempt < attempts:
            time.sleep(max(0, int(retry_delay_seconds)))

    return _empty_periods(), last_error or "unknown fetch error", attempts, last_source_status


def _active_from_periods(periods: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    if periods.empty:
        return _empty_periods()

    check_date = as_of or _today()
    out = resolve_disposition_periods(periods, knowledge_as_of=as_of or datetime.now())
    end = pd.to_datetime(out["period_end"], errors="coerce").dt.date
    # Keep current and already-announced future periods so T+1 gates can fail
    # closed when the next session is covered by a disposition period.
    active = out[end >= check_date].copy()
    active = active.sort_values(["stock_id", "period_start", "period_end"])
    # Keep overlapping/current + already-announced future periods for one code.
    # Collapsing by stock_id can erase a current interval in favor of a later one.
    active = active.drop_duplicates(["stock_id", "period_start", "period_end"])
    return active.reset_index(drop=True)


def _build_report(
    *,
    status: str,
    periods: pd.DataFrame,
    active: pd.DataFrame,
    error_message: str | None,
    retry_count: int,
    previous_report: dict[str, Any] | None,
    source_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_date = _today().isoformat()
    previous_success = None
    if previous_report:
        previous_success = previous_report.get("last_successful_effective_date")
        if not previous_success and previous_report.get("status") == "OK":
            previous_success = previous_report.get("effective_date")

    source_status = source_status or {}
    last_success = effective_date if status == "OK" else previous_success
    missing_sources = [
        source
        for source in SOURCE_NAMES
        if source_status.get(source, {}).get("status") != "OK"
    ]
    if not missing_sources and status not in {"OK", "DEGRADED"}:
        missing_sources = ["OfficialDisposition"]
    gate_action = "ALLOW_T1_AND_DUAL" if status == "OK" else "CLOSED_FOR_T1_AND_DUAL"

    return {
        "schema_version": 2,
        "status": status,
        "fetched_at": _now_iso(),
        "effective_date": effective_date,
        "last_successful_effective_date": last_success,
        "source_counts": {
            "OfficialDisposition": int(len(periods)) if status in {"OK", "DEGRADED"} else "FAILED",
            "active_disposition": int(len(active)) if status in {"OK", "DEGRADED"} else "STALE",
        },
        "missing_sources": missing_sources,
        "source_status": source_status,
        "last_complete_source_status": source_status if status == "OK" else (
            (previous_report or {}).get("last_complete_source_status")
            or ((previous_report or {}).get("source_status") if (previous_report or {}).get("status") == "OK" else {})
        ),
        "unsupported_sources": [
            "attention",
            "full_delivery",
            "suspended",
        ],
        "retry_count": int(retry_count),
        "gate_action": gate_action,
        "error_message": error_message,
        "outputs": {
            "active_snapshot": OUTPUT_PATH,
            "disposition_periods": PERIODS_PATH,
            "status_report": STATUS_REPORT_PATH,
        },
    }


def update_disposition_status(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    max_retries: int = 3,
    retry_delay_seconds: int = 5,
) -> dict[str, Any]:
    """Fetch, persist, and report disposition-status health."""

    previous_report = _load_previous_report()
    periods, error_message, retry_count, source_status = fetch_disposition_periods(
        start_date=start_date,
        end_date=end_date,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )
    # A full-history refresh must not silently shrink one market to a fragment.
    # Explicitly narrowed research requests are not comparable to the baseline.
    if not error_message and previous_report and start_date is None and end_date is None:
        previous_sources = previous_report.get("last_complete_source_status") or previous_report.get("source_status", {})
        for source in SOURCE_NAMES:
            previous_count = previous_sources.get(source, {}).get("rows")
            current_count = source_status.get(source, {}).get("rows")
            if isinstance(previous_count, int) and previous_count > 0 and current_count < previous_count * 0.8:
                error_message = f"{source} partial-content: {current_count} < 80% of previous {previous_count}"
                source_status[source] = {"status": "FAILED", "rows": current_count, "error": error_message}
                break

    # Never replace a complete file with a single-market subset.
    if error_message:
        status = "STALE" if os.path.exists(PERIODS_PATH) or os.path.exists(OUTPUT_PATH) else "FAILED"
        active = _empty_periods()
        if os.path.exists(OUTPUT_PATH):
            try:
                active = pd.read_csv(OUTPUT_PATH, dtype=str)
            except Exception as exc:
                error_message += f"; previous active snapshot unreadable: {exc}"
        if os.path.exists(PERIODS_PATH):
            try:
                periods = pd.read_csv(PERIODS_PATH, dtype=str)
            except Exception as exc:
                error_message += f"; previous period history unreadable: {exc}"
                periods = _empty_periods()
        report = _build_report(
            status=status,
            periods=periods,
            active=active,
            error_message=error_message,
            retry_count=retry_count,
            previous_report=previous_report,
            source_status=source_status,
        )
        _atomic_write_json(STATUS_REPORT_PATH, report)
        return report

    active = _active_from_periods(periods)
    # Publish a closed state first: a crash between the two atomic CSV writes
    # must not leave an old OK report advertising a mixed data generation.
    _atomic_write_json(STATUS_REPORT_PATH, _build_report(
        status="UPDATING", periods=periods, active=active, error_message="publishing new generation",
        retry_count=retry_count, previous_report=previous_report, source_status=source_status))
    _atomic_write_csv(PERIODS_PATH, periods)
    _atomic_write_csv(OUTPUT_PATH, active)
    status = "OK"
    report = _build_report(
        status=status,
        periods=periods,
        active=active,
        error_message=error_message,
        retry_count=retry_count,
        previous_report=previous_report,
        source_status=source_status,
    )
    report["query_start_date"] = start_date or (_today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    report["query_end_date"] = end_date or _today().isoformat()
    report["disposition_coverage_end"] = next_taiwan_trading_day(date.fromisoformat(report["query_end_date"])).isoformat()
    report["empty_verified"] = periods.empty
    report["period_semantics"] = "raw_original_announcements; corrections applied by knowledge_as_of"
    report["output_sha256"] = {}
    for label, path in (("disposition_periods", PERIODS_PATH), ("active_snapshot", OUTPUT_PATH)):
        with open(path, "rb") as stream:
            report["output_sha256"][label] = hashlib.sha256(stream.read()).hexdigest()
    _atomic_write_json(STATUS_REPORT_PATH, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch TW disposition periods and status health.")
    parser.add_argument("--start-date", default=None, help="Optional official-source start_date override.")
    parser.add_argument("--end-date", default=None, help="Optional official-source end_date override.")
    parser.add_argument("--max-retries", type=int, default=3, help="Fetch retry attempts.")
    parser.add_argument("--retry-delay-seconds", type=int, default=5, help="Delay between retries.")
    parser.add_argument(
        "--retry-now",
        action="store_true",
        help="Compatibility flag for manual runbooks; performs the normal fetch now.",
    )
    args = parser.parse_args(argv)

    print("Updating disposition periods and special-status health...")
    report = update_disposition_status(
        start_date=args.start_date,
        end_date=args.end_date,
        max_retries=args.max_retries,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    print(
        "  status={status} active={active} periods={periods} gate={gate}".format(
            status=report.get("status"),
            active=report.get("source_counts", {}).get("active_disposition"),
            periods=report.get("source_counts", {}).get("OfficialDisposition"),
            gate=report.get("gate_action"),
        )
    )
    if report.get("error_message"):
        print(f"  error={report.get('error_message')}")
    return 0 if report.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
