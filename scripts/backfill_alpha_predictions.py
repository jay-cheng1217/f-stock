"""Backfill research context onto historical prediction artifacts.

The script keeps the existing production prediction artifact intact and only
adds classifier and penalty-overlay context columns needed by research gates.
It builds point-in-time feature snapshots from DuckDB, so the resulting
predictions_*_YYYY-MM-DD.csv files can be compared apples-to-apples against the
existing predictions_YYYY-MM-DD.csv files.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import BASE_DIR, DAILY_K_DIR, FINANCIAL_DIR, MODEL_DIR, REPORT_DIR
from ml.dataset import _apply_percentile_ranking, _winsorize_features, apply_snapshot_zscore
from ml.features.industry import compute_industry_features
from ml.features.registry import get_available_features
from ml.features.sector import compute_sector_features
from ml.predict import (
    PENALTY_CONTEXT_COLS,
    _attach_beta_artifacts,
    _attach_liquidity_artifacts,
    _attach_penalty_context_artifacts,
)
from scripts.v1_snapshot_ab_test import (
    _compute_single_stock_features,
    _eligible_history,
    build_duckdb_snapshot,
)

DEFAULT_START_DATE = "2026-04-09"
DEFAULT_END_DATE = "2026-04-20"
DEFAULT_OUTPUT_PREFIX = "predictions_alpha_cls"
SNAPSHOT_SOURCES = {"auto", "duckdb", "csv"}
DEFAULT_CHUNK_SIZE_DAYS = 5
DEFAULT_MIN_ARTIFACT_BYTES = 1024
DEFAULT_MANIFEST_PATH = str(Path(REPORT_DIR) / "backfill_alpha_predictions_latest.jsonl")
BALANCE_SHEET_DIR = Path(BASE_DIR) / "資產負債"


def _latest_alpha_meta() -> Path:
    candidates = sorted(Path(MODEL_DIR).glob("lgbm_alpha_cls_v2_*_meta.json"))
    if not candidates:
        raise FileNotFoundError("No lgbm_alpha_cls_v2_*_meta.json found")
    return candidates[-1]


def _load_alpha_model(meta_path: str | None) -> tuple[lgb.Booster, dict[str, Any]]:
    path = Path(meta_path).resolve() if meta_path else _latest_alpha_meta()
    with path.open("r", encoding="utf-8") as fh:
        meta = json.load(fh)
    model = lgb.Booster(model_file=meta["model_file"])
    return model, meta


def _date_range(start_date: str, end_date: str) -> list[str]:
    days = pd.bdate_range(start_date, end_date)
    return [day.strftime("%Y-%m-%d") for day in days]


def _feature_frame(snapshot: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
            for col in feature_cols
        }
    )


def _score_snapshot(
    snapshot: pd.DataFrame,
    model: lgb.Booster,
    meta: dict[str, Any],
) -> pd.DataFrame:
    feature_cols = list(meta.get("feature_columns", []))
    if not feature_cols:
        raise ValueError("Alpha classifier meta has no feature_columns")

    input_snapshot = apply_snapshot_zscore(snapshot) if meta.get("cross_sectional_zscore") else snapshot
    x = _feature_frame(input_snapshot, feature_cols)
    prob = model.predict(x.values)

    scores = snapshot[["ticker", "Date"]].copy()
    scores["ticker"] = scores["ticker"].astype(str)
    scores["date"] = pd.to_datetime(scores["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    scores["alpha_win_prob_20d"] = prob
    alpha_prob = pd.to_numeric(scores["alpha_win_prob_20d"], errors="coerce")
    scores["alpha_win_prob_percentile"] = alpha_prob.rank(pct=True, method="average")
    scores["alpha_win_prob_rank"] = alpha_prob.rank(ascending=False, method="min")
    scores["alpha_classifier_model_file"] = os.path.basename(str(meta.get("model_file", "")))
    scores["alpha_classifier_threshold"] = float(meta.get("default_gate_threshold", 0.52))

    context = snapshot[["ticker", "Date", "Close"]].copy()
    context["ticker"] = context["ticker"].astype(str)
    context["date"] = pd.to_datetime(context["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    context["close"] = pd.to_numeric(context["Close"], errors="coerce")
    context = _attach_liquidity_artifacts(context[["ticker", "date", "close"]], snapshot)
    context = _attach_beta_artifacts(context)
    context = _attach_penalty_context_artifacts(context, snapshot)
    context_cols = [
        "ticker",
        "date",
        "volume_today",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "beta_60",
        *PENALTY_CONTEXT_COLS,
    ]
    scores = scores.merge(context[context_cols], on=["ticker", "date"], how="left")
    return scores.drop(columns=["Date"])


def _build_csv_snapshot(
    as_of_date: str,
    *,
    max_stocks: int = 0,
    same_day_only: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    as_of_ts = pd.Timestamp(as_of_date)
    files = sorted(Path(DAILY_K_DIR).glob("*.csv"))
    if max_stocks > 0:
        files = files[:max_stocks]

    available = get_available_features()
    rows: list[pd.DataFrame] = []
    total = len(files)
    for idx, path in enumerate(files, start=1):
        ticker = path.stem
        if verbose and (idx == 1 or idx % 250 == 0 or idx == total):
            print(f"  csv feature snapshot {as_of_date}: {idx}/{total}", flush=True)
        history = pd.read_csv(path, dtype={"Date": str})
        history["Date"] = pd.to_datetime(history["Date"], errors="coerce").astype("datetime64[ns]")
        history = history.dropna(subset=["Date"]).sort_values("Date")
        history = history[history["Date"] <= as_of_ts].copy()
        if history.empty:
            continue
        history["Ticker"] = ticker
        if not _eligible_history(history, as_of_ts, same_day_only=same_day_only):
            continue

        featured = _compute_single_stock_features(history, ticker=str(ticker), available=available)
        latest = featured[featured["Date"].dt.normalize() == as_of_ts.normalize()].tail(1)
        if latest.empty and not same_day_only:
            latest = featured.tail(1)
        if not latest.empty:
            rows.append(latest.copy())

    if not rows:
        raise ValueError(f"No eligible CSV snapshot rows built for {as_of_date}")

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


def _finalize_snapshot_rows(rows: list[pd.DataFrame], as_of_date: str) -> pd.DataFrame:
    if not rows:
        raise ValueError(f"No eligible CSV snapshot rows built for {as_of_date}")

    available = get_available_features()
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


def _build_csv_snapshots(
    date_values: list[str],
    *,
    max_stocks: int = 0,
    same_day_only: bool = True,
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    if not date_values:
        return {}

    target_dates = [pd.Timestamp(value) for value in date_values]
    target_set = {ts.normalize() for ts in target_dates}
    end_ts = max(target_dates)
    files = sorted(Path(DAILY_K_DIR).glob("*.csv"))
    if max_stocks > 0:
        files = files[:max_stocks]

    available = get_available_features()
    rows_by_date: dict[str, list[pd.DataFrame]] = {value: [] for value in date_values}
    total = len(files)
    for idx, path in enumerate(files, start=1):
        ticker = path.stem
        if verbose and (idx == 1 or idx % 100 == 0 or idx == total):
            print(f"  csv bulk feature snapshots: {idx}/{total}", flush=True)

        history = pd.read_csv(path, dtype={"Date": str})
        history["Date"] = pd.to_datetime(history["Date"], errors="coerce").astype("datetime64[ns]")
        history = history.dropna(subset=["Date"]).sort_values("Date")
        history = history[history["Date"] <= end_ts].copy()
        if history.empty:
            continue
        history["Ticker"] = ticker

        available_dates = set(history["Date"].dt.normalize())
        if same_day_only and not (available_dates & target_set):
            continue

        featured = _compute_single_stock_features(history, ticker=str(ticker), available=available)
        for date_value, as_of_ts in zip(date_values, target_dates):
            history_until = history[history["Date"] <= as_of_ts]
            if history_until.empty:
                continue
            if not _eligible_history(history_until, as_of_ts, same_day_only=same_day_only):
                continue
            latest = featured[featured["Date"].dt.normalize() == as_of_ts.normalize()].tail(1)
            if latest.empty and not same_day_only:
                latest = featured[featured["Date"] <= as_of_ts].tail(1)
            if not latest.empty:
                rows_by_date[date_value].append(latest.copy())

    return {
        date_value: _finalize_snapshot_rows(rows, date_value)
        for date_value, rows in rows_by_date.items()
        if rows
    }


def _build_feature_snapshot(
    date_value: str,
    *,
    snapshot_source: str,
    max_stocks: int,
) -> pd.DataFrame:
    if snapshot_source not in SNAPSHOT_SOURCES:
        raise ValueError(f"Unsupported snapshot source: {snapshot_source}")

    if snapshot_source in {"auto", "duckdb"}:
        try:
            return build_duckdb_snapshot(
                date_value,
                max_stocks=max_stocks,
                same_day_only=True,
                verbose=True,
            )
        except Exception as exc:
            if snapshot_source == "duckdb":
                raise
            print(f"[alpha-backfill] DuckDB snapshot failed for {date_value}: {exc}", flush=True)
            print(f"[alpha-backfill] fallback to CSV daily K snapshot for {date_value}", flush=True)

    return _build_csv_snapshot(
        date_value,
        max_stocks=max_stocks,
        same_day_only=True,
        verbose=True,
    )


def _chunked_dates(date_values: list[str], chunk_size_days: int) -> list[list[str]]:
    if chunk_size_days <= 0:
        return [date_values]
    return [
        date_values[idx : idx + chunk_size_days]
        for idx in range(0, len(date_values), chunk_size_days)
    ]


def _artifact_health(path: Path, *, min_bytes: int) -> tuple[bool, str]:
    if not path.exists():
        return False, "missing"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"stat_error:{exc}"
    if size < min_bytes:
        return False, f"too_small:{size}<{min_bytes}"
    return True, f"ok:{size}"


def _append_manifest(manifest_path: str | None, row: dict[str, Any]) -> None:
    if not manifest_path:
        return
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _build_unified_signal_artifact(
    *,
    date_value: str,
    prediction_20d_path: Path,
    output_prefix: str,
    force: bool,
    min_output_bytes: int,
    disable_t1: bool,
    top_n_20d: int,
    penalty_overlay: bool,
    penalty_overlay_version: str,
) -> dict[str, Any]:
    from scripts.build_unified_signals import build_unified_signals

    output_path = Path(MODEL_DIR) / f"{output_prefix}_{date_value}.csv"
    prediction_cols = pd.read_csv(prediction_20d_path, nrows=0).columns
    if penalty_overlay and "pred_return_20d" not in prediction_cols:
        return {
            "unified_output": str(output_path),
            "unified_status": "skipped_missing_pred_return_20d",
            "unified_health": "not_applicable_legacy_prediction_schema",
        }

    healthy, reason = _artifact_health(output_path, min_bytes=min_output_bytes)
    if healthy and not force:
        print(
            f"[alpha-backfill] skip unified {date_value}: {output_path.name} exists ({reason})",
            flush=True,
        )
        return {
            "unified_output": str(output_path),
            "unified_status": "exists",
            "unified_health": reason,
        }
    if output_path.exists() and not healthy:
        print(
            f"[alpha-backfill] rebuild unified {date_value}: {output_path.name} unhealthy ({reason})",
            flush=True,
        )

    result = build_unified_signals(
        pred_date=date_value,
        prediction_20d_path=str(prediction_20d_path),
        prediction_t1_path=None,
        output_prefix=output_prefix,
        top_n_20d=top_n_20d,
        penalty_overlay=penalty_overlay,
        penalty_overlay_version=penalty_overlay_version,
        disable_t1=disable_t1,
        verbose=True,
    )
    healthy, reason = _artifact_health(Path(result.output_path), min_bytes=min_output_bytes)
    if not healthy:
        raise RuntimeError(f"Unified artifact failed health check: {result.output_path} ({reason})")
    return {
        "unified_output": result.output_path,
        "unified_status": "written",
        "unified_health": reason,
        "unified_total_names": int(result.total_names),
        "unified_total_units": int(result.total_units),
        "unified_dual_count": int(result.dual_count),
        "unified_d20_only_count": int(result.d20_only_count),
        "unified_t1_only_count": int(result.t1_only_count),
        "unified_tradability_blocked_count": int(result.tradability_blocked_count),
    }


def _pit_financial_quarter(as_of_date: str) -> tuple[int, int]:
    ts = pd.Timestamp(as_of_date)
    if ts >= pd.Timestamp("2026-03-31"):
        return 2025, 4
    return 2025, 3


def _load_financial_context(as_of_date: str) -> pd.DataFrame:
    year, season = _pit_financial_quarter(as_of_date)
    financial_path = Path(FINANCIAL_DIR) / f"financial_{year}Q{season}.csv"
    bs_path = BALANCE_SHEET_DIR / f"bs_{year}Q{season}.csv"
    if not financial_path.exists():
        return pd.DataFrame(columns=["ticker"])

    financial = pd.read_csv(financial_path, dtype={"Ticker": str}, on_bad_lines="skip")
    financial["ticker"] = financial["Ticker"].astype(str).str.strip()
    out = financial[["ticker"]].copy()
    out["gross_margin_latest"] = pd.to_numeric(financial.get("Gross_Margin_Pct"), errors="coerce")
    out["operating_margin_latest"] = pd.to_numeric(financial.get("Operating_Margin_Pct"), errors="coerce")
    out["net_margin_latest"] = pd.to_numeric(financial.get("Net_Margin_Pct"), errors="coerce")

    if bs_path.exists():
        bs = pd.read_csv(bs_path, dtype={"Ticker": str}, on_bad_lines="skip")
        bs["ticker"] = bs["Ticker"].astype(str).str.strip()
        merged = out.merge(
            bs[["ticker", "Total_Assets", "Total_Equity"]],
            on="ticker",
            how="left",
        )
        revenue = pd.to_numeric(financial.get("Revenue_M"), errors="coerce")
        net_margin = pd.to_numeric(financial.get("Net_Margin_Pct"), errors="coerce")
        if "Net_Income_M" in financial.columns:
            net_income_m = pd.to_numeric(financial["Net_Income_M"], errors="coerce")
        else:
            net_income_m = pd.Series(np.nan, index=financial.index, dtype="float64")
        net_income_m = net_income_m.where(net_income_m.notna(), revenue * net_margin / 100.0)
        merged["net_income_m"] = net_income_m.to_numpy()
        assets = pd.to_numeric(merged["Total_Assets"], errors="coerce")
        equity = pd.to_numeric(merged["Total_Equity"], errors="coerce")
        net_income_thousand = pd.to_numeric(merged["net_income_m"], errors="coerce") * 1000.0
        merged["roa_annualized"] = np.where(assets.abs() > 0.01, net_income_thousand / assets * 100.0, np.nan)
        merged["roe_annualized"] = np.where(equity.abs() > 0.01, net_income_thousand / equity * 100.0, np.nan)
        out = merged.drop(columns=["Total_Assets", "Total_Equity", "net_income_m"])
    else:
        out["roa_annualized"] = np.nan
        out["roe_annualized"] = np.nan

    return out.drop_duplicates(subset=["ticker"], keep="last")


def _fast_market_context_for_ticker(ticker: str, as_of_ts: pd.Timestamp) -> dict[str, float]:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return {}
    try:
        stock = pd.read_csv(path, usecols=lambda col: col in {
            "Date",
            "Open",
            "Close",
            "Volume",
            "VOL_MA_5",
            "VOL_MA_20",
            "Foreign_BuySell",
            "Trust_BuySell",
            "Dealer_BuySell",
        })
    except Exception:
        return {}

    stock["Date"] = pd.to_datetime(stock["Date"], errors="coerce")
    stock = stock.dropna(subset=["Date"]).sort_values("Date")
    stock = stock[stock["Date"] <= as_of_ts].copy()
    if stock.empty:
        return {}

    close = pd.to_numeric(stock.get("Close"), errors="coerce")
    open_ = pd.to_numeric(stock.get("Open"), errors="coerce")
    volume = pd.to_numeric(stock.get("Volume"), errors="coerce").fillna(0.0)
    prev_close = close.shift(1).replace(0, np.nan)
    gap = open_ / prev_close - 1.0
    positive_gap = gap.clip(lower=0)
    foreign = pd.to_numeric(stock.get("Foreign_BuySell"), errors="coerce").fillna(0.0)
    trust = pd.to_numeric(stock.get("Trust_BuySell"), errors="coerce").fillna(0.0)
    dealer = pd.to_numeric(stock.get("Dealer_BuySell"), errors="coerce").fillna(0.0)
    inst = foreign + trust + dealer
    latest = stock.iloc[-1]
    latest_close = float(close.iloc[-1]) if len(close) else np.nan

    def _window_sum(series: pd.Series, window: int) -> float:
        value = float(series.tail(window).sum())
        return value if np.isfinite(value) else np.nan

    def _window_norm(series: pd.Series, window: int) -> float:
        vol_sum = float(volume.tail(window).sum())
        if not np.isfinite(vol_sum) or vol_sum <= 0:
            return np.nan
        value = float(series.tail(window).sum() / vol_sum)
        return value if np.isfinite(value) else np.nan

    avg_5d_volume = pd.to_numeric(pd.Series([latest.get("VOL_MA_5")]), errors="coerce").iloc[0]
    avg_20d_volume = pd.to_numeric(pd.Series([latest.get("VOL_MA_20")]), errors="coerce").iloc[0]
    if pd.isna(avg_5d_volume):
        avg_5d_volume = float(volume.tail(5).mean())
    if pd.isna(avg_20d_volume):
        avg_20d_volume = float(volume.tail(20).mean())

    return {
        "Volume": float(volume.iloc[-1]) if len(volume) else np.nan,
        "VOL_MA_5": avg_5d_volume,
        "VOL_MA_20": avg_20d_volume,
        "AMOUNT_MA_5": avg_5d_volume * latest_close if np.isfinite(latest_close) else np.nan,
        "AMOUNT_MA_20": avg_20d_volume * latest_close if np.isfinite(latest_close) else np.nan,
        "gap_pct": float(gap.iloc[-1]) if len(gap) and np.isfinite(gap.iloc[-1]) else np.nan,
        "gap_freq_10d": float((gap.tail(10) > 0).mean()) if len(gap) else np.nan,
        "gap_up_avg_5d": float(positive_gap.tail(5).mean()) if len(positive_gap) else np.nan,
        "foreign_cumsum_20d_raw": _window_sum(foreign, 20),
        "foreign_cumsum_20d_norm_raw": _window_norm(foreign, 20),
        "foreign_cumsum_10d_raw": _window_sum(foreign, 10),
        "foreign_cumsum_10d_norm_raw": _window_norm(foreign, 10),
        "inst_buy_ratio_20d": _window_norm(inst, 20),
    }


def _context_only_scores(pred: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    base = pred[["ticker", "date", "close"]].copy()
    base["ticker"] = base["ticker"].astype(str).str.strip()
    base["date"] = base["date"].astype(str)
    base["close"] = pd.to_numeric(base["close"], errors="coerce")
    as_of_ts = pd.Timestamp(as_of_date)

    rows = [
        _fast_market_context_for_ticker(ticker, as_of_ts)
        for ticker in base["ticker"].astype(str)
    ]
    snapshot = pd.DataFrame(rows, index=base.index)
    financial = _load_financial_context(as_of_date)
    if not financial.empty:
        snapshot = snapshot.join(
            base[["ticker"]].merge(financial, on="ticker", how="left").drop(columns=["ticker"])
        )
    else:
        for col in [
            "roa_annualized",
            "roe_annualized",
            "operating_margin_latest",
            "net_margin_latest",
            "gross_margin_latest",
        ]:
            snapshot[col] = np.nan

    context = _attach_liquidity_artifacts(base[["ticker", "date", "close"]], snapshot)
    context = _attach_beta_artifacts(context)
    context = _attach_penalty_context_artifacts(context, snapshot)
    context_cols = [
        "ticker",
        "date",
        "volume_today",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "beta_60",
        *PENALTY_CONTEXT_COLS,
    ]
    return context[context_cols]


def backfill_alpha_predictions(
    *,
    start_date: str,
    end_date: str,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    alpha_meta_path: str | None = None,
    force: bool = False,
    max_stocks: int = 0,
    snapshot_source: str = "auto",
    context_only: bool = False,
    chunk_size_days: int = DEFAULT_CHUNK_SIZE_DAYS,
    chunk_sleep_seconds: float = 0.0,
    min_source_bytes: int = DEFAULT_MIN_ARTIFACT_BYTES,
    min_output_bytes: int = DEFAULT_MIN_ARTIFACT_BYTES,
    manifest_path: str | None = DEFAULT_MANIFEST_PATH,
    reset_manifest: bool = True,
    fail_fast: bool = False,
    build_unified_signals_after: bool = False,
    unified_output_prefix: str = "unified_signals_backfill",
    force_unified: bool = False,
    unified_min_output_bytes: int = DEFAULT_MIN_ARTIFACT_BYTES,
    disable_t1: bool = False,
    top_n_20d: int = 30,
    unified_penalty_overlay: bool = False,
    penalty_overlay_version: str = "v1",
) -> list[dict[str, Any]]:
    model: lgb.Booster | None = None
    meta: dict[str, Any] = {}
    if not context_only:
        model, meta = _load_alpha_model(alpha_meta_path)
    rows: list[dict[str, Any]] = []
    date_values = _date_range(start_date, end_date)
    chunks = _chunked_dates(date_values, chunk_size_days)
    if manifest_path and reset_manifest:
        manifest = Path(manifest_path)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("", encoding="utf-8")
        print(f"[alpha-backfill] manifest: {manifest}", flush=True)
    elif manifest_path:
        print(f"[alpha-backfill] append manifest: {manifest_path}", flush=True)

    for chunk_index, chunk_dates in enumerate(chunks, start=1):
        if not chunk_dates:
            continue
        print(
            "[alpha-backfill] chunk "
            f"{chunk_index}/{len(chunks)} {chunk_dates[0]} to {chunk_dates[-1]}",
            flush=True,
        )
        snapshot_dates: list[str] = []
        for date_value in chunk_dates:
            source_path = Path(MODEL_DIR) / f"predictions_{date_value}.csv"
            output_path = Path(MODEL_DIR) / f"{output_prefix}_{date_value}.csv"
            source_healthy, _ = _artifact_health(source_path, min_bytes=min_source_bytes)
            output_healthy, _ = _artifact_health(output_path, min_bytes=min_output_bytes)
            if source_healthy and (force or not output_healthy):
                snapshot_dates.append(date_value)

        csv_snapshots: dict[str, pd.DataFrame] = {}
        if context_only:
            print("[alpha-backfill] context-only mode; skip classifier feature snapshots", flush=True)
        elif snapshot_source == "csv" and snapshot_dates:
            print(
                f"[alpha-backfill] prebuild CSV snapshots {snapshot_dates[0]} to {snapshot_dates[-1]}",
                flush=True,
            )
            csv_snapshots = _build_csv_snapshots(
                snapshot_dates,
                max_stocks=max_stocks,
                same_day_only=True,
                verbose=True,
            )
        elif snapshot_source == "csv":
            print("[alpha-backfill] all dates in chunk are checkpointed; skip CSV snapshot", flush=True)

        for date_value in chunk_dates:
            source_path = Path(MODEL_DIR) / f"predictions_{date_value}.csv"
            output_path = Path(MODEL_DIR) / f"{output_prefix}_{date_value}.csv"
            row: dict[str, Any] = {
                "date": date_value,
                "source": str(source_path),
                "output": str(output_path),
            }

            try:
                source_healthy, source_health = _artifact_health(
                    source_path,
                    min_bytes=min_source_bytes,
                )
                row["source_health"] = source_health
                if not source_healthy:
                    row["status"] = "missing_source" if source_health == "missing" else "bad_source"
                    print(
                        f"[alpha-backfill] skip {date_value}: {source_path.name} {source_health}",
                        flush=True,
                    )
                    rows.append(row)
                    _append_manifest(manifest_path, row)
                    continue

                output_healthy, output_health = _artifact_health(
                    output_path,
                    min_bytes=min_output_bytes,
                )
                if output_healthy and not force:
                    print(
                        f"[alpha-backfill] skip {date_value}: {output_path.name} exists ({output_health})",
                        flush=True,
                    )
                    row.update(
                        {
                            "status": "exists",
                            "output_health": output_health,
                        }
                    )
                else:
                    if output_path.exists() and not output_healthy:
                        print(
                            f"[alpha-backfill] rebuild {date_value}: "
                            f"{output_path.name} unhealthy ({output_health})",
                            flush=True,
                        )
                    pred = pd.read_csv(source_path, dtype={"ticker": str})
                    pred["ticker"] = pred["ticker"].astype(str)
                    pred["date"] = pred["date"].astype(str)
                    drop_cols = [
                        "alpha_win_prob_20d",
                        "alpha_win_prob_percentile",
                        "alpha_win_prob_rank",
                        "alpha_classifier_model_file",
                        "alpha_classifier_threshold",
                        "volume_today",
                        "avg_5d_volume",
                        "avg_20d_volume",
                        "avg_5d_amount",
                        "avg_20d_amount",
                        "beta_60",
                        *PENALTY_CONTEXT_COLS,
                    ]
                    pred = pred.drop(columns=[col for col in drop_cols if col in pred.columns], errors="ignore")

                    if context_only:
                        print(f"[alpha-backfill] build penalty context {date_value}", flush=True)
                        scores = _context_only_scores(pred, date_value)
                        snapshot_rows = int(len(scores))
                        scored = None
                    else:
                        print(f"[alpha-backfill] build snapshot {date_value}", flush=True)
                        if snapshot_source == "csv":
                            snapshot = csv_snapshots.get(date_value)
                            if snapshot is None:
                                row["status"] = "no_snapshot_rows"
                                print(
                                    f"[alpha-backfill] skip {date_value}: no CSV snapshot rows",
                                    flush=True,
                                )
                                rows.append(row)
                                _append_manifest(manifest_path, row)
                                continue
                        else:
                            snapshot = _build_feature_snapshot(
                                date_value,
                                snapshot_source=snapshot_source,
                                max_stocks=max_stocks,
                            )
                        if model is None:
                            raise RuntimeError("Alpha model was not loaded")
                        scores = _score_snapshot(snapshot, model=model, meta=meta)
                        snapshot_rows = int(len(snapshot))
                        scored = int(scores["alpha_win_prob_20d"].notna().sum())

                    merged = pred.merge(scores, on=["ticker", "date"], how="left")

                    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
                    merged.to_csv(tmp_path, index=False, encoding="utf-8-sig")
                    os.replace(tmp_path, output_path)

                    output_healthy, output_health = _artifact_health(
                        output_path,
                        min_bytes=min_output_bytes,
                    )
                    if not output_healthy:
                        raise RuntimeError(
                            f"Output artifact failed health check: {output_path} ({output_health})"
                        )

                    if scored is None:
                        scored = int(merged["beta_60"].notna().sum()) if "beta_60" in merged.columns else 0
                    print(
                        f"[alpha-backfill] wrote {output_path.name}: rows={len(merged):,} "
                        f"context_rows={scored:,}",
                        flush=True,
                    )
                    row.update(
                        {
                            "status": "written",
                            "output_health": output_health,
                            "rows": int(len(merged)),
                            "scored_rows": scored,
                            "snapshot_rows": snapshot_rows,
                            "alpha_model_file": os.path.basename(str(meta.get("model_file", ""))),
                            "context_only": context_only,
                        }
                    )

                if build_unified_signals_after:
                    row.update(
                        _build_unified_signal_artifact(
                            date_value=date_value,
                            prediction_20d_path=output_path,
                            output_prefix=unified_output_prefix,
                            force=force_unified,
                            min_output_bytes=unified_min_output_bytes,
                            disable_t1=disable_t1,
                            top_n_20d=top_n_20d,
                            penalty_overlay=unified_penalty_overlay,
                            penalty_overlay_version=penalty_overlay_version,
                        )
                    )

                rows.append(row)
                _append_manifest(manifest_path, row)
            except Exception as exc:
                row.update({"status": "error", "error": repr(exc)})
                rows.append(row)
                _append_manifest(manifest_path, row)
                print(f"[alpha-backfill] error {date_value}: {exc}", flush=True)
                if fail_fast:
                    raise

        del csv_snapshots
        if chunk_sleep_seconds > 0 and chunk_index < len(chunks):
            time.sleep(chunk_sleep_seconds)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill alpha-classifier columns onto historical predictions.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--alpha-meta", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--snapshot-source", choices=sorted(SNAPSHOT_SOURCES), default="auto")
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="Fast V2 mode: only attach penalty-overlay context; skip alpha classifier scoring.",
    )
    parser.add_argument(
        "--chunk-size-days",
        type=int,
        default=DEFAULT_CHUNK_SIZE_DAYS,
        help="Business-date chunk size. Use 0 to process the full range as one chunk.",
    )
    parser.add_argument("--chunk-sleep-seconds", type=float, default=0.0)
    parser.add_argument("--min-source-bytes", type=int, default=DEFAULT_MIN_ARTIFACT_BYTES)
    parser.add_argument("--min-output-bytes", type=int, default=DEFAULT_MIN_ARTIFACT_BYTES)
    parser.add_argument(
        "--manifest-path",
        default=DEFAULT_MANIFEST_PATH,
        help="JSONL checkpoint manifest path. Use an empty string to disable.",
    )
    parser.add_argument("--append-manifest", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--build-unified-signals",
        action="store_true",
        help="After each healthy enriched prediction, build the matching unified signal artifact.",
    )
    parser.add_argument("--unified-output-prefix", default="unified_signals_backfill")
    parser.add_argument("--force-unified", action="store_true")
    parser.add_argument("--unified-min-output-bytes", type=int, default=DEFAULT_MIN_ARTIFACT_BYTES)
    parser.add_argument(
        "--disable-t1",
        action="store_true",
        help="Research mode: build 20D-only unified signals without requiring T+1 artifacts.",
    )
    parser.add_argument("--top-n-20d", type=int, default=30)
    parser.add_argument(
        "--unified-penalty-overlay",
        action="store_true",
        help="Build unified signals with the Entry Penalty Overlay enabled.",
    )
    parser.add_argument("--penalty-overlay-version", default="v1", choices=["v1", "tuned"])
    args = parser.parse_args()

    rows = backfill_alpha_predictions(
        start_date=args.start_date,
        end_date=args.end_date,
        output_prefix=args.output_prefix,
        alpha_meta_path=args.alpha_meta,
        force=args.force,
        max_stocks=args.max_stocks,
        snapshot_source=args.snapshot_source,
        context_only=args.context_only,
        chunk_size_days=args.chunk_size_days,
        chunk_sleep_seconds=args.chunk_sleep_seconds,
        min_source_bytes=args.min_source_bytes,
        min_output_bytes=args.min_output_bytes,
        manifest_path=args.manifest_path or None,
        reset_manifest=not args.append_manifest,
        fail_fast=args.fail_fast,
        build_unified_signals_after=args.build_unified_signals,
        unified_output_prefix=args.unified_output_prefix,
        force_unified=args.force_unified,
        unified_min_output_bytes=args.unified_min_output_bytes,
        disable_t1=args.disable_t1,
        top_n_20d=args.top_n_20d,
        unified_penalty_overlay=args.unified_penalty_overlay,
        penalty_overlay_version=args.penalty_overlay_version,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
