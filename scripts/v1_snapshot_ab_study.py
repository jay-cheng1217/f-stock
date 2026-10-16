from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backend.config import DUCKDB_PATH
from ml.config import (
    FINANCIAL_DIR,
    INDEX_DIR,
    MIN_AVG_VOLUME,
    MIN_HISTORY_DAYS,
    MIN_PRICE,
    MODEL_DIR,
    REPORT_DIR,
    REVENUE_DIR,
    TARGET_CLASSES,
    VALUATION_DIR,
)
from ml.dataset import (
    _apply_percentile_ranking,
    _get_issued_shares,
    _winsorize_features,
    apply_snapshot_zscore,
)
from ml.features.balance_sheet import compute_balance_sheet_features
from ml.features.entry import compute_entry_features
from ml.features.eps import compute_eps_features
from ml.features.fundamental import compute_fundamental_features
from ml.features.industry import compute_industry_features
from ml.features.institutional import compute_institutional_features
from ml.features.market import compute_market_features
from ml.features.news import compute_news_features
from ml.features.registry import FEATURE_REGISTRY, get_available_features
from ml.features.revenue import compute_revenue_features
from ml.features.sector import compute_sector_features
from ml.features.sentiment import compute_sentiment_features
from ml.features.tdcc import compute_tdcc_features
from ml.features.technical import compute_technical_features
from ml.features.valuation import compute_valuation_features

warnings.filterwarnings("ignore")

DEFAULT_DATES = ["2026-04-10", "2026-04-13"]
DEFAULT_OLD_META = os.path.join(MODEL_DIR, "lgbm_20260404_131437_meta.json")
DEFAULT_NEW_META = os.path.join(MODEL_DIR, "lgbm_20260409_125845_meta.json")
DEFAULT_OUTPUT_JSON = os.path.join(REPORT_DIR, "v1_snapshot_ab_test_latest.json")
DEFAULT_OUTPUT_MD = os.path.join(REPORT_DIR, "v1_snapshot_ab_test_latest.md")
DEFAULT_OUTPUT_CSV = os.path.join(REPORT_DIR, "v1_snapshot_ab_test_latest.csv")
DEFAULT_GATE_THRESHOLDS = {
    "max_old_up_to_new_down_rate": 0.30,
    "max_top_decile_edge_loss": 0.05,
    "min_new_up_count": 50,
    "max_v2_top30_new_down_rate": 0.40,
    "max_new_down_rate": 0.95,
}


@dataclass(frozen=True)
class ModelBundle:
    label: str
    meta_path: str
    meta: dict[str, Any]
    model: lgb.Booster
    use_zscore: bool

    @property
    def model_file_name(self) -> str:
        return os.path.basename(str(self.meta.get("model_file", "")))

    @property
    def trained_at(self) -> str | None:
        trained = self.meta.get("trained_at")
        return str(trained) if trained is not None else None

    @property
    def feature_columns(self) -> list[str]:
        return list(self.meta.get("feature_columns", []))


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        if pd.isna(value):
            return None
        num = float(value)
    except Exception:
        return None
    if not np.isfinite(num):
        return None
    return round(num, digits)


def _series_stats(series: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "mean": None,
            "median": None,
            "q05": None,
            "q25": None,
            "q75": None,
            "q95": None,
            "min": None,
            "max": None,
        }
    return {
        "mean": _round(values.mean()),
        "median": _round(values.median()),
        "q05": _round(values.quantile(0.05)),
        "q25": _round(values.quantile(0.25)),
        "q75": _round(values.quantile(0.75)),
        "q95": _round(values.quantile(0.95)),
        "min": _round(values.min()),
        "max": _round(values.max()),
    }


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}


def _rates(series: pd.Series) -> dict[str, float]:
    total = len(series)
    if total == 0:
        return {}
    return {
        str(k): round(float(v) / total, 6)
        for k, v in series.value_counts(dropna=False).items()
    }


def _format_pct(value: Any, digits: int = 1) -> str:
    rounded = _round(value, digits + 2)
    if rounded is None:
        return "-"
    return f"{rounded * 100:+.{digits}f}%"


def _format_num(value: Any, digits: int = 3) -> str:
    rounded = _round(value, digits)
    if rounded is None:
        return "-"
    return f"{rounded:.{digits}f}"


def _load_model_bundle(meta_path: str, label: str, use_zscore: bool) -> ModelBundle:
    resolved = str(Path(meta_path).resolve())
    with open(resolved, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    model = lgb.Booster(model_file=meta["model_file"])
    return ModelBundle(
        label=label,
        meta_path=resolved,
        meta=meta,
        model=model,
        use_zscore=use_zscore,
    )


def _feature_requires_dir(group: str) -> str:
    requires = FEATURE_REGISTRY.get(group, {}).get("requires", [])
    return str(requires[0]) if requires else BASE_DIR


def _load_duckdb_daily_k(as_of_date: str, max_stocks: int = 0) -> pd.DataFrame:
    sql = """
        SELECT *
        FROM daily_k
        WHERE Date <= ?
    """
    params: list[Any] = [as_of_date]
    if max_stocks > 0:
        sql += """
            AND Ticker IN (
                SELECT Ticker
                FROM (
                    SELECT Ticker, max(Date) AS last_date
                    FROM daily_k
                    WHERE Date <= ?
                    GROUP BY Ticker
                )
                WHERE last_date = ?
                ORDER BY Ticker
                LIMIT ?
            )
        """
        params.extend([as_of_date, as_of_date, int(max_stocks)])
    sql += " ORDER BY Ticker, Date"

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        df = con.execute(sql, params).fetchdf()
    finally:
        con.close()

    if df.empty:
        raise ValueError(f"No daily_k rows found in DuckDB up to {as_of_date}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").astype("datetime64[ns]")
    df["Ticker"] = df["Ticker"].astype(str)
    return df.dropna(subset=["Date", "Ticker"]).sort_values(["Ticker", "Date"])


def _eligible_history(history: pd.DataFrame, as_of_ts: pd.Timestamp, same_day_only: bool) -> bool:
    if len(history) < MIN_HISTORY_DAYS:
        return False

    last_row = history.iloc[-1]
    if same_day_only and pd.Timestamp(last_row["Date"]).normalize() != as_of_ts.normalize():
        return False

    avg_vol = pd.to_numeric(history["Volume"].tail(60), errors="coerce").mean()
    last_price = pd.to_numeric(pd.Series([last_row.get("Close")]), errors="coerce").iloc[0]
    if pd.isna(avg_vol) or pd.isna(last_price):
        return False
    return bool(avg_vol >= MIN_AVG_VOLUME and last_price >= MIN_PRICE)


def _compute_single_stock_features(
    history: pd.DataFrame,
    ticker: str,
    available: dict[str, dict],
) -> pd.DataFrame:
    df = history.copy().reset_index(drop=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").astype("datetime64[ns]")

    if "Ticker" in df.columns:
        df = df.drop(columns=["Ticker"])

    if "technical" in available:
        df = compute_technical_features(df, ticker=ticker)
    if "institutional" in available:
        df = compute_institutional_features(df)

    if "revenue" in available:
        rev_path = os.path.join(REVENUE_DIR, f"revenue_{ticker}.csv")
        if os.path.exists(rev_path):
            rev_df = pd.read_csv(rev_path, dtype={"Date": str})
            df = compute_revenue_features(df, rev_df)

    if "fundamental" in available:
        df = compute_fundamental_features(df, ticker, FINANCIAL_DIR)
    if "market" in available:
        df = compute_market_features(df, INDEX_DIR)
    if "valuation" in available:
        df = compute_valuation_features(df, ticker, VALUATION_DIR)
    if "eps" in available:
        df = compute_eps_features(df, ticker, FINANCIAL_DIR)
    if "sentiment" in available:
        df = compute_sentiment_features(df)
    if "tdcc" in available:
        tdcc_path = os.path.join(_feature_requires_dir("tdcc"), "tdcc_summary.csv")
        df = compute_tdcc_features(df, ticker, tdcc_path)
    if "news" in available:
        df = compute_news_features(df, ticker, _feature_requires_dir("news"))
    if "balance_sheet" in available:
        df = compute_balance_sheet_features(
            df,
            ticker,
            _feature_requires_dir("balance_sheet"),
            FINANCIAL_DIR,
        )
    if "entry" in available:
        df = compute_entry_features(df)

    issued = _get_issued_shares()
    shares = issued.get(ticker)
    if shares and shares > 0 and "Volume" in df.columns:
        lots_outstanding = shares / 1000
        df["turnover_rate"] = np.where(
            lots_outstanding > 0,
            ((df["Volume"] / 1000) / lots_outstanding * 100).astype(np.float32),
            np.nan,
        )
    else:
        df["turnover_rate"] = np.nan

    df["ticker"] = ticker
    return df


def build_duckdb_snapshot(
    as_of_date: str,
    *,
    max_stocks: int = 0,
    same_day_only: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    as_of_ts = pd.Timestamp(as_of_date)
    raw = _load_duckdb_daily_k(as_of_date, max_stocks=max_stocks)
    available = get_available_features()
    rows: list[pd.DataFrame] = []

    grouped = raw.groupby("Ticker", sort=True)
    total = len(grouped)
    for idx, (ticker, history) in enumerate(grouped, start=1):
        if verbose and (idx == 1 or idx % 250 == 0 or idx == total):
            _safe_print(f"  feature snapshot {as_of_date}: {idx}/{total}")
        if not _eligible_history(history, as_of_ts, same_day_only=same_day_only):
            continue

        featured = _compute_single_stock_features(history, ticker=str(ticker), available=available)
        latest = featured[featured["Date"].dt.normalize() == as_of_ts.normalize()].tail(1)
        if latest.empty and not same_day_only:
            latest = featured.tail(1)
        if not latest.empty:
            rows.append(latest.copy())

    if not rows:
        raise ValueError(f"No eligible snapshot rows built for {as_of_date}")

    snapshot = pd.concat(rows, ignore_index=True)
    snapshot = snapshot.sort_values(["Date", "ticker"]).reset_index(drop=True)

    snapshot = _apply_percentile_ranking(snapshot)
    if "sector" in available:
        snapshot = compute_sector_features(snapshot)
    snapshot = compute_industry_features(snapshot)

    if "atr_pct" in snapshot.columns:
        snapshot["atr_pct_rank"] = snapshot["atr_pct"].rank(pct=True).astype(np.float32)
    else:
        snapshot["atr_pct_rank"] = np.nan

    snapshot = _winsorize_features(snapshot)
    snapshot.attrs["zscore_applied"] = False
    return snapshot


def _snapshot_fingerprint(snapshot: pd.DataFrame) -> str:
    stable = snapshot.copy()
    if "Date" in stable.columns:
        stable["Date"] = pd.to_datetime(stable["Date"]).dt.strftime("%Y-%m-%d")
    stable = stable.reindex(sorted(stable.columns), axis=1)
    hashed = pd.util.hash_pandas_object(stable, index=False).values.tobytes()
    return hashlib.sha256(hashed).hexdigest()


def _feature_frame(snapshot: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    missing = [col for col in feature_cols if col not in snapshot.columns]
    frame = pd.DataFrame(
        {
            col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
            for col in feature_cols
        }
    )
    return frame, missing


def _predict_bundle(snapshot: pd.DataFrame, bundle: ModelBundle) -> tuple[pd.DataFrame, list[str]]:
    input_snapshot = apply_snapshot_zscore(snapshot) if bundle.use_zscore else snapshot
    feature_cols = bundle.feature_columns
    x, missing = _feature_frame(input_snapshot, feature_cols)
    proba = bundle.model.predict(x.values)
    proba = np.asarray(proba)
    if proba.ndim != 2 or proba.shape[1] < 3:
        raise ValueError(f"{bundle.label} prediction shape is not multiclass: {proba.shape}")

    pred = snapshot[["ticker", "Date", "Close"]].copy()
    pred["date"] = pd.to_datetime(pred["Date"]).dt.strftime("%Y-%m-%d")
    pred["close"] = pred["Close"]
    pred[f"{bundle.label}_up_prob"] = proba[:, 2]
    pred[f"{bundle.label}_flat_prob"] = proba[:, 1]
    pred[f"{bundle.label}_down_prob"] = proba[:, 0]
    pred[f"{bundle.label}_prob_edge"] = pred[f"{bundle.label}_up_prob"] - pred[f"{bundle.label}_down_prob"]
    pred[f"{bundle.label}_signal"] = [TARGET_CLASSES[int(i)] for i in np.argmax(proba, axis=1)]
    pred[f"{bundle.label}_model_file"] = bundle.model_file_name
    pred[f"{bundle.label}_trained_at"] = bundle.trained_at
    pred[f"{bundle.label}_zscore_applied"] = bool(bundle.use_zscore)
    return pred.drop(columns=["Date", "Close"]), missing


def _load_production_context(as_of_date: str) -> pd.DataFrame:
    path = os.path.join(MODEL_DIR, f"predictions_{as_of_date}.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "date"])
    use_cols = [
        "ticker",
        "date",
        "pred_return_20d",
        "prob_edge",
        "signal",
        "recommendation",
        "leaderboard_score",
        "risk_adjusted_return",
    ]
    df = pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")
    keep = [col for col in use_cols if col in df.columns]
    df = df[keep].copy()
    rename = {
        "pred_return_20d": "production_v2_pred_return_20d",
        "prob_edge": "production_prob_edge",
        "signal": "production_signal",
        "recommendation": "production_recommendation",
        "leaderboard_score": "production_leaderboard_score",
        "risk_adjusted_return": "production_risk_adjusted_return",
    }
    return df.rename(columns=rename)


def compare_snapshot(
    snapshot: pd.DataFrame,
    old_bundle: ModelBundle,
    new_bundle: ModelBundle,
    as_of_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    old_pred, old_missing = _predict_bundle(snapshot, old_bundle)
    new_pred, new_missing = _predict_bundle(snapshot, new_bundle)
    joined = old_pred.merge(new_pred, on=["ticker", "date", "close"], how="inner")

    context = _load_production_context(as_of_date)
    if not context.empty:
        joined = joined.merge(context, on=["ticker", "date"], how="left")

    joined["prob_edge_delta"] = joined["new_prob_edge"] - joined["old_prob_edge"]
    joined["prob_edge_loss"] = joined["old_prob_edge"] - joined["new_prob_edge"]
    joined["up_prob_delta"] = joined["new_up_prob"] - joined["old_up_prob"]
    joined["down_prob_delta"] = joined["new_down_prob"] - joined["old_down_prob"]
    joined["signal_changed"] = joined["old_signal"] != joined["new_signal"]
    joined["old_to_new_down"] = (joined["old_signal"] != "DOWN") & (joined["new_signal"] == "DOWN")
    joined["old_up_to_new_down"] = (joined["old_signal"] == "UP") & (joined["new_signal"] == "DOWN")
    joined["same_snapshot_fingerprint"] = _snapshot_fingerprint(snapshot)

    joined = joined.sort_values(
        ["prob_edge_loss", "old_prob_edge", "ticker"],
        ascending=[False, False, True],
    ).reset_index(drop=True)

    summary = {
        "date": as_of_date,
        "snapshot_rows": int(len(snapshot)),
        "joined_rows": int(len(joined)),
        "snapshot_fingerprint": str(joined["same_snapshot_fingerprint"].iloc[0]) if not joined.empty else None,
        "old_missing_features": old_missing,
        "new_missing_features": new_missing,
        "old_prob_edge": _series_stats(joined["old_prob_edge"]),
        "new_prob_edge": _series_stats(joined["new_prob_edge"]),
        "prob_edge_delta": _series_stats(joined["prob_edge_delta"]),
        "prob_edge_loss": _series_stats(joined["prob_edge_loss"]),
        "old_signal_counts": _counts(joined["old_signal"]),
        "new_signal_counts": _counts(joined["new_signal"]),
        "old_signal_rates": _rates(joined["old_signal"]),
        "new_signal_rates": _rates(joined["new_signal"]),
        "signal_changed_count": int(joined["signal_changed"].sum()),
        "signal_changed_rate": _round(joined["signal_changed"].mean()),
        "old_to_new_down_count": int(joined["old_to_new_down"].sum()),
        "old_up_to_new_down_count": int(joined["old_up_to_new_down"].sum()),
        "top_prob_edge_loss": _records(joined.head(20)),
    }

    if "production_v2_pred_return_20d" in joined.columns:
        top_v2 = joined.sort_values(
            ["production_v2_pred_return_20d", "ticker"],
            ascending=[False, True],
        ).head(30)
        summary["production_v2_top30"] = {
            "rows": int(len(top_v2)),
            "old_signal_counts": _counts(top_v2["old_signal"]),
            "new_signal_counts": _counts(top_v2["new_signal"]),
            "prob_edge_loss": _series_stats(top_v2["prob_edge_loss"]),
            "old_to_new_down_count": int(top_v2["old_to_new_down"].sum()),
            "top_prob_edge_loss": _records(
                top_v2.sort_values("prob_edge_loss", ascending=False).head(15)
            ),
        }

    return joined, summary


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    columns = [
        "date",
        "ticker",
        "close",
        "old_signal",
        "new_signal",
        "old_prob_edge",
        "new_prob_edge",
        "prob_edge_loss",
        "prob_edge_delta",
        "old_up_prob",
        "new_up_prob",
        "old_down_prob",
        "new_down_prob",
        "production_v2_pred_return_20d",
        "production_recommendation",
    ]
    existing = [col for col in columns if col in df.columns]
    rows: list[dict[str, Any]] = []
    for raw in df[existing].to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, (np.integer, np.floating)):
                row[key] = _round(value)
            elif isinstance(value, float):
                row[key] = _round(value)
            elif pd.isna(value):
                row[key] = None
            else:
                row[key] = value
        rows.append(row)
    return rows


def _safe_rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(float(count) / float(total), 6)


def _top_old_edge_decile(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty or "old_prob_edge" not in detail.columns:
        return pd.DataFrame()
    n = max(1, int(np.ceil(len(detail) * 0.10)))
    return detail.nlargest(n, "old_prob_edge")


def _v2_top30_rows(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty or "production_v2_pred_return_20d" not in detail.columns:
        return pd.DataFrame()
    rows = []
    for _, day in detail.dropna(subset=["production_v2_pred_return_20d"]).groupby("date"):
        rows.append(
            day.sort_values(
                ["production_v2_pred_return_20d", "ticker"],
                ascending=[False, True],
            ).head(30)
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _gate_check(
    name: str,
    value: float | int | None,
    threshold: float | int | str,
    passed: bool,
    *,
    direction: str,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "threshold": threshold,
        "direction": direction,
        "required": required,
        "passed": bool(passed),
    }


def evaluate_promotion_gate(
    detail: pd.DataFrame,
    thresholds: dict[str, float | int],
) -> dict[str, Any]:
    if detail.empty:
        return {
            "passed": False,
            "thresholds": thresholds,
            "metrics": {},
            "checks": [
                _gate_check("detail_rows", 0, "> 0", False, direction="gt"),
            ],
        }

    old_up_count = int((detail["old_signal"] == "UP").sum())
    old_up_to_new_down_count = int(detail["old_up_to_new_down"].sum())
    old_up_to_new_down_rate = _safe_rate(old_up_to_new_down_count, old_up_count)
    new_up_count = int((detail["new_signal"] == "UP").sum())
    new_down_count = int((detail["new_signal"] == "DOWN").sum())
    new_down_rate = _safe_rate(new_down_count, len(detail))

    top_decile = _top_old_edge_decile(detail)
    top_decile_edge_loss = (
        _round(top_decile["prob_edge_loss"].mean())
        if not top_decile.empty and "prob_edge_loss" in top_decile.columns
        else None
    )
    top_decile_flip_to_down_rate = (
        _safe_rate(int((top_decile["new_signal"] == "DOWN").sum()), len(top_decile))
        if not top_decile.empty
        else None
    )

    v2_top30 = _v2_top30_rows(detail)
    v2_top30_new_down_rate = (
        _safe_rate(int((v2_top30["new_signal"] == "DOWN").sum()), len(v2_top30))
        if not v2_top30.empty
        else None
    )
    v2_top30_rows = int(len(v2_top30))

    metrics = {
        "rows": int(len(detail)),
        "old_up_count": old_up_count,
        "old_up_to_new_down_count": old_up_to_new_down_count,
        "old_up_to_new_down_rate": old_up_to_new_down_rate,
        "new_up_count": new_up_count,
        "new_down_count": new_down_count,
        "new_down_rate": new_down_rate,
        "top_old_edge_decile_rows": int(len(top_decile)),
        "top_old_edge_decile_edge_loss_mean": top_decile_edge_loss,
        "top_old_edge_decile_flip_to_down_rate": top_decile_flip_to_down_rate,
        "v2_top30_rows": v2_top30_rows,
        "v2_top30_new_down_rate": v2_top30_new_down_rate,
    }

    old_up_rate_required = old_up_count > 0
    checks = [
        _gate_check(
            "old_up_to_new_down_rate",
            old_up_to_new_down_rate,
            thresholds["max_old_up_to_new_down_rate"],
            (
                True
                if not old_up_rate_required
                else old_up_to_new_down_rate is not None
                and old_up_to_new_down_rate <= float(thresholds["max_old_up_to_new_down_rate"])
            ),
            direction="lte",
            required=old_up_rate_required,
        ),
        _gate_check(
            "top_old_edge_decile_edge_loss_mean",
            top_decile_edge_loss,
            thresholds["max_top_decile_edge_loss"],
            top_decile_edge_loss is not None
            and top_decile_edge_loss <= float(thresholds["max_top_decile_edge_loss"]),
            direction="lte",
        ),
        _gate_check(
            "new_up_count",
            new_up_count,
            thresholds["min_new_up_count"],
            new_up_count >= int(thresholds["min_new_up_count"]),
            direction="gte",
        ),
        _gate_check(
            "new_down_rate",
            new_down_rate,
            thresholds["max_new_down_rate"],
            new_down_rate is not None
            and new_down_rate <= float(thresholds["max_new_down_rate"]),
            direction="lte",
        ),
    ]

    if v2_top30_rows > 0:
        checks.append(
            _gate_check(
                "v2_top30_new_down_rate",
                v2_top30_new_down_rate,
                thresholds["max_v2_top30_new_down_rate"],
                v2_top30_new_down_rate is not None
                and v2_top30_new_down_rate <= float(thresholds["max_v2_top30_new_down_rate"]),
                direction="lte",
            )
        )
    else:
        checks.append(
            _gate_check(
                "v2_top30_new_down_rate",
                None,
                thresholds["max_v2_top30_new_down_rate"],
                True,
                direction="lte",
                required=False,
            )
        )

    return {
        "passed": all(check["passed"] for check in checks if check.get("required", True)),
        "thresholds": thresholds,
        "metrics": metrics,
        "checks": checks,
    }


def generate_report(
    dates: list[str],
    *,
    old_meta: str = DEFAULT_OLD_META,
    new_meta: str = DEFAULT_NEW_META,
    output_json: str = DEFAULT_OUTPUT_JSON,
    output_md: str = DEFAULT_OUTPUT_MD,
    output_csv: str = DEFAULT_OUTPUT_CSV,
    max_stocks: int = 0,
    same_day_only: bool = True,
    verbose: bool = True,
    gate_thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    os.makedirs(REPORT_DIR, exist_ok=True)
    old_bundle = _load_model_bundle(old_meta, label="old", use_zscore=False)
    new_bundle = _load_model_bundle(new_meta, label="new", use_zscore=True)

    detail_frames: list[pd.DataFrame] = []
    day_summaries: list[dict[str, Any]] = []

    for as_of_date in dates:
        if verbose:
            _safe_print(f"\n=== Building same-snapshot A/B for {as_of_date} ===")
        snapshot = build_duckdb_snapshot(
            as_of_date,
            max_stocks=max_stocks,
            same_day_only=same_day_only,
            verbose=verbose,
        )
        joined, summary = compare_snapshot(snapshot, old_bundle, new_bundle, as_of_date)
        detail_frames.append(joined)
        day_summaries.append(summary)

    detail = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
    detail.to_csv(output_csv, index=False, encoding="utf-8-sig")

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": {
            "dates": dates,
            "duckdb_path": DUCKDB_PATH,
            "same_day_only": same_day_only,
            "max_stocks": max_stocks,
            "detail_csv": output_csv,
            "method": (
                "Build one DuckDB as-of raw feature snapshot per date, run the old V1 "
                "bundle without cross-sectional z-score and the new V1 bundle with "
                "cross-sectional z-score, then join by ticker/date."
            ),
        },
        "models": {
            "old": {
                "meta_path": old_bundle.meta_path,
                "model_file": old_bundle.model_file_name,
                "trained_at": old_bundle.trained_at,
                "cross_sectional_zscore": False,
                "feature_count": len(old_bundle.feature_columns),
            },
            "new": {
                "meta_path": new_bundle.meta_path,
                "model_file": new_bundle.model_file_name,
                "trained_at": new_bundle.trained_at,
                "cross_sectional_zscore": True,
                "feature_count": len(new_bundle.feature_columns),
            },
        },
        "days": day_summaries,
    }

    if not detail.empty:
        report["aggregate"] = {
            "rows": int(len(detail)),
            "prob_edge_loss": _series_stats(detail["prob_edge_loss"]),
            "prob_edge_delta": _series_stats(detail["prob_edge_delta"]),
            "signal_changed_count": int(detail["signal_changed"].sum()),
            "signal_changed_rate": _round(detail["signal_changed"].mean()),
            "old_to_new_down_count": int(detail["old_to_new_down"].sum()),
            "old_up_to_new_down_count": int(detail["old_up_to_new_down"].sum()),
            "old_signal_counts": _counts(detail["old_signal"]),
            "new_signal_counts": _counts(detail["new_signal"]),
        }

    report["promotion_gate"] = evaluate_promotion_gate(
        detail,
        dict(gate_thresholds or DEFAULT_GATE_THRESHOLDS),
    )

    with open(output_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)

    with open(output_md, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))

    if verbose:
        _safe_print(f"\nJSON report: {output_json}")
        _safe_print(f"Markdown report: {output_md}")
        _safe_print(f"Joined detail CSV: {output_csv}")

    return report


def render_markdown(report: dict[str, Any]) -> str:
    scope = report["scope"]
    lines = [
        "# V1 Same-Snapshot A/B Test",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Dates: `{', '.join(scope['dates'])}`",
        f"- Detail CSV: `{scope['detail_csv']}`",
        "- Method: same DuckDB as-of raw feature snapshot, old V1 without z-score vs new V1 with z-score.",
        "",
        "## Model Bundles",
        "",
        "| Bundle | Model | Trained At | Z-score | Features |",
        "|---|---|---|---:|---:|",
    ]
    for label in ["old", "new"]:
        model = report["models"][label]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(model["model_file"]),
                    str(model["trained_at"]),
                    str(model["cross_sectional_zscore"]),
                    str(model["feature_count"]),
                ]
            )
            + " |"
        )

    aggregate = report.get("aggregate", {})
    if aggregate:
        lines.extend(
            [
                "",
                "## Aggregate Readout",
                "",
                "| Rows | Avg Edge Loss | Median Edge Loss | Signal Changed | Old->New DOWN | Old UP->New DOWN |",
                "|---:|---:|---:|---:|---:|---:|",
                "| "
                + " | ".join(
                    [
                        str(aggregate["rows"]),
                        _format_num((aggregate["prob_edge_loss"] or {}).get("mean")),
                        _format_num((aggregate["prob_edge_loss"] or {}).get("median")),
                        _format_pct(aggregate.get("signal_changed_rate")),
                        str(aggregate.get("old_to_new_down_count")),
                        str(aggregate.get("old_up_to_new_down_count")),
                    ]
                )
                + " |",
                "",
                f"- Old signal counts: `{aggregate.get('old_signal_counts', {})}`",
                f"- New signal counts: `{aggregate.get('new_signal_counts', {})}`",
            ]
        )

    gate = report.get("promotion_gate", {})
    if gate:
        status = "PASS" if gate.get("passed") else "FAIL"
        metrics = gate.get("metrics", {})
        lines.extend(
            [
                "",
                "## Promotion Gate",
                "",
                f"- Gate status: **{status}**",
                f"- Old UP -> New DOWN rate: `{_format_pct(metrics.get('old_up_to_new_down_rate'))}`",
                f"- Top old-edge decile mean edge loss: `{_format_num(metrics.get('top_old_edge_decile_edge_loss_mean'))}`",
                f"- New UP count: `{metrics.get('new_up_count')}`",
                f"- V2 Top30 new DOWN rate: `{_format_pct(metrics.get('v2_top30_new_down_rate'))}`",
                "",
                "| Check | Value | Threshold | Result |",
                "|---|---:|---:|---|",
            ]
        )
        for check in gate.get("checks", []):
            value = check.get("value")
            formatted = _format_pct(value) if isinstance(value, float) and "rate" in check["name"] else str(value)
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(check["name"]),
                        formatted,
                        f"{check['direction']} {check['threshold']}",
                        "PASS" if check.get("passed") else "FAIL",
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Daily Summary",
            "",
            "| Date | Rows | Old Edge Mean | New Edge Mean | Avg Edge Loss | Signal Changed | Old->New DOWN | Snapshot Fingerprint |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for day in report["days"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(day["date"]),
                    str(day["joined_rows"]),
                    _format_num((day["old_prob_edge"] or {}).get("mean")),
                    _format_num((day["new_prob_edge"] or {}).get("mean")),
                    _format_num((day["prob_edge_loss"] or {}).get("mean")),
                    _format_pct(day.get("signal_changed_rate")),
                    str(day.get("old_to_new_down_count")),
                    str(day.get("snapshot_fingerprint", ""))[:12],
                ]
            )
            + " |"
        )

    for day in report["days"]:
        lines.extend(
            [
                "",
                f"## {day['date']} Largest Edge Loss",
                "",
                "| Ticker | Old Signal | New Signal | Old Edge | New Edge | Edge Loss | V2 Pred20D | Production Rec |",
                "|---|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in day.get("top_prob_edge_loss", [])[:15]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("ticker", "")),
                        str(row.get("old_signal", "")),
                        str(row.get("new_signal", "")),
                        _format_num(row.get("old_prob_edge")),
                        _format_num(row.get("new_prob_edge")),
                        _format_num(row.get("prob_edge_loss")),
                        _format_pct(row.get("production_v2_pred_return_20d")),
                        str(row.get("production_recommendation") or "-"),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `prob_edge_loss = old_prob_edge - new_prob_edge`; positive values mean the new z-score model bundle removed bullish edge on the exact same DuckDB snapshot.",
            "- This is the clean production-bundle A/B for incident closure: same date, same tickers, same raw feature source, two processors/models, joined by ticker/date.",
            "- `same_snapshot_fingerprint` is included so the joined rows can be traced back to the exact raw feature frame used by both branches.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V1 same-snapshot A/B incident test.")
    parser.add_argument("--date", action="append", dest="dates", help="As-of date YYYY-MM-DD. Repeatable.")
    parser.add_argument("--old-meta", default=DEFAULT_OLD_META, help="Old V1 meta path.")
    parser.add_argument("--new-meta", default=DEFAULT_NEW_META, help="New V1 meta path.")
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--max-stocks", type=int, default=0, help="Limit tickers for smoke testing.")
    parser.add_argument(
        "--assert-gate",
        action="store_true",
        help="Exit non-zero when the promotion gate fails.",
    )
    parser.add_argument(
        "--max-up-to-down-rate",
        type=float,
        default=DEFAULT_GATE_THRESHOLDS["max_old_up_to_new_down_rate"],
        help="Maximum allowed Old UP -> New DOWN flip rate.",
    )
    parser.add_argument(
        "--max-top-decile-edge-loss",
        type=float,
        default=DEFAULT_GATE_THRESHOLDS["max_top_decile_edge_loss"],
        help="Maximum allowed mean edge loss in the top old-edge decile.",
    )
    parser.add_argument(
        "--min-new-up-count",
        type=int,
        default=DEFAULT_GATE_THRESHOLDS["min_new_up_count"],
        help="Minimum allowed total new UP signals.",
    )
    parser.add_argument(
        "--max-v2-top30-new-down-rate",
        type=float,
        default=DEFAULT_GATE_THRESHOLDS["max_v2_top30_new_down_rate"],
        help="Maximum allowed new DOWN rate among production V2 Top30 candidates.",
    )
    parser.add_argument(
        "--max-new-down-rate",
        type=float,
        default=DEFAULT_GATE_THRESHOLDS["max_new_down_rate"],
        help="Maximum allowed total new DOWN signal rate.",
    )
    parser.add_argument(
        "--include-stale",
        action="store_true",
        help="Allow a ticker whose latest DuckDB row is before the requested date.",
    )
    args = parser.parse_args()

    thresholds = {
        "max_old_up_to_new_down_rate": args.max_up_to_down_rate,
        "max_top_decile_edge_loss": args.max_top_decile_edge_loss,
        "min_new_up_count": args.min_new_up_count,
        "max_v2_top30_new_down_rate": args.max_v2_top30_new_down_rate,
        "max_new_down_rate": args.max_new_down_rate,
    }
    report = generate_report(
        dates=args.dates or DEFAULT_DATES,
        old_meta=args.old_meta,
        new_meta=args.new_meta,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
        max_stocks=args.max_stocks,
        same_day_only=not args.include_stale,
        gate_thresholds=thresholds,
    )

    gate = report.get("promotion_gate", {})
    gate_status = "PASS" if gate.get("passed") else "FAIL"
    _safe_print(f"Promotion gate: {gate_status}")
    if args.assert_gate and not gate.get("passed"):
        _safe_print("Promotion gate failures:")
        for check in gate.get("checks", []):
            if check.get("required", True) and not check.get("passed"):
                _safe_print(
                    f"  - {check['name']}: value={check.get('value')} "
                    f"threshold={check['direction']} {check.get('threshold')}"
                )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
