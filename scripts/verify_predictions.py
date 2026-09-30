r"""
Verify historical prediction files using the canonical trade-aligned spec.

Outputs:
    F:\stock\ml\reports\prediction_tracking.json

Trade spec:
    - V2: Open[t+1] -> Close[t+20]
    - V1: Open[t+1] -> Close[t+5]
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from functools import lru_cache
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR

TRACKING_PATH = os.path.join(REPORT_DIR, "prediction_tracking.json")
PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")

TRACKING_VERSION = "trade_aligned_v2"
V2_MIN_CALENDAR_DAYS = 28
V1_MIN_CALENDAR_DAYS = 8
V2_HOLD_TRADING_DAYS = 20
V1_HOLD_TRADING_DAYS = 5

V2_SPEC_LABEL = "Open[t+1] -> Close[t+20]"
V1_SPEC_LABEL = "Open[t+1] -> Close[t+5]"
RECOMMENDATION_ORDER = ["強力買進", "建議買進", "觀望", "建議賣出", "強力賣出"]


def _load_tracking() -> dict[str, Any]:
    if os.path.exists(TRACKING_PATH):
        with open(TRACKING_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"verified": [], "summary": {}}


def _save_tracking(data: dict[str, Any]) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(TRACKING_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


@lru_cache(maxsize=4096)
def _load_history(ticker: str) -> pd.DataFrame | None:
    csv_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path, usecols=["Date", "Open", "Close"])
    except Exception:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Open"] = pd.to_numeric(df["Open"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)
    return df if not df.empty else None


def _trade_outcome(
    ticker: str,
    prediction_date: str,
    hold_trading_days: int,
) -> dict[str, Any] | None:
    history = _load_history(ticker)
    if history is None:
        return None

    pred_ts = pd.Timestamp(prediction_date)
    future = history[history["Date"] > pred_ts].reset_index(drop=True)
    if len(future) < hold_trading_days:
        return None

    entry_row = future.iloc[0]
    exit_row = future.iloc[hold_trading_days - 1]
    entry_open = float(entry_row["Open"])
    exit_close = float(exit_row["Close"])
    if entry_open <= 0:
        return None

    return {
        "entry_date": str(entry_row["Date"].date()),
        "entry_open": entry_open,
        "exit_date": str(exit_row["Date"].date()),
        "exit_close": exit_close,
        "actual_return": exit_close / entry_open - 1.0,
    }


def _verify_v2(pred_df: pd.DataFrame, prediction_date: str) -> dict[str, Any] | None:
    if "pred_return_20d" not in pred_df.columns or not pred_df["pred_return_20d"].notna().any():
        return None

    records: list[dict[str, Any]] = []
    for _, row in pred_df.iterrows():
        if pd.isna(row.get("pred_return_20d")):
            continue
        ticker = str(row["ticker"])
        trade = _trade_outcome(ticker, prediction_date, V2_HOLD_TRADING_DAYS)
        if trade is None:
            continue

        records.append(
            {
                "ticker": ticker,
                "pred_return": float(row["pred_return_20d"]),
                "actual_return": float(trade["actual_return"]),
                "recommendation": str(row.get("recommendation", "")),
                "entry_date": trade["entry_date"],
                "entry_open": trade["entry_open"],
                "exit_date": trade["exit_date"],
                "exit_close": trade["exit_close"],
            }
        )

    if not records:
        return None

    rdf = pd.DataFrame(records)
    ic = rdf["pred_return"].corr(rdf["actual_return"], method="spearman")
    mae = (rdf["pred_return"] - rdf["actual_return"]).abs().mean()

    rec_stats: dict[str, dict[str, Any]] = {}
    for rec_name in RECOMMENDATION_ORDER:
        sub = rdf[rdf["recommendation"] == rec_name]
        if sub.empty:
            continue
        rec_stats[rec_name] = {
            "count": int(len(sub)),
            "avg_predicted_return": round(float(sub["pred_return"].mean()) * 100, 2),
            "avg_actual_return": round(float(sub["actual_return"].mean()) * 100, 2),
            "win_rate": round(float((sub["actual_return"] > 0).mean()) * 100, 1),
            "hit_rate_direction": round(
                float(((sub["pred_return"] > 0) == (sub["actual_return"] > 0)).mean()) * 100, 1
            ),
        }

    rdf_sorted = rdf.sort_values("pred_return", ascending=False)
    top30 = rdf_sorted.head(30)
    bot30 = rdf_sorted.tail(30)

    return {
        "spec": V2_SPEC_LABEL,
        "n_verified": int(len(rdf)),
        "ic_spearman": round(float(ic), 4) if not pd.isna(ic) else None,
        "mae": round(float(mae) * 100, 2),
        "avg_predicted_return": round(float(rdf["pred_return"].mean()) * 100, 2),
        "avg_actual_return": round(float(rdf["actual_return"].mean()) * 100, 2),
        "overall_win_rate": round(float((rdf["actual_return"] > 0).mean()) * 100, 1),
        "top30_avg_actual": round(float(top30["actual_return"].mean()) * 100, 2),
        "bot30_avg_actual": round(float(bot30["actual_return"].mean()) * 100, 2),
        "spread": round(float((top30["actual_return"].mean() - bot30["actual_return"].mean()) * 100), 2),
        "recommendation_accuracy": rec_stats,
    }


def _verify_v1(pred_df: pd.DataFrame, prediction_date: str) -> dict[str, Any] | None:
    if "signal" not in pred_df.columns:
        return None

    records: list[dict[str, Any]] = []
    for _, row in pred_df.iterrows():
        ticker = str(row["ticker"])
        trade = _trade_outcome(ticker, prediction_date, V1_HOLD_TRADING_DAYS)
        if trade is None:
            continue

        records.append(
            {
                "ticker": ticker,
                "signal": str(row.get("signal", "")),
                "up_prob": float(row.get("up_prob", 0)),
                "actual_return": float(trade["actual_return"]),
                "entry_date": trade["entry_date"],
                "entry_open": trade["entry_open"],
                "exit_date": trade["exit_date"],
                "exit_close": trade["exit_close"],
            }
        )

    if not records:
        return None

    vdf = pd.DataFrame(records)
    signal_stats: dict[str, dict[str, Any]] = {}
    for signal in ["UP", "FLAT", "DOWN"]:
        sub = vdf[vdf["signal"] == signal]
        if sub.empty:
            continue
        if signal == "UP":
            correct = (sub["actual_return"] > 0.015).sum()
        elif signal == "DOWN":
            correct = (sub["actual_return"] < -0.015).sum()
        else:
            correct = (sub["actual_return"].abs() <= 0.015).sum()
        signal_stats[signal] = {
            "count": int(len(sub)),
            "correct": int(correct),
            "hit_rate": round(float(correct / len(sub)) * 100, 1),
            "avg_actual_return": round(float(sub["actual_return"].mean()) * 100, 2),
        }

    top30 = vdf.sort_values("up_prob", ascending=False).head(30)
    return {
        "spec": V1_SPEC_LABEL,
        "n_verified": int(len(vdf)),
        "signal_accuracy": signal_stats,
        "top30_by_up_prob_avg_return": round(float(top30["actual_return"].mean()) * 100, 2),
        "top30_win_rate": round(float((top30["actual_return"] > 0).mean()) * 100, 1),
    }


def verify_one_prediction_file(pred_path: str, today: datetime) -> dict[str, Any] | None:
    pred_df = pd.read_csv(pred_path, encoding="utf-8-sig", dtype={"ticker": str})
    if "date" not in pred_df.columns or "ticker" not in pred_df.columns or pred_df.empty:
        return None

    prediction_date = str(pred_df["date"].iloc[0])
    pred_date = datetime.strptime(prediction_date, "%Y-%m-%d")
    days_elapsed = (today - pred_date).days

    result: dict[str, Any] = {
        "prediction_date": prediction_date,
        "verified_at": today.strftime("%Y-%m-%d"),
        "days_elapsed": days_elapsed,
        "total_stocks": int(len(pred_df)),
        "tracking_version": TRACKING_VERSION,
        "trade_spec": {
            "v1": V1_SPEC_LABEL,
            "v2": V2_SPEC_LABEL,
            "friction_applied": False,
        },
    }

    if days_elapsed >= V1_MIN_CALENDAR_DAYS:
        v1_result = _verify_v1(pred_df, prediction_date)
        if v1_result is not None:
            result["v1"] = v1_result

    if days_elapsed >= V2_MIN_CALENDAR_DAYS:
        v2_result = _verify_v2(pred_df, prediction_date)
        if v2_result is not None:
            result["v2"] = v2_result

    return result if ("v1" in result or "v2" in result) else None


def compute_summary(verified_list: list[dict[str, Any]]) -> dict[str, Any]:
    v2_results = [item for item in verified_list if "v2" in item]
    v1_results = [item for item in verified_list if "v1" in item]

    summary: dict[str, Any] = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tracking_version": TRACKING_VERSION,
        "trade_spec": {
            "v1": V1_SPEC_LABEL,
            "v2": V2_SPEC_LABEL,
            "friction_applied": False,
        },
    }

    if v2_results:
        ics = [item["v2"]["ic_spearman"] for item in v2_results if item["v2"].get("ic_spearman") is not None]
        spreads = [item["v2"]["spread"] for item in v2_results]
        win_rates = [item["v2"]["overall_win_rate"] for item in v2_results]

        rec_totals: dict[str, dict[str, Any]] = {}
        for item in v2_results:
            for rec_name, stats in item["v2"].get("recommendation_accuracy", {}).items():
                if rec_name not in rec_totals:
                    rec_totals[rec_name] = {"returns": 0.0, "wins": 0, "total": 0}
                rec_totals[rec_name]["returns"] += stats["avg_actual_return"] * stats["count"]
                rec_totals[rec_name]["wins"] += int(stats["win_rate"] / 100 * stats["count"])
                rec_totals[rec_name]["total"] += stats["count"]

        rec_summary: dict[str, dict[str, Any]] = {}
        for rec_name, payload in rec_totals.items():
            if payload["total"] <= 0:
                continue
            rec_summary[rec_name] = {
                "total_predictions": int(payload["total"]),
                "historical_win_rate": round(payload["wins"] / payload["total"] * 100, 1),
                "avg_actual_return": round(payload["returns"] / payload["total"], 2),
            }

        summary["v2"] = {
            "n_periods_verified": int(len(v2_results)),
            "avg_ic": round(float(np.mean(ics)), 4) if ics else None,
            "avg_spread": round(float(np.mean(spreads)), 2) if spreads else None,
            "avg_win_rate": round(float(np.mean(win_rates)), 1) if win_rates else None,
            "recommendation_historical": rec_summary,
        }

    if v1_results:
        summary["v1"] = {"n_periods_verified": int(len(v1_results))}

    return summary


def _needs_refresh(existing: dict[str, Any], days_elapsed: int) -> bool:
    if existing.get("tracking_version") != TRACKING_VERSION:
        return True
    if days_elapsed >= V1_MIN_CALENDAR_DAYS and "v1" not in existing:
        return True
    if days_elapsed >= V2_MIN_CALENDAR_DAYS and "v2" not in existing:
        return True
    return False


def main() -> None:
    today = datetime.now()
    tracking = _load_tracking()
    existing_by_date = {item["prediction_date"]: item for item in tracking.get("verified", [])}

    pred_files = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(path))
    ]

    new_or_refreshed = 0
    for pred_path in pred_files:
        prediction_date = os.path.basename(pred_path).replace("predictions_", "").replace(".csv", "")
        pred_date = datetime.strptime(prediction_date, "%Y-%m-%d")
        days_elapsed = (today - pred_date).days

        existing = existing_by_date.get(prediction_date)
        if existing is not None and not _needs_refresh(existing, days_elapsed):
            continue

        result = verify_one_prediction_file(pred_path, today)
        if result is None:
            print(f"跳過: {prediction_date} (尚未到期)")
            continue

        if existing is None:
            tracking.setdefault("verified", []).append(result)
            existing_by_date[prediction_date] = result
            print(f"驗證完成: {prediction_date}")
        else:
            existing.update(result)
            print(f"更新驗證: {prediction_date}")
        new_or_refreshed += 1

        if "v2" in result:
            v2 = result["v2"]
            print(
                f"  v2[{v2['spec']}]: IC={v2.get('ic_spearman')}, "
                f"Spread={v2.get('spread')}%, WinRate={v2.get('overall_win_rate')}%"
            )
        if "v1" in result:
            v1 = result["v1"]
            print(f"  v1[{v1['spec']}]: top30={v1.get('top30_by_up_prob_avg_return')}%")

    tracking["verified"] = sorted(
        tracking.get("verified", []),
        key=lambda item: item.get("prediction_date", ""),
    )
    tracking["summary"] = compute_summary(tracking["verified"])
    _save_tracking(tracking)

    print(f"\n已更新 {new_or_refreshed} 筆驗證，結果儲存至 {TRACKING_PATH}")

    summary = tracking.get("summary", {})
    if "v2" in summary:
        v2s = summary["v2"]
        print(f"\n=== v2 歷史表現 ({v2s.get('n_periods_verified', 0)} 期, {V2_SPEC_LABEL}) ===")
        if v2s.get("avg_ic") is not None:
            print(f"  平均 IC: {v2s['avg_ic']}")
        if v2s.get("avg_spread") is not None:
            print(f"  平均 Top-Bot Spread: {v2s['avg_spread']}%")
        if v2s.get("avg_win_rate") is not None:
            print(f"  平均勝率: {v2s['avg_win_rate']}%")


if __name__ == "__main__":
    main()
