"""Market regime gate for unified production signals."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR

STATE_OPEN = "OPEN"
STATE_CAUTION = "CAUTION"
STATE_CLOSED = "CLOSED"

ACTION_ALLOW = "ALLOW_NEW_POSITIONS"
ACTION_LIMIT = "LIMIT_20D_TOP10_AND_BLOCK_T1"
ACTION_BLOCK = "BLOCK_NEW_POSITIONS"

REPORT_JSON = "market_regime_latest.json"
REPORT_MD = "market_regime_latest.md"

TWII_FILE = "index_TWII.csv"
MA20_SLOPE_DAYS = 5
OPEN_BREADTH_MIN = 0.50
CLOSED_BREADTH_MAX = 0.40
SELECTIVE_BREADTH_MIN = 0.35


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _round_or_none(value: Any, digits: int = 6) -> float | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    return round(numeric, digits)


def _pct(value: Any, digits: int = 2) -> str:
    numeric = _to_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric * 100:.{digits}f}%"


def _load_twii() -> pd.DataFrame:
    path = Path(INDEX_DIR) / TWII_FILE
    if not path.exists():
        raise FileNotFoundError(f"TWII index file not found: {path}")

    df = pd.read_csv(path)
    required = {"Date", "Close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    df["MA20"] = df["Close"].rolling(20, min_periods=20).mean()
    df["MA60"] = df["Close"].rolling(60, min_periods=60).mean()
    df["MA20_slope_5d"] = df["MA20"] / df["MA20"].shift(MA20_SLOPE_DAYS) - 1.0
    return df


def _resolve_as_of_date(twii: pd.DataFrame, as_of_date: str | None) -> pd.Timestamp:
    if as_of_date:
        target = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(target):
            raise ValueError(f"Invalid as_of_date: {as_of_date}")
        subset = twii[twii["Date"] <= target]
    else:
        subset = twii
    if subset.empty:
        raise ValueError(f"No TWII data on or before {as_of_date or 'latest'}")
    return pd.Timestamp(subset.iloc[-1]["Date"]).normalize()


def _stock_latest_snapshot(path: Path, as_of: pd.Timestamp) -> tuple[bool, bool]:
    try:
        df = pd.read_csv(path, usecols=lambda column: column in {"Date", "Close", "MA_20"})
    except Exception:
        return False, False
    if "Date" not in df.columns or "Close" not in df.columns:
        return False, False

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    df = df[df["Date"] <= as_of]
    if df.empty:
        return False, False
    if "MA_20" in df.columns:
        df["MA_20"] = pd.to_numeric(df["MA_20"], errors="coerce")
    else:
        df["MA_20"] = np.nan
    if pd.isna(df["MA_20"].iloc[-1]):
        df["MA_20"] = df["Close"].rolling(20, min_periods=20).mean()

    row = df.iloc[-1]
    close = _to_float(row.get("Close"))
    ma20 = _to_float(row.get("MA_20"))
    if close is None or ma20 is None or ma20 <= 0:
        return False, False
    return True, close > ma20


def _compute_market_breadth(as_of: pd.Timestamp) -> dict[str, Any]:
    total = 0
    above_ma20 = 0
    data_dir = Path(DAILY_K_DIR)
    for path in data_dir.glob("*.csv"):
        valid, is_above = _stock_latest_snapshot(path, as_of)
        if not valid:
            continue
        total += 1
        above_ma20 += int(is_above)

    ratio = above_ma20 / total if total > 0 else None
    return {
        "total_names": total,
        "above_ma20_count": above_ma20,
        "breadth_ma20_ratio": ratio,
    }


def _classify_regime(
    *,
    twii_close: float | None,
    twii_ma20: float | None,
    twii_ma60: float | None,
    twii_ma20_slope_5d: float | None,
    breadth_ma20_ratio: float | None,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if (
        twii_close is None
        or twii_ma20 is None
        or twii_ma60 is None
        or twii_ma20_slope_5d is None
        or breadth_ma20_ratio is None
    ):
        return STATE_CLOSED, ACTION_BLOCK, ["insufficient_market_regime_data"]

    if twii_close < twii_ma60:
        reasons.append("twii_below_ma60")
        if breadth_ma20_ratio < CLOSED_BREADTH_MAX:
            reasons.append(f"breadth_ma20_below_{CLOSED_BREADTH_MAX:.0%}")
        return STATE_CLOSED, ACTION_BLOCK, reasons

    if breadth_ma20_ratio < CLOSED_BREADTH_MAX:
        if (
            twii_close > twii_ma20
            and twii_ma20_slope_5d > 0
            and breadth_ma20_ratio >= SELECTIVE_BREADTH_MIN
        ):
            return STATE_CAUTION, ACTION_LIMIT, [
                f"narrow_breadth_selective_open_{SELECTIVE_BREADTH_MIN:.0%}_to_{CLOSED_BREADTH_MAX:.0%}",
                "twii_above_ma20_with_positive_slope",
            ]
        return STATE_CLOSED, ACTION_BLOCK, [f"breadth_ma20_below_{CLOSED_BREADTH_MAX:.0%}"]

    if (
        twii_close > twii_ma20
        and twii_ma20_slope_5d > 0
        and breadth_ma20_ratio > OPEN_BREADTH_MIN
    ):
        return STATE_OPEN, ACTION_ALLOW, ["twii_above_ma20_with_positive_slope_and_breadth"]

    if twii_close > twii_ma60 and breadth_ma20_ratio >= CLOSED_BREADTH_MAX:
        return STATE_CAUTION, ACTION_LIMIT, ["market_not_fully_open"]

    return STATE_CLOSED, ACTION_BLOCK, ["market_not_tradable"]


def evaluate_market_regime(as_of_date: str | None = None) -> dict[str, Any]:
    twii = _load_twii()
    as_of = _resolve_as_of_date(twii, as_of_date)
    row = twii[twii["Date"] <= as_of].iloc[-1]
    breadth = _compute_market_breadth(as_of)

    twii_close = _to_float(row.get("Close"))
    twii_ma20 = _to_float(row.get("MA20"))
    twii_ma60 = _to_float(row.get("MA60"))
    twii_ma20_slope_5d = _to_float(row.get("MA20_slope_5d"))
    breadth_ratio = _to_float(breadth.get("breadth_ma20_ratio"))

    state, action, reasons = _classify_regime(
        twii_close=twii_close,
        twii_ma20=twii_ma20,
        twii_ma60=twii_ma60,
        twii_ma20_slope_5d=twii_ma20_slope_5d,
        breadth_ma20_ratio=breadth_ratio,
    )

    return {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of.date().isoformat(),
        "state": state,
        "action": action,
        "reason": "; ".join(reasons),
        "reasons": reasons,
        "thresholds": {
            "open_breadth_min": OPEN_BREADTH_MIN,
            "closed_breadth_max": CLOSED_BREADTH_MAX,
            "selective_breadth_min": SELECTIVE_BREADTH_MIN,
            "ma20_slope_days": MA20_SLOPE_DAYS,
        },
        "twii": {
            "close": _round_or_none(twii_close),
            "ma20": _round_or_none(twii_ma20),
            "ma60": _round_or_none(twii_ma60),
            "ma20_slope_5d": _round_or_none(twii_ma20_slope_5d),
            "above_ma20": bool(twii_close is not None and twii_ma20 is not None and twii_close > twii_ma20),
            "above_ma60": bool(twii_close is not None and twii_ma60 is not None and twii_close > twii_ma60),
        },
        "breadth": {
            "total_names": int(breadth["total_names"]),
            "above_ma20_count": int(breadth["above_ma20_count"]),
            "ma20_ratio": _round_or_none(breadth_ratio),
        },
    }


def _write_markdown(report: dict[str, Any], output_path: Path) -> None:
    twii = report.get("twii", {}) or {}
    breadth = report.get("breadth", {}) or {}
    lines = [
        "# Market Regime Latest",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| as_of_date | {report.get('as_of_date')} |",
        f"| state | {report.get('state')} |",
        f"| action | {report.get('action')} |",
        f"| reason | {report.get('reason') or 'ok'} |",
        f"| TWII close | {twii.get('close')} |",
        f"| TWII MA20 | {twii.get('ma20')} |",
        f"| TWII MA60 | {twii.get('ma60')} |",
        f"| TWII MA20 slope 5d | {_pct(twii.get('ma20_slope_5d'))} |",
        f"| Breadth above MA20 | {_pct(breadth.get('ma20_ratio'))} |",
        f"| Breadth names | {breadth.get('above_ma20_count')} / {breadth.get('total_names')} |",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def update_market_regime_report(
    as_of_date: str | None = None,
    report_dir: str = REPORT_DIR,
) -> dict[str, Any]:
    report = evaluate_market_regime(as_of_date=as_of_date)
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / REPORT_JSON
    md_path = output_dir / REPORT_MD
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(md_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build latest market regime report.")
    parser.add_argument("--as-of-date", default=None, help="Optional YYYY-MM-DD regime date.")
    args = parser.parse_args()
    report = update_market_regime_report(as_of_date=args.as_of_date)
    print(
        "[market-regime] "
        f"as_of={report['as_of_date']} state={report['state']} "
        f"action={report['action']} reason={report['reason'] or 'ok'} "
        f"breadth={_pct((report.get('breadth') or {}).get('ma20_ratio'))}"
    )


if __name__ == "__main__":
    main()
