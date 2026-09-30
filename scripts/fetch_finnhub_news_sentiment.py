"""Fetch Finnhub news-sentiment overlay snapshots for mapped Taiwan ADRs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from backend.services.finnhub_sentiment_service import fetch_all_sentiments  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Finnhub news-sentiment overlay snapshots.")
    parser.add_argument("--ticker", action="append", help="Taiwan ticker to fetch; can be repeated.")
    args = parser.parse_args()

    tickers = {str(value).strip() for value in args.ticker or [] if str(value).strip()} or None
    payload = fetch_all_sentiments(tickers=tickers)
    print(
        "[finnhub] status={status} mapped={mapped} ok={ok} latest={latest}".format(
            status=payload.get("status"),
            mapped=payload.get("mapped_ticker_count", 0),
            ok=payload.get("ok_count", 0),
            latest=payload.get("latest_json_path"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
