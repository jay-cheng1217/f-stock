"""Backtest Finnhub news sentiment overlay using archived point-in-time snapshots.

This script intentionally refuses to promote when only current Finnhub summary
data is available. Reusing today's sentiment for historical trades would be
lookahead.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from backend.services.finnhub_sentiment_service import DATA_DIR, REPORT_DIR  # noqa: E402

LATEST_JSON = REPORT_DIR / "finnhub_news_overlay_backtest_latest.json"
LATEST_MD = REPORT_DIR / "finnhub_news_overlay_backtest_latest.md"


def _latest_fold_path() -> Path | None:
    paths = sorted((BASE_DIR / "ml" / "reports").glob("v2_fold_top30_*.csv"))
    return paths[-1] if paths else None


def _snapshot_rows() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(DATA_DIR.glob("finnhub_news_sentiment_*.json")):
        if path.name.endswith("_latest.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        generated_at = str(payload.get("generated_at") or "")
        snapshot_date = generated_at[:10] if generated_at else path.stem.rsplit("_", 1)[-1]
        for record in payload.get("records") or []:
            if record.get("status") != "ok":
                continue
            scored = record.get("scored") or {}
            rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "ticker": str(record.get("ticker") or "").strip(),
                    "finnhub_score": float(scored.get("score") or 50.0),
                    "finnhub_confidence": float(scored.get("confidence") or 0.0),
                }
            )
    return pd.DataFrame(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _render_md(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Finnhub News Overlay Backtest",
            "",
            f"- Generated at: {payload.get('generated_at')}",
            f"- Promotion: {payload.get('promotion')}",
            f"- Reason: {payload.get('reason')}",
            f"- Fold source: {payload.get('fold_path') or '-'}",
            f"- Historical snapshot days: {payload.get('snapshot_days', 0)}",
            f"- Joined rows: {payload.get('joined_rows', 0)}",
            f"- Top-minus-bottom excess return: {payload.get('top_minus_bottom_excess_return')}",
            f"- Spearman: {payload.get('spearman')}",
            "",
            "Promotion gate:",
            "- Requires at least 20 historical snapshot days.",
            "- Requires at least 50 joined no-lookahead rows.",
            "- Requires top-minus-bottom excess return > 0.5pp and Spearman > 0.",
        ]
    ) + "\n"


def run_backtest() -> dict[str, Any]:
    now = datetime.now()
    generated_at = now.isoformat(timespec="seconds")
    fold_path = _latest_fold_path()
    snapshots = _snapshot_rows()
    snapshot_days = int(snapshots["snapshot_date"].nunique()) if not snapshots.empty else 0
    base_payload: dict[str, Any] = {
        "generated_at": generated_at,
        "promotion": "not_promoted",
        "dated_json_path": str(REPORT_DIR / f"finnhub_news_overlay_backtest_{now.strftime('%Y%m%d_%H%M%S')}.json"),
        "dated_md_path": str(REPORT_DIR / f"finnhub_news_overlay_backtest_{now.strftime('%Y%m%d_%H%M%S')}.md"),
        "reason": "",
        "fold_path": str(fold_path) if fold_path else None,
        "snapshot_days": snapshot_days,
        "joined_rows": 0,
        "top_minus_bottom_excess_return": None,
        "spearman": None,
    }

    if fold_path is None:
        base_payload["reason"] = "missing_v2_fold_top30_source"
        return base_payload
    if snapshot_days < 20:
        base_payload["reason"] = "insufficient_historical_finnhub_snapshots_no_lookahead_backtest_blocked"
        return base_payload

    folds = pd.read_csv(fold_path, dtype={"ticker": str})
    if folds.empty:
        base_payload["reason"] = "empty_v2_fold_source"
        return base_payload
    folds["date"] = pd.to_datetime(folds["date"], errors="coerce")
    snapshots["snapshot_date"] = pd.to_datetime(snapshots["snapshot_date"], errors="coerce")
    joined = folds.merge(snapshots, on="ticker", how="inner")
    joined = joined[joined["snapshot_date"] <= joined["date"]]
    if len(joined) < 50:
        base_payload["joined_rows"] = int(len(joined))
        base_payload["reason"] = "insufficient_joined_no_lookahead_rows"
        return base_payload

    joined["bucket"] = pd.qcut(joined["finnhub_score"], q=2, labels=["low", "high"], duplicates="drop")
    grouped = joined.groupby("bucket", observed=True)["trade_excess_return_20d"].mean()
    if {"low", "high"}.issubset(set(grouped.index.astype(str))):
        edge = float(grouped.loc["high"] - grouped.loc["low"])
    else:
        edge = 0.0
    spearman = float(joined[["finnhub_score", "trade_excess_return_20d"]].corr(method="spearman").iloc[0, 1])
    promoted = edge > 0.005 and spearman > 0
    base_payload.update(
        {
            "promotion": "promoted" if promoted else "not_promoted",
            "reason": "passed_backtest_gate" if promoted else "failed_edge_or_rank_correlation_gate",
            "joined_rows": int(len(joined)),
            "top_minus_bottom_excess_return": round(edge, 6),
            "spearman": round(spearman, 6),
        }
    )
    return base_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest Finnhub news-sentiment overlay.")
    parser.parse_args()
    payload = run_backtest()
    _write_json(LATEST_JSON, payload)
    _write_text(LATEST_MD, _render_md(payload))
    _write_json(Path(str(payload["dated_json_path"])), payload)
    _write_text(Path(str(payload["dated_md_path"])), _render_md(payload))
    print(
        "[finnhub-backtest] promotion={promotion} reason={reason} joined={joined}".format(
            promotion=payload.get("promotion"),
            reason=payload.get("reason"),
            joined=payload.get("joined_rows"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
