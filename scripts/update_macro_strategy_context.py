"""Refresh the daily macro strategy context artifact."""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backend.services.macro_event_service import write_macro_strategy_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh macro event and market-sentiment strategy context.")
    parser.add_argument("--as-of", default=None, help="As-of date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--horizon", type=int, default=20, help="Forward event horizon in calendar days.")
    args = parser.parse_args(argv)

    payload = write_macro_strategy_context(as_of=args.as_of, horizon_days=args.horizon)
    print(
        json.dumps(
            {
                "status": "ok",
                "as_of": payload["as_of"],
                "macro_pressure_score": payload["strategy"]["macro_pressure_score"],
                "strategy_action": payload["strategy"]["action"],
                "event_count": payload["events"]["event_count"],
                "market_sentiment": payload["market_sentiment"]["label"],
                "paths": payload["paths"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
