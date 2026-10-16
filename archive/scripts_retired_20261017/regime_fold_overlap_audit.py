"""Compare REQ-009 regime fold Top30 artifacts with production predictions.

Research-only. This script reads the fold Top30 artifact emitted by
``scripts/train_v2.py`` and compares each fold's month-end Top30 with the
saved production ``predictions_YYYY-MM-DD.csv`` artifact on the same date.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402


def _latest_fold_artifact() -> Path:
    paths = sorted(Path(REPORT_DIR).glob("v2_fold_top30_*.csv"))
    if not paths:
        raise FileNotFoundError(f"No v2_fold_top30_*.csv found under {REPORT_DIR}")
    return paths[-1]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if np.isfinite(number) else None


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def _prediction_top30(prediction_date: str) -> pd.DataFrame | None:
    path = Path(MODEL_DIR) / f"predictions_{prediction_date}.csv"
    if not path.exists():
        return None
    predictions = pd.read_csv(path, dtype={"ticker": str})
    if predictions.empty:
        return predictions
    if "sector" in predictions.columns:
        selected = apply_sector_cap(predictions, top_n=30)
    else:
        selected = predictions.nlargest(30, "leaderboard_score").copy()
    selected = selected.copy()
    selected["ticker"] = selected["ticker"].astype(str)
    return selected.head(30)


def run_overlap_audit(fold_artifact: Path, output_prefix: str | None = None) -> dict:
    fold = pd.read_csv(fold_artifact, dtype={"ticker": str})
    if fold.empty:
        raise ValueError(f"Fold artifact is empty: {fold_artifact}")

    rows: list[dict[str, Any]] = []
    for month, group in fold.groupby("month", sort=True):
        group = group.copy()
        prediction_date = str(pd.to_datetime(group["date"]).max().date())
        production = _prediction_top30(prediction_date)
        if production is None or production.empty:
            rows.append(
                {
                    "month": month,
                    "prediction_date": prediction_date,
                    "status": "missing_production_prediction",
                    "fold_count": int(len(group)),
                    "production_count": 0,
                    "overlap_count": 0,
                    "overlap_pct": np.nan,
                    "fold_only_tickers": "",
                    "production_only_tickers": "",
                    "fold_top30_avg_return_20d": group["trade_return_20d"].mean(),
                    "fold_top30_avg_excess_20d": group["trade_excess_return_20d"].mean(),
                    "production_path": str(Path(MODEL_DIR) / f"predictions_{prediction_date}.csv"),
                }
            )
            continue

        fold_tickers = set(group["ticker"].astype(str))
        prod_tickers = set(production["ticker"].astype(str))
        overlap = sorted(fold_tickers & prod_tickers)
        fold_only = sorted(fold_tickers - prod_tickers)
        prod_only = sorted(prod_tickers - fold_tickers)
        rows.append(
            {
                "month": month,
                "prediction_date": prediction_date,
                "status": "ok",
                "fold_count": int(len(fold_tickers)),
                "production_count": int(len(prod_tickers)),
                "overlap_count": int(len(overlap)),
                "overlap_pct": len(overlap) / max(1, min(len(fold_tickers), len(prod_tickers))),
                "fold_only_tickers": ",".join(fold_only),
                "production_only_tickers": ",".join(prod_only),
                "fold_top30_avg_return_20d": group["trade_return_20d"].mean(),
                "fold_top30_avg_excess_20d": group["trade_excess_return_20d"].mean(),
                "production_path": str(Path(MODEL_DIR) / f"predictions_{prediction_date}.csv"),
            }
        )

    summary = pd.DataFrame(rows)
    if output_prefix is None:
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")
        output_prefix = f"req009_regime_fold_overlap_{stamp}"
    out_base = Path(REPORT_DIR) / output_prefix
    csv_path = out_base.with_suffix(".csv")
    json_path = out_base.with_suffix(".json")
    md_path = out_base.with_suffix(".md")

    summary.to_csv(csv_path, index=False, encoding="utf-8")

    ok = summary[summary["status"] == "ok"].copy()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fold_artifact": str(fold_artifact),
        "months_compared": int(len(ok)),
        "mean_overlap_pct": float(ok["overlap_pct"].mean()) if not ok.empty else None,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# REQ-009 Regime Fold Overlap Audit",
        "",
        f"- Fold artifact: `{fold_artifact}`",
        f"- Months compared with production artifacts: {len(ok)} / {len(summary)}",
        f"- Mean overlap: {_fmt_pct(ok['overlap_pct'].mean()) if not ok.empty else '-'}",
        "",
        "| Month | Date | Status | Overlap | Fold 20D | Fold Excess |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {month} | {prediction_date} | {status} | {overlap_count}/{fold_count} ({overlap_pct}) | {ret} | {excess} |".format(
                month=row["month"],
                prediction_date=row["prediction_date"],
                status=row["status"],
                overlap_count=row["overlap_count"],
                fold_count=row["fold_count"],
                overlap_pct=_fmt_pct(row["overlap_pct"], 0),
                ret=_fmt_pct(row["fold_top30_avg_return_20d"]),
                excess=_fmt_pct(row["fold_top30_avg_excess_20d"]),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "csv": str(csv_path),
        "json": str(json_path),
        "md": str(md_path),
        "months_compared": int(len(ok)),
        "mean_overlap_pct": float(ok["overlap_pct"].mean()) if not ok.empty else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fold-artifact", default=None)
    parser.add_argument("--output-prefix", default=None)
    args = parser.parse_args()

    fold_artifact = Path(args.fold_artifact) if args.fold_artifact else _latest_fold_artifact()
    result = run_overlap_audit(fold_artifact, output_prefix=args.output_prefix)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
