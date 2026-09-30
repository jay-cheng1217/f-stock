from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover - optional dependency at runtime
    duckdb = None

from backend.config import DUCKDB_PATH
from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR
from ml.predict import apply_sector_cap

PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
DEFAULT_REPORT_PATH = os.path.join(REPORT_DIR, "daily_replay_monitor_latest.json")
FRICTION_COST = 0.004
MAX_OBSERVED_TRADING_DAYS = 10
OVERHEAT_THRESHOLD = 0.18
MDD_ALERT_THRESHOLD = -0.08

BUY_RECOMMENDATIONS = {"建議買進", "強力買進"}


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


def _to_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        num = float(value)
    except Exception:
        return None
    if not np.isfinite(num):
        return None
    return num


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _to_bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    normalized = series.fillna("").astype(str).str.strip().str.lower()
    truthy = {"1", "true", "t", "yes", "y"}
    return normalized.isin(truthy)


def _risk_tag_contains(df: pd.DataFrame, keyword: str) -> pd.Series:
    if "risk_tags" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["risk_tags"].fillna("").astype(str).str.contains(keyword, regex=False)


def _series_mean(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.mean()
    if pd.isna(value):
        return None
    return float(value)


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype=np.float64)
    return pd.to_numeric(df[column], errors="coerce")


@dataclass
class MarketDataSource:
    daily_k_dir: str
    duckdb_path: str

    def __post_init__(self) -> None:
        self._cache: dict[str, pd.DataFrame] = {}
        self._backend = "csv"
        self._duckdb_conn = None
        if duckdb is not None:
            try:
                self._duckdb_conn = duckdb.connect(self.duckdb_path, read_only=True)
                self._backend = "duckdb"
            except Exception:
                self._duckdb_conn = None
                self._backend = "csv"

    @property
    def backend(self) -> str:
        return self._backend

    def close(self) -> None:
        if self._duckdb_conn is not None:
            self._duckdb_conn.close()
            self._duckdb_conn = None

    def get_history(self, ticker: str) -> pd.DataFrame:
        ticker = str(ticker)
        if ticker in self._cache:
            return self._cache[ticker]

        history = self._load_from_duckdb(ticker)
        if history is None:
            history = self._load_from_csv(ticker)
        if history is None:
            history = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])

        history = history.sort_values("Date").reset_index(drop=True)
        self._cache[ticker] = history
        return history

    def _load_from_duckdb(self, ticker: str) -> pd.DataFrame | None:
        if self._duckdb_conn is None:
            return None
        try:
            df = self._duckdb_conn.execute(
                """
                SELECT
                    CAST(Date AS DATE) AS Date,
                    Open,
                    High,
                    Low,
                    Close
                FROM daily_k
                WHERE Ticker = ?
                ORDER BY Date
                """,
                [ticker],
            ).fetchdf()
        except Exception:
            return None

        if df.empty:
            return None
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        numeric_cols = [col for col in ["Open", "High", "Low", "Close"] if col in df.columns]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Date", "Open", "Close"])

    def _load_from_csv(self, ticker: str) -> pd.DataFrame | None:
        path = os.path.join(self.daily_k_dir, f"{ticker}.csv")
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path, usecols=["Date", "Open", "High", "Low", "Close"])
        except Exception:
            return None
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Date", "Open", "Close"])


def _list_prediction_paths(days: int) -> list[str]:
    pred_files = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(path))
    ]
    if days <= 0:
        return pred_files
    return pred_files[-days:]


def _load_prediction_file(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")


def _resolve_selected_positions(pred_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if pred_df.empty:
        return pred_df.head(0).copy()
    selected = apply_sector_cap(pred_df, top_n=top_n).copy()
    if selected.empty:
        return selected
    selected = selected.reset_index(drop=True)
    selected["selection_rank"] = np.arange(1, len(selected) + 1, dtype=np.int16)
    return selected


def _position_observation(
    history: pd.DataFrame,
    prediction_date: str,
) -> dict[str, Any] | None:
    pred_ts = pd.Timestamp(prediction_date)
    future = history[history["Date"] > pred_ts].copy()
    if future.empty:
        return None

    observed = future.head(MAX_OBSERVED_TRADING_DAYS).copy()
    if observed.empty:
        return None

    entry = observed.iloc[0]
    entry_open = _to_float(entry.get("Open"))
    if entry_open is None or entry_open <= 0:
        return None

    settle = observed.iloc[-1]
    settle_close = _to_float(settle.get("Close"))
    if settle_close is None:
        return None

    low_col = "Low" if "Low" in observed.columns else "Close"
    low_series = pd.to_numeric(observed[low_col], errors="coerce").dropna()
    worst_low = _to_float(low_series.min()) if not low_series.empty else None

    partial_return = settle_close / entry_open - 1.0 - FRICTION_COST
    max_drawdown = (worst_low / entry_open - 1.0) if worst_low is not None else None

    return {
        "entry_date": entry["Date"].date().isoformat(),
        "settle_date": settle["Date"].date().isoformat(),
        "observed_trading_days": int(len(observed)),
        "entry_open": entry_open,
        "settle_close": settle_close,
        "partial_return": partial_return,
        "max_drawdown": max_drawdown,
    }


def _build_position_frame(selected: pd.DataFrame, data_source: MarketDataSource) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        prediction_date = str(row["date"])
        history = data_source.get_history(str(row["ticker"]))
        observed = _position_observation(history, prediction_date)

        item = row.to_dict()
        item["prediction_date"] = prediction_date
        item["pred_return_20d"] = _to_float(row.get("pred_return_20d"))
        item["price_vs_ma20"] = _to_float(row.get("price_vs_ma20"))
        item["is_disposition_selected"] = bool(
            (
                "is_disposition" in row.index and bool(_to_bool_series(pd.Series([row.get("is_disposition")])).iloc[0])
            )
            or (
                "disposition_override" in row.index
                and bool(_to_bool_series(pd.Series([row.get("disposition_override")])).iloc[0])
            )
            or ("risk_tags" in row.index and "處置股" in str(row.get("risk_tags") or ""))
        )
        item["is_overheat_selected"] = bool(
            item["price_vs_ma20"] is not None and item["price_vs_ma20"] > OVERHEAT_THRESHOLD
        )

        if observed is None:
            item.update(
                {
                    "entry_date": None,
                    "settle_date": None,
                    "observed_trading_days": 0,
                    "entry_open": None,
                    "settle_close": None,
                    "partial_return": None,
                    "max_drawdown": None,
                }
            )
        else:
            item.update(observed)
        rows.append(item)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "partial_return" in df.columns:
        df["partial_return"] = pd.to_numeric(df["partial_return"], errors="coerce")
    if "pred_return_20d" in df.columns:
        df["pred_return_20d"] = pd.to_numeric(df["pred_return_20d"], errors="coerce")
    if "max_drawdown" in df.columns:
        df["max_drawdown"] = pd.to_numeric(df["max_drawdown"], errors="coerce")
    return df


def _summarize_subset(positions: pd.DataFrame) -> dict[str, Any]:
    if positions.empty or "partial_return" not in positions.columns:
        return {
            "count": int(len(positions)),
            "observed_count": 0,
            "avg_pred_return_20d": _round_or_none(
                _series_mean(_numeric_series(positions, "pred_return_20d"))
            ),
            "avg_partial_return": None,
            "win_rate": None,
            "avg_max_drawdown": None,
            "worst_max_drawdown": None,
            "mdd_alert_count": 0,
        }

    if "max_drawdown" not in positions.columns:
        positions = positions.assign(max_drawdown=np.nan)

    observed = positions.dropna(subset=["partial_return"]).copy()
    pred_series = _numeric_series(positions, "pred_return_20d")
    summary = {
        "count": int(len(positions)),
        "observed_count": int(len(observed)),
        "avg_pred_return_20d": _round_or_none(_series_mean(pred_series)),
        "avg_partial_return": _round_or_none(_series_mean(observed["partial_return"])) if not observed.empty else None,
        "win_rate": _round_or_none(float((observed["partial_return"] > 0).mean()), 4) if not observed.empty else None,
        "avg_max_drawdown": _round_or_none(_series_mean(observed["max_drawdown"])) if not observed.empty else None,
        "worst_max_drawdown": _round_or_none(_to_float(observed["max_drawdown"].min())) if not observed.empty else None,
        "mdd_alert_count": int((observed["max_drawdown"] <= MDD_ALERT_THRESHOLD).sum()) if not observed.empty else 0,
    }
    return summary


def _guardrail_payload(pred_df: pd.DataFrame) -> dict[str, Any]:
    payload = {
        "loose_mode": None,
        "intercept_rate_top50": None,
        "dual_track_fail_rate_top50": None,
        "trigger_reason": None,
    }
    if pred_df.empty:
        return payload

    first_row = pred_df.iloc[0]
    if "guardrail_loose_mode" in pred_df.columns:
        loose_mode = _to_float(first_row.get("guardrail_loose_mode"))
        payload["loose_mode"] = bool(int(loose_mode)) if loose_mode is not None else None
    if "guardrail_intercept_rate_top50" in pred_df.columns:
        payload["intercept_rate_top50"] = _round_or_none(_to_float(first_row.get("guardrail_intercept_rate_top50")))
    if "guardrail_dual_track_fail_rate_top50" in pred_df.columns:
        payload["dual_track_fail_rate_top50"] = _round_or_none(_to_float(first_row.get("guardrail_dual_track_fail_rate_top50")))
    if "guardrail_trigger_reason" in pred_df.columns:
        reason = str(first_row.get("guardrail_trigger_reason") or "").strip()
        payload["trigger_reason"] = reason or None
    return payload


def _day_alerts(day_summary: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    if (day_summary.get("selected_count") or 0) < (day_summary.get("top_n") or 0):
        alerts.append("breathing_below_target")

    guardrail = day_summary.get("guardrail", {})
    if guardrail.get("loose_mode"):
        alerts.append("guardrail_loose_mode")
    intercept_rate = guardrail.get("intercept_rate_top50")
    dual_track_rate = guardrail.get("dual_track_fail_rate_top50")
    if intercept_rate is not None and intercept_rate > 0.60:
        alerts.append("guardrail_intercept_high")
    if dual_track_rate is not None and dual_track_rate > 0.70:
        alerts.append("guardrail_dual_track_high")

    high_risk = day_summary.get("high_risk_groups", {})
    disp = high_risk.get("disposition_allowed", {})
    overheat = high_risk.get("overheat_gt_18pct", {})
    if disp.get("mdd_alert_count", 0) > 0:
        alerts.append("disposition_mdd_alert")
    if overheat.get("mdd_alert_count", 0) > 0:
        alerts.append("overheat_mdd_alert")
    return alerts


def _summarize_day(
    prediction_path: str,
    pred_df: pd.DataFrame,
    selected: pd.DataFrame,
    positions: pd.DataFrame,
    top_n: int,
) -> dict[str, Any]:
    prediction_date = str(pred_df["date"].iloc[0])
    observed = positions.dropna(subset=["partial_return"]).copy() if not positions.empty else positions.copy()
    avg_pred = _series_mean(pd.to_numeric(positions.get("pred_return_20d"), errors="coerce")) if not positions.empty else None
    avg_partial = _series_mean(observed["partial_return"]) if not observed.empty else None
    avg_spread = None
    if avg_pred is not None and avg_partial is not None:
        avg_spread = avg_partial - avg_pred

    settle_dates = sorted({str(value) for value in observed.get("settle_date", pd.Series(dtype=str)).dropna().tolist()})
    day_summary = {
        "prediction_date": prediction_date,
        "prediction_file": prediction_path,
        "top_n": int(top_n),
        "selected_count": int(len(selected)),
        "breathing_ratio": _round_or_none(len(selected) / top_n if top_n else None, 4),
        "observed_count": int(len(observed)),
        "avg_pred_return_20d": _round_or_none(avg_pred),
        "avg_partial_return": _round_or_none(avg_partial),
        "avg_prediction_spread": _round_or_none(avg_spread),
        "win_rate": _round_or_none(float((observed["partial_return"] > 0).mean()), 4) if not observed.empty else None,
        "avg_max_drawdown": _round_or_none(_series_mean(observed["max_drawdown"])) if not observed.empty else None,
        "worst_max_drawdown": _round_or_none(_to_float(observed["max_drawdown"].min())) if not observed.empty else None,
        "avg_observed_trading_days": _round_or_none(_series_mean(pd.to_numeric(observed.get("observed_trading_days"), errors="coerce")), 2) if not observed.empty else None,
        "settle_date_min": settle_dates[0] if settle_dates else None,
        "settle_date_max": settle_dates[-1] if settle_dates else None,
        "guardrail": _guardrail_payload(pred_df),
        "high_risk_groups": {
            "disposition_allowed": _summarize_subset(positions[positions["is_disposition_selected"]]) if not positions.empty else _summarize_subset(pd.DataFrame()),
            "overheat_gt_18pct": _summarize_subset(positions[positions["is_overheat_selected"]]) if not positions.empty else _summarize_subset(pd.DataFrame()),
        },
    }
    day_summary["alerts"] = _day_alerts(day_summary)
    return day_summary


def _compound_return(values: list[float]) -> float | None:
    if not values:
        return None
    result = 1.0
    for value in values:
        result *= 1.0 + value
    return result - 1.0


def generate_report(
    days: int = 10,
    top_n: int = 30,
    output_path: str = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    os.makedirs(REPORT_DIR, exist_ok=True)
    prediction_paths = _list_prediction_paths(days)

    data_source = MarketDataSource(daily_k_dir=DAILY_K_DIR, duckdb_path=DUCKDB_PATH)
    try:
        day_rows: list[dict[str, Any]] = []
        observed_frames: list[pd.DataFrame] = []

        for path in prediction_paths:
            pred_df = _load_prediction_file(path)
            if pred_df.empty or "date" not in pred_df.columns or "ticker" not in pred_df.columns:
                continue
            if "pred_return_20d" not in pred_df.columns:
                continue

            selected = _resolve_selected_positions(pred_df, top_n=top_n)
            positions = _build_position_frame(selected, data_source)
            day_summary = _summarize_day(path, pred_df, selected, positions, top_n=top_n)
            day_rows.append(day_summary)

            if not positions.empty:
                observed = positions.dropna(subset=["partial_return"]).copy()
                if not observed.empty:
                    observed_frames.append(observed)

        if observed_frames:
            observed_all = pd.concat(observed_frames, ignore_index=True)
        else:
            observed_all = pd.DataFrame()

        avg_daily_returns = [
            row["avg_partial_return"]
            for row in day_rows
            if row.get("avg_partial_return") is not None
        ]

        report = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "report_path": output_path,
            "lookback_prediction_days": int(days),
            "top_n": int(top_n),
            "friction_cost": FRICTION_COST,
            "max_observed_trading_days": MAX_OBSERVED_TRADING_DAYS,
            "market_data_backend": data_source.backend,
            "summary": {
                "prediction_days": int(len(day_rows)),
                "observed_prediction_days": int(sum(1 for row in day_rows if row.get("observed_count", 0) > 0)),
                "avg_selected_count": _round_or_none(_series_mean(pd.Series([row["selected_count"] for row in day_rows], dtype=np.float64))),
                "avg_breathing_ratio": _round_or_none(_series_mean(pd.Series([row["breathing_ratio"] for row in day_rows if row.get("breathing_ratio") is not None], dtype=np.float64)), 4),
                "avg_pred_return_20d": _round_or_none(_series_mean(pd.Series([row["avg_pred_return_20d"] for row in day_rows if row.get("avg_pred_return_20d") is not None], dtype=np.float64))),
                "avg_partial_return": _round_or_none(_series_mean(pd.Series([row["avg_partial_return"] for row in day_rows if row.get("avg_partial_return") is not None], dtype=np.float64))),
                "avg_prediction_spread": _round_or_none(_series_mean(pd.Series([row["avg_prediction_spread"] for row in day_rows if row.get("avg_prediction_spread") is not None], dtype=np.float64))),
                "synthetic_compound_partial_return": _round_or_none(_compound_return(avg_daily_returns)),
                "observed_positions": int(len(observed_all)),
                "all_slot_win_rate": _round_or_none(float((observed_all["partial_return"] > 0).mean()), 4) if not observed_all.empty else None,
                "all_slot_avg_max_drawdown": _round_or_none(_series_mean(observed_all["max_drawdown"])) if not observed_all.empty else None,
                "alert_days": int(sum(1 for row in day_rows if row.get("alerts"))),
                "latest_prediction_date": day_rows[-1]["prediction_date"] if day_rows else None,
            },
            "days": day_rows,
        }

        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        return report
    finally:
        data_source.close()


def _print_report(report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    _safe_print(
        "Replay monitor: "
        f"{summary.get('prediction_days', 0)} days, "
        f"avg selected={summary.get('avg_selected_count')}, "
        f"avg partial={summary.get('avg_partial_return')}, "
        f"avg spread={summary.get('avg_prediction_spread')}, "
        f"backend={report.get('market_data_backend')}"
    )
    for row in report.get("days", []):
        _safe_print(
            f"  {row['prediction_date']}: selected={row['selected_count']}/{row['top_n']}, "
            f"partial={row.get('avg_partial_return')}, spread={row.get('avg_prediction_spread')}, "
            f"alerts={','.join(row.get('alerts', [])) or '-'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the daily replay monitor report.")
    parser.add_argument("--days", type=int, default=10, help="Number of recent production prediction files to replay.")
    parser.add_argument("--top-n", type=int, default=30, help="Top N slots to replay.")
    parser.add_argument("--output", default=DEFAULT_REPORT_PATH, help="Output JSON path.")
    args = parser.parse_args()

    report = generate_report(days=args.days, top_n=args.top_n, output_path=args.output)
    _print_report(report)
    _safe_print(f"Saved replay monitor report to {args.output}")


if __name__ == "__main__":
    main()
