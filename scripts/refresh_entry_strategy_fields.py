"""Refresh tactical/swing entry strategy fields on an existing prediction CSV.

This is a deployment helper for UI/reporting fields only. It preserves model
scores and ranking columns, then recomputes the additive strategy columns from
the latest local daily K data and optional ChipK snapshot.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.chipk import load_latest_chipk_main_force
from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR
from ml.entry_shortwave import SHORTWAVE_OUTPUT_COLS, compute_shortwave_overlay

PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
PRESERVE_COLUMNS = [
    "leaderboard_score",
    "leaderboard_score_before_shortwave",
    "risk_adjusted_return",
    "two_stage_rank",
    "recommendation",
]
DAILY_COLUMNS = [
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "MA_5",
    "MA_20",
    "MA_60",
    "RSI_14",
    "MACDh_12_26_9",
    "K",
    "D",
    "VOL_MA_20",
]


def _latest_prediction_path(model_dir: Path) -> Path:
    candidates = [
        path
        for path in model_dir.glob("predictions_*.csv")
        if PREDICTION_RE.match(path.name)
    ]
    if not candidates:
        raise FileNotFoundError(f"No predictions_YYYY-MM-DD.csv under {model_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _normalize_ticker(value: object) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= 4:
        return text.zfill(4)
    return text


def _load_daily_rows(tickers: pd.Series, date_value: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    target_date = pd.Timestamp(date_value)
    for ticker in sorted({_normalize_ticker(value) for value in tickers.dropna().tolist()}):
        path = Path(DAILY_K_DIR) / f"{ticker}.csv"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, usecols=lambda col: col in DAILY_COLUMNS)
        except Exception:
            continue
        if "Date" not in frame.columns:
            continue
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        matched = frame.loc[frame["Date"].eq(target_date)].tail(1)
        if matched.empty:
            matched = frame.loc[frame["Date"].le(target_date)].tail(1)
        if matched.empty:
            continue
        row = matched.iloc[0].to_dict()
        row["ticker"] = ticker
        rows.append(row)
    return pd.DataFrame(rows)


def _build_snapshot(pred_df: pd.DataFrame) -> pd.DataFrame:
    snapshot = pred_df.copy()
    if "ticker" not in snapshot.columns:
        raise ValueError("prediction CSV missing ticker column")
    if "date" not in snapshot.columns:
        raise ValueError("prediction CSV missing date column")

    snapshot["_ticker_key"] = snapshot["ticker"].map(_normalize_ticker)
    date_value = str(snapshot["date"].dropna().astype(str).iloc[0])
    daily = _load_daily_rows(snapshot["_ticker_key"], date_value)
    if not daily.empty:
        daily["_ticker_key"] = daily["ticker"].map(_normalize_ticker)
        daily = daily.drop(columns=["ticker"], errors="ignore")
        snapshot = snapshot.merge(daily, on="_ticker_key", how="left", suffixes=("", "_daily"))
        for column in DAILY_COLUMNS:
            if column == "Date":
                continue
            daily_col = f"{column}_daily"
            if daily_col in snapshot.columns:
                if column in snapshot.columns:
                    snapshot[column] = snapshot[column].where(snapshot[column].notna(), snapshot[daily_col])
                else:
                    snapshot[column] = snapshot[daily_col]
        snapshot = snapshot.drop(columns=[col for col in snapshot.columns if col.endswith("_daily")], errors="ignore")
    return snapshot.drop(columns=["_ticker_key"], errors="ignore")


def refresh_entry_strategy_fields(prediction_path: Path, *, output_path: Path | None = None) -> dict[str, Any]:
    pred_df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
    original_columns = list(pred_df.columns)
    preserved = {
        column: pred_df[column].copy()
        for column in PRESERVE_COLUMNS
        if column in pred_df.columns
    }
    strategy_columns = [column for column in SHORTWAVE_OUTPUT_COLS if column in pred_df.columns]
    working_df = pred_df.drop(columns=strategy_columns, errors="ignore")
    snapshot = _build_snapshot(working_df)
    try:
        chipk_df, chipk_meta = load_latest_chipk_main_force()
        chipk_status = chipk_meta.status
    except Exception as exc:
        chipk_df = pd.DataFrame()
        chipk_status = f"error:{exc}"

    refreshed = compute_shortwave_overlay(
        working_df,
        snapshot=snapshot,
        chipk_df=chipk_df,
        rerank_enabled=False,
    )
    for column, values in preserved.items():
        refreshed[column] = values

    output_columns = original_columns + [
        column for column in SHORTWAVE_OUTPUT_COLS if column not in original_columns and column in refreshed.columns
    ]
    for column in refreshed.columns:
        if column not in output_columns:
            output_columns.append(column)
    out_path = output_path or prediction_path
    refreshed.loc[:, output_columns].to_csv(out_path, index=False, encoding="utf-8-sig")

    tactical_action = refreshed.get("tactical_action", pd.Series("", index=refreshed.index)).fillna("").astype(str)
    swing_action = refreshed.get("shortwave_action", pd.Series("", index=refreshed.index)).fillna("").astype(str)
    report = {
        "status": "ok",
        "prediction_path": str(prediction_path),
        "output_path": str(out_path),
        "rows": int(len(refreshed)),
        "prediction_date": str(refreshed["date"].dropna().astype(str).iloc[0]) if "date" in refreshed.columns and not refreshed.empty else None,
        "chipk_status": chipk_status,
        "tactical_actions": {str(k): int(v) for k, v in tactical_action.value_counts().items()},
        "swing_actions": {str(k): int(v) for k, v in swing_action.value_counts().items()},
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh tactical/swing entry strategy fields on prediction CSV.")
    parser.add_argument("--prediction-file", default=None, help="Path to predictions_YYYY-MM-DD.csv. Defaults to latest.")
    parser.add_argument("--output-file", default=None, help="Optional output CSV. Defaults to in-place.")
    parser.add_argument("--report-file", default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    prediction_path = Path(args.prediction_file) if args.prediction_file else _latest_prediction_path(Path(MODEL_DIR))
    output_path = Path(args.output_file) if args.output_file else None
    report = refresh_entry_strategy_fields(prediction_path, output_path=output_path)

    report_path = Path(args.report_file) if args.report_file else Path(REPORT_DIR) / "entry_strategy_refresh_latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "report": str(report_path), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
