"""Build Taiwan-stock news intelligence reports.

Examples:
    python scripts/tw_news_intel.py --scope top --limit 6 --days 7 --write-report
    python scripts/tw_news_intel.py --ticker 2330 --name 台積電 --write-report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.services.tw_news_intel_service import (  # noqa: E402
    build_news_intel_dashboard,
    build_ticker_news_intel,
    write_news_intel_artifacts,
)


def _risk_counts_text(counts: dict[str, int]) -> str:
    return " / ".join(
        f"{key}={int(counts.get(key, 0))}"
        for key in ("red", "yellow", "green")
    )


def _print_ticker_report(report: dict[str, object]) -> None:
    print(
        "[tw-news-intel] {ticker} {name} verdict={verdict} risk={risk} web={web}".format(
            ticker=report.get("ticker") or "-",
            name=report.get("name") or "",
            verdict=report.get("verdict") or "-",
            risk=_risk_counts_text(report.get("risk_counts") or {}),
            web=report.get("web_search_status") or "-",
        )
    )
    for item in (report.get("items") or [])[:5]:
        print(
            "  - [{risk}] {dimension} | {date} | {source} | {title}".format(
                risk=item.get("risk_level") or "-",
                dimension=item.get("dimension_label") or item.get("dimension") or "-",
                date=item.get("published_date") or item.get("date_status") or "-",
                source=item.get("source") or item.get("provider") or "-",
                title=item.get("title") or "-",
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Taiwan-stock news intelligence reports.")
    parser.add_argument("--ticker", help="Single ticker mode, e.g. 2330.")
    parser.add_argument("--name", help="Optional stock name for single ticker mode.")
    parser.add_argument("--scope", choices=["top", "holdings"], default="top")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-web", action="store_true", help="Disable web provider fallback.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON and Markdown artifacts.")
    args = parser.parse_args()

    include_web = not args.no_web
    if args.ticker:
        payload = build_ticker_news_intel(
            ticker=args.ticker,
            name=args.name,
            days=args.days,
            limit=args.limit,
            include_web=include_web,
        )
        _print_ticker_report(payload["report"])
    else:
        payload = build_news_intel_dashboard(
            scope=args.scope,
            limit=args.limit,
            days=args.days,
            include_web=include_web,
        )
        summary = payload.get("summary") or {}
        print(
            "[tw-news-intel] scope={scope} tickers={tickers} items={items} risk={risk}".format(
                scope=payload.get("scope"),
                tickers=summary.get("ticker_count", 0),
                items=summary.get("item_count", 0),
                risk=_risk_counts_text(summary.get("risk_counts") or {}),
            )
        )
        for report in payload.get("reports") or []:
            _print_ticker_report(report)

    if args.write_report:
        paths = write_news_intel_artifacts(payload)
        print(f"[tw-news-intel] wrote json={paths['json']}")
        print(f"[tw-news-intel] wrote md={paths['md']}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
