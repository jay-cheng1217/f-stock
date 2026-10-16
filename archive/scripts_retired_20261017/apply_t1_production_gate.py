"""Apply the T+1 production gate to an existing prediction CSV.

This is a lightweight recovery utility for already-generated CSVs. Normal live
prediction applies the same gate inside ``ml.predict_t1`` before writing a new
file, but this script lets the scheduler or an operator freeze stale artifacts
without rebuilding the full feature snapshot.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR
from ml.t1_production_gate import evaluate_t1_production_gate


def _selected_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    text = series.fillna(False).astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes"})


def _resolve_prediction_path(pred_date: str | None, prediction_file: str | None) -> str:
    if prediction_file:
        path = os.path.abspath(prediction_file)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return path

    if pred_date:
        path = os.path.join(MODEL_DIR, f"predictions_t1_{pred_date}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return path

    candidates = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if not candidates:
        raise FileNotFoundError("No predictions_t1_YYYY-MM-DD.csv files found.")
    return candidates[-1]


def apply_gate_to_file(path: str, dry_run: bool = False) -> dict[str, object]:
    gate = evaluate_t1_production_gate()
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    before_selected = int(_selected_mask(df.get("selected_for_trade", pd.Series(False, index=df.index))).sum())

    df["production_gate_status"] = str(gate.get("status") or "UNKNOWN")
    df["production_gate_reason"] = str(gate.get("reason") or "")

    if bool(gate.get("closed")):
        df["selected_for_trade"] = False
        df["selection_rank"] = pd.NA
        df["position_weight"] = pd.NA
        df["recommendation"] = "觀望"
        existing_veto = df.get("veto_reason", pd.Series("", index=df.index)).fillna("").astype(str)
        gate_veto = f"T1_GATE_CLOSED({gate.get('reason') or ''}) "
        df["veto_reason"] = (gate_veto + existing_veto).str.strip()

    after_selected = int(_selected_mask(df.get("selected_for_trade", pd.Series(False, index=df.index))).sum())

    if not dry_run:
        tmp_path = path + ".tmp"
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, path)

    return {
        "path": os.path.abspath(path),
        "gate_status": gate.get("status"),
        "gate_reason": gate.get("reason"),
        "before_selected": before_selected,
        "after_selected": after_selected,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply production gate to a T+1 prediction CSV.")
    parser.add_argument("--date", default=None, help="Prediction date in YYYY-MM-DD.")
    parser.add_argument("--prediction-file", default=None, help="Explicit predictions_t1_YYYY-MM-DD.csv path.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = _resolve_prediction_path(args.date, args.prediction_file)
    result = apply_gate_to_file(path=path, dry_run=args.dry_run)
    print(
        "[t1-gate] "
        f"{os.path.basename(path)} | gate={result['gate_status']} | "
        f"selected {result['before_selected']} -> {result['after_selected']}"
    )
    if result.get("gate_reason"):
        print(f"[t1-gate] reason: {result['gate_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
