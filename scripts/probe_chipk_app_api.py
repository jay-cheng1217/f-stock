"""Probe CMoney ChipK desktop-app API for broker-detail data.

The script uses the logged-in AppViewer WebView session from the local machine.
It never prints or stores cookies, JWTs, passwords, account ids, or raw session
values.  Generated outputs contain only non-secret API status and market/broker
rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.chipk_app_api import (  # noqa: E402
    CHIPK_BROKER_DEFAULT_PERIODS,
    CHIPK_BROKER_TOP_FUN_ID,
    chipk_broker_top_params,
    find_chipk_cookie_store,
    iter_default_auth_pairs,
    load_chipk_session,
    normalize_broker_top_rows,
    request_chipk_api,
    resolve_session_value,
)

DEFAULT_OUTPUT_DIR = BASE_DIR / "ml" / "data" / "chipk" / "app_api"


def _read_tickers(args: argparse.Namespace) -> list[str]:
    tickers: list[str] = []
    if args.ticker:
        tickers.extend(args.ticker)
    if args.tickers_file:
        path = Path(args.tickers_file)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            value = line.strip().split(",", 1)[0]
            if value and value.lower() != "ticker":
                tickers.append(value)
    normalized = []
    for ticker in tickers:
        value = str(ticker).strip()
        if value:
            normalized.append(value.zfill(4) if value.isdigit() else value)
    return sorted(dict.fromkeys(normalized))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _probe_one(
    *,
    ticker: str,
    date: str,
    period: str,
    fun_id: int,
    session,
    guid_source: str | None,
    auth_source: str | None,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params = chipk_broker_top_params(ticker, date, period) if fun_id == CHIPK_BROKER_TOP_FUN_ID else f"{ticker},{date},{period},1"
    auth_pairs = []
    if guid_source and auth_source:
        guid = resolve_session_value(session, guid_source)
        auth = resolve_session_value(session, auth_source)
        if guid and auth:
            auth_pairs.append((guid_source, auth_source, guid, auth))
    if not auth_pairs:
        auth_pairs = list(iter_default_auth_pairs(session))

    attempts = []
    best_rows: list[dict[str, Any]] = []
    for guid_label, auth_label, guid, auth in auth_pairs:
        # Secure TLS first.  Some CMoney desktop endpoints fail local certificate
        # validation in requests; retry insecure only as a captured diagnostic.
        result, decoded = request_chipk_api(
            fun_id=fun_id,
            fun_params=params,
            guid=guid,
            auth_token=auth,
            guid_label=guid_label,
            auth_label=auth_label,
            timeout=timeout,
            tls_verify=True,
        )
        if result.status == "request_error" and "SSLError" in result.message:
            result, decoded = request_chipk_api(
                fun_id=fun_id,
                fun_params=params,
                guid=guid,
                auth_token=auth,
                guid_label=guid_label,
                auth_label=auth_label,
                timeout=timeout,
                tls_verify=False,
            )
        attempts.append(result.to_dict())
        if decoded and decoded.rows:
            best_rows = normalize_broker_top_rows(decoded.rows, ticker=ticker, asof_date=date, period=period)
            break

    status = "ok" if best_rows else "no_rows"
    summary = {
        "ticker": ticker,
        "date": date,
        "period": str(period),
        "fun_id": fun_id,
        "status": status,
        "row_count": len(best_rows),
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    return summary, best_rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    tickers = _read_tickers(args)
    if not tickers:
        raise ValueError("at least one --ticker or --tickers-file row is required")
    date = args.date or datetime.now().strftime("%Y%m%d")
    periods = [str(p) for p in (args.period or CHIPK_BROKER_DEFAULT_PERIODS)]

    store = find_chipk_cookie_store(Path(args.webview_root) if args.webview_root else None)
    if store is None:
        raise FileNotFoundError("CMoney AppViewer WebView cookie store not found")
    session = load_chipk_session(store)

    all_summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for ticker in tickers:
        for period in periods:
            summary, rows = _probe_one(
                ticker=ticker,
                date=date,
                period=period,
                fun_id=args.fun_id,
                session=session,
                guid_source=args.guid_source,
                auth_source=args.auth_source,
                timeout=args.timeout,
            )
            all_summaries.append(summary)
            all_rows.extend(rows)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": date,
        "tickers": tickers,
        "periods": periods,
        "fun_id": args.fun_id,
        "session_sources_available": session.available_sources(),
        "cookie_store": {
            "local_state": ".../EBWebView/Local State",
            "cookies": ".../EBWebView/Default/Network/Cookies",
        },
        "summary": all_summaries,
        "total_rows": len(all_rows),
        "data_boundary": (
            "session values are used only in memory; output includes API status and "
            "non-secret market/broker rows only"
        ),
    }

    if args.write:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"chipk_app_api_{date}_{datetime.now().strftime('%H%M%S_%f')}"
        summary_path = out_dir / f"{stem}.json"
        rows_path = out_dir / f"{stem}_broker_rows.csv"
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(rows_path, all_rows)
        payload["output_summary_json"] = str(summary_path)
        payload["output_rows_csv"] = str(rows_path)

    if args.verbose_attempts:
        display_payload = payload
    else:
        display_payload = {
            **payload,
            "summary": [
                {
                    "ticker": item["ticker"],
                    "date": item["date"],
                    "period": item["period"],
                    "fun_id": item["fun_id"],
                    "status": item["status"],
                    "row_count": item["row_count"],
                    "attempt_count": item["attempt_count"],
                    "attempt_statuses": [
                        {
                            "status": attempt.get("status"),
                            "http_status": attempt.get("http_status"),
                            "api_state": attempt.get("api_state"),
                            "row_count": attempt.get("row_count"),
                            "guid_label": attempt.get("guid_label"),
                            "auth_label": attempt.get("auth_label"),
                            "tls_verify": attempt.get("tls_verify"),
                        }
                        for attempt in item.get("attempts", [])
                    ],
                }
                for item in all_summaries
            ],
        }
    print(json.dumps(display_payload, ensure_ascii=False, indent=2))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe CMoney ChipK app API")
    parser.add_argument("--ticker", action="append", help="Ticker to probe; repeatable")
    parser.add_argument("--tickers-file", help="Optional CSV/text file with ticker in first column")
    parser.add_argument("--date", help="YYYYMMDD. Defaults to today.")
    parser.add_argument("--period", action="append", help="Broker period; repeatable. Defaults to app broker Top15 1/5/10.")
    parser.add_argument("--fun-id", type=int, default=CHIPK_BROKER_TOP_FUN_ID)
    parser.add_argument("--guid-source", choices=["device", "ltcid", "user_guid", "cm_at", "cm_idt", "idp_session", "cm_rt"])
    parser.add_argument("--auth-source", choices=["device", "ltcid", "user_guid", "cm_at", "cm_idt", "idp_session", "cm_rt"])
    parser.add_argument("--webview-root", help="Override AppViewer EBWebView root")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--write", action="store_true", help="Write sanitized summary and broker rows under output dir")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--verbose-attempts", action="store_true", help="Print full attempt details to stdout")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
