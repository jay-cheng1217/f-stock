"""Run a fixed-snapshot A/B plus replay for the V2 alpha classifier line.

This is the incident-closure artifact for classifier target changes:
1. Train two classifier variants on the same training universe.
2. Use one DuckDB as-of raw feature snapshot per date for both branches.
3. Join predictions by ticker/date and report prob_edge deltas.
4. Replay both branches through the same unified signal + exit pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import duckdb

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR
from ml.dataset import apply_snapshot_zscore
from ml.predict import (
    PENALTY_CONTEXT_COLS,
    _attach_beta_artifacts,
    _attach_liquidity_artifacts,
    _attach_penalty_context_artifacts,
)
from scripts.build_unified_signals import build_unified_signals
from scripts.train_v2_alpha_classifier import train_alpha_classifier
from scripts.unified_execution_replay import run_replay
import scripts.v1_snapshot_ab_test as snapshot_ab_module
from scripts.v1_snapshot_ab_test import build_duckdb_snapshot, _snapshot_fingerprint

DEFAULT_DATES = [
    "2026-04-09",
    "2026-04-10",
]
DEFAULT_OUTPUT_DIR = Path(REPORT_DIR) / "v2_alpha_classifier_snapshot_ab_20260424"
DEFAULT_EXIT_POLICY = "ma5_break_plus_hwm_8pct"
DEFAULT_GATE_THRESHOLD = 0.52


def _latest_dataset_cache() -> Path:
    candidates = sorted(Path(MODEL_DIR).glob("dataset_cache_*.parquet"))
    if not candidates:
        raise FileNotFoundError("No ml/models/dataset_cache_*.parquet found")
    return candidates[-1]


@dataclass(frozen=True)
class ClassifierBundle:
    label: str
    target_mode: str
    threshold: float
    meta: dict[str, Any]
    model: lgb.Booster
    artifact_paths: tuple[Path, ...]

    @property
    def feature_columns(self) -> list[str]:
        return list(self.meta.get("feature_columns", []))

    @property
    def trained_at(self) -> str | None:
        value = self.meta.get("trained_at")
        return str(value) if value is not None else None

    @property
    def model_file_name(self) -> str:
        return os.path.basename(str(self.meta.get("model_file", "")))


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
        numeric = float(value)
    except Exception:
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _format_pct(value: Any, digits: int = 2) -> str:
    rounded = _round(value, digits + 2)
    if rounded is None:
        return "n/a"
    return f"{rounded * 100:.{digits}f}%"


def _format_num(value: Any, digits: int = 4) -> str:
    rounded = _round(value, digits)
    if rounded is None:
        return "n/a"
    return f"{rounded:.{digits}f}"


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
    if series.empty:
        return {}
    return {
        str(key): int(value)
        for key, value in series.fillna("NULL").astype(str).value_counts().sort_index().items()
    }


def _records(df: pd.DataFrame, columns: list[str], limit: int = 20) -> list[dict[str, Any]]:
    if df.empty:
        return []
    local = df.copy()
    for column in columns:
        if column not in local.columns:
            local[column] = np.nan
    rows: list[dict[str, Any]] = []
    for raw in local.head(limit)[columns].to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, (np.integer,)):
                row[key] = int(value)
            elif isinstance(value, (np.floating, float)):
                row[key] = _round(value)
            elif pd.isna(value):
                row[key] = None
            else:
                row[key] = value
        rows.append(row)
    return rows


def _feature_frame(snapshot: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    missing = [col for col in feature_cols if col not in snapshot.columns]
    frame = pd.DataFrame(
        {
            col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
            for col in feature_cols
        }
    )
    return frame, missing


@contextmanager
def _temp_duckdb_snapshot_path() -> Any:
    original_path = Path(str(snapshot_ab_module.DUCKDB_PATH))
    temp_dir = Path(tempfile.mkdtemp(prefix="duckdb_snapshot_copy_"))
    temp_db = temp_dir / original_path.name
    temp_wal = temp_dir / f"{original_path.name}.wal"
    original_wal = Path(f"{str(original_path)}.wal")
    try:
        shutil.copy2(original_path, temp_db)
        if original_wal.exists():
            shutil.copy2(original_wal, temp_wal)
        os.chmod(temp_db, stat.S_IWRITE | stat.S_IREAD)
        if temp_wal.exists():
            os.chmod(temp_wal, stat.S_IWRITE | stat.S_IREAD)
        old_value = snapshot_ab_module.DUCKDB_PATH
        old_loader = snapshot_ab_module._load_duckdb_daily_k
        snapshot_ab_module.DUCKDB_PATH = str(temp_db)
        def _temp_loader(as_of_date: str, max_stocks: int = 0) -> pd.DataFrame:
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
            con = duckdb.connect(str(temp_db), read_only=False)
            try:
                df = con.execute(sql, params).fetchdf()
            finally:
                con.close()
            if df.empty:
                raise ValueError(f"No daily_k rows found in DuckDB up to {as_of_date}")
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce").astype("datetime64[ns]")
            df["Ticker"] = df["Ticker"].astype(str)
            return df.dropna(subset=["Date", "Ticker"]).sort_values(["Ticker", "Date"])
        snapshot_ab_module._load_duckdb_daily_k = _temp_loader
        try:
            yield
        finally:
            snapshot_ab_module.DUCKDB_PATH = old_value
            snapshot_ab_module._load_duckdb_daily_k = old_loader
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _train_bundle(label: str, target_mode: str, device: str) -> ClassifierBundle:
    _safe_print(f"[alpha-ab] training {label} ({target_mode})")
    meta = train_alpha_classifier(
        device=device,
        save_model=True,
        target_mode=target_mode,
    )
    if not meta or not meta.get("model_file"):
        raise RuntimeError(f"Failed to train/save classifier bundle: {label}")

    model_path = Path(str(meta["model_file"]))
    model = lgb.Booster(model_file=str(model_path))
    ts = str(meta.get("trained_at") or "")
    threshold = float(meta.get("default_gate_threshold", DEFAULT_GATE_THRESHOLD))
    artifact_paths = (
        model_path,
        Path(str(model_path).replace(".txt", "_meta.json")),
        Path(REPORT_DIR) / f"v2_alpha_classifier_backtest_{target_mode}_{ts}.csv",
        Path(REPORT_DIR) / f"feature_importance_v2_alpha_classifier_{target_mode}_{ts}.csv",
    )
    return ClassifierBundle(
        label=label,
        target_mode=target_mode,
        threshold=threshold,
        meta=meta,
        model=model,
        artifact_paths=artifact_paths,
    )


def _cleanup_bundle_artifacts(bundle: ClassifierBundle) -> None:
    for path in bundle.artifact_paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _predict_bundle(snapshot: pd.DataFrame, bundle: ClassifierBundle) -> tuple[pd.DataFrame, list[str]]:
    zsnap = apply_snapshot_zscore(snapshot.copy())
    x, missing = _feature_frame(zsnap, bundle.feature_columns)
    alpha_prob = pd.to_numeric(pd.Series(bundle.model.predict(x.values), index=snapshot.index), errors="coerce")
    alpha_pct = alpha_prob.rank(pct=True, method="average")
    alpha_rank = alpha_prob.rank(ascending=False, method="min")

    pred = snapshot[["ticker", "Date", "Close"]].copy()
    pred["date"] = pd.to_datetime(pred["Date"]).dt.strftime("%Y-%m-%d")
    pred["close"] = pd.to_numeric(pred["Close"], errors="coerce")
    pred[f"{bundle.label}_alpha_win_prob_20d"] = alpha_prob.astype(np.float32)
    pred[f"{bundle.label}_alpha_win_prob_percentile"] = alpha_pct.astype(np.float32)
    pred[f"{bundle.label}_alpha_win_prob_rank"] = alpha_rank.astype(np.float32)
    pred[f"{bundle.label}_prob_edge"] = (alpha_prob - bundle.threshold).astype(np.float32)
    pred[f"{bundle.label}_gate_pass"] = alpha_prob.ge(bundle.threshold).fillna(False)
    pred[f"{bundle.label}_model_file"] = bundle.model_file_name
    pred[f"{bundle.label}_target_mode"] = bundle.target_mode
    pred[f"{bundle.label}_threshold"] = bundle.threshold
    return pred.drop(columns=["Date", "Close"]), missing


def _load_base_predictions(as_of_date: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"predictions_{as_of_date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Base 20D prediction file not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if "date" not in df.columns:
        raise ValueError(f"Prediction file missing date column: {path}")
    df = df[df["date"].astype(str) == as_of_date].copy()
    if df.empty:
        raise ValueError(f"Prediction file has no rows for {as_of_date}: {path}")
    return df


def _load_cached_snapshot(as_of_date: str, tickers: list[str]) -> pd.DataFrame:
    cache_path = _latest_dataset_cache()
    df = pd.read_parquet(cache_path, filters=[("Date", "==", pd.Timestamp(as_of_date))])
    if df.empty:
        raise ValueError(f"Dataset cache has no rows for {as_of_date}: {cache_path}")

    df["ticker"] = df["ticker"].astype(str)
    ticker_set = {str(ticker) for ticker in tickers}
    if ticker_set:
        df = df[df["ticker"].isin(ticker_set)].copy()
    if df.empty:
        raise ValueError(f"Dataset cache rows for {as_of_date} do not overlap prediction universe")
    return df.sort_values(["Date", "ticker"]).reset_index(drop=True)


def _load_same_snapshot_frame(as_of_date: str, tickers: list[str]) -> tuple[pd.DataFrame, str]:
    try:
        snapshot = _load_cached_snapshot(as_of_date, tickers)
        return snapshot, "dataset_cache"
    except Exception as exc:
        _safe_print(f"[alpha-ab] cache snapshot fallback for {as_of_date}: {exc}")

    with _temp_duckdb_snapshot_path():
        snapshot = build_duckdb_snapshot(as_of_date, same_day_only=True, verbose=False)
    if tickers:
        ticker_set = {str(ticker) for ticker in tickers}
        snapshot = snapshot[snapshot["ticker"].astype(str).isin(ticker_set)].copy()
    if snapshot.empty:
        raise ValueError(f"DuckDB snapshot has no overlap with prediction universe for {as_of_date}")
    return snapshot.sort_values(["Date", "ticker"]).reset_index(drop=True), "duckdb"


def _compare_day(
    as_of_date: str,
    old_bundle: ClassifierBundle,
    new_bundle: ClassifierBundle,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    base = _load_base_predictions(as_of_date)
    snapshot, snapshot_source = _load_same_snapshot_frame(
        as_of_date,
        tickers=base["ticker"].astype(str).tolist(),
    )
    old_pred, old_missing = _predict_bundle(snapshot, old_bundle)
    new_pred, new_missing = _predict_bundle(snapshot, new_bundle)
    context_cols = [
        "ticker",
        "date",
        "pred_return_20d",
        "risk_adjusted_return",
        "leaderboard_score",
        "recommendation",
        "sector",
        "beta_60",
        "gap_pct",
        "foreign_cumsum_20d_raw",
        "foreign_cumsum_20d_norm_raw",
        "roa_annualized",
        "operating_margin_latest",
    ]
    context = base[[col for col in context_cols if col in base.columns]].copy()
    snapshot_context = snapshot.copy()
    snapshot_context["date"] = pd.to_datetime(snapshot_context["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    rename_map = {
        "foreign_cumsum_20d": "foreign_cumsum_20d_raw",
        "foreign_cumsum_20d_norm": "foreign_cumsum_20d_norm_raw",
    }
    keep_cols = ["ticker", "date", "beta_60", "gap_pct", "roa_annualized", "operating_margin_latest"]
    for source_col, target_col in rename_map.items():
        if source_col in snapshot_context.columns and target_col not in snapshot_context.columns:
            snapshot_context[target_col] = snapshot_context[source_col]
    keep_cols.extend(rename_map.values())
    keep_cols = [col for col in keep_cols if col in snapshot_context.columns]
    snapshot_context = snapshot_context[keep_cols].drop_duplicates(subset=["ticker", "date"])

    joined = old_pred.merge(new_pred, on=["ticker", "date", "close"], how="inner")
    joined = joined.merge(context, on=["ticker", "date"], how="left")
    joined = joined.merge(snapshot_context, on=["ticker", "date"], how="left", suffixes=("", "_snapshot"))
    for column in ["beta_60", "gap_pct", "foreign_cumsum_20d_raw", "foreign_cumsum_20d_norm_raw", "roa_annualized", "operating_margin_latest"]:
        snapshot_col = f"{column}_snapshot"
        if snapshot_col in joined.columns:
            if column in joined.columns:
                joined[column] = joined[column].where(joined[column].notna(), joined[snapshot_col])
                joined = joined.drop(columns=[snapshot_col])
            else:
                joined = joined.rename(columns={snapshot_col: column})
    joined["snapshot_fingerprint"] = _snapshot_fingerprint(snapshot)
    joined["prob_edge_delta"] = joined["new_prob_edge"] - joined["old_prob_edge"]
    joined["prob_edge_loss"] = joined["old_prob_edge"] - joined["new_prob_edge"]
    joined["alpha_prob_delta"] = (
        joined["new_alpha_win_prob_20d"] - joined["old_alpha_win_prob_20d"]
    )
    joined["gate_changed"] = joined["old_gate_pass"] != joined["new_gate_pass"]
    joined["old_pass_new_fail"] = joined["old_gate_pass"] & (~joined["new_gate_pass"])
    joined["old_fail_new_pass"] = (~joined["old_gate_pass"]) & joined["new_gate_pass"]
    joined = joined.sort_values(
        ["prob_edge_loss", "old_prob_edge", "pred_return_20d", "ticker"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)

    top30 = joined.sort_values(
        ["pred_return_20d", "ticker"],
        ascending=[False, True],
        na_position="last",
    ).head(30)
    summary = {
        "date": as_of_date,
        "snapshot_source": snapshot_source,
        "snapshot_rows": int(len(snapshot)),
        "joined_rows": int(len(joined)),
        "snapshot_fingerprint": str(joined["snapshot_fingerprint"].iloc[0]) if not joined.empty else None,
        "old_missing_features": old_missing,
        "new_missing_features": new_missing,
        "old_alpha_prob": _series_stats(joined["old_alpha_win_prob_20d"]),
        "new_alpha_prob": _series_stats(joined["new_alpha_win_prob_20d"]),
        "old_prob_edge": _series_stats(joined["old_prob_edge"]),
        "new_prob_edge": _series_stats(joined["new_prob_edge"]),
        "prob_edge_delta": _series_stats(joined["prob_edge_delta"]),
        "prob_edge_loss": _series_stats(joined["prob_edge_loss"]),
        "gate_changed_count": int(joined["gate_changed"].sum()),
        "old_pass_new_fail_count": int(joined["old_pass_new_fail"].sum()),
        "old_fail_new_pass_count": int(joined["old_fail_new_pass"].sum()),
        "old_pass_count": int(joined["old_gate_pass"].sum()),
        "new_pass_count": int(joined["new_gate_pass"].sum()),
        "top30_old_pass_count": int(top30["old_gate_pass"].sum()) if not top30.empty else 0,
        "top30_new_pass_count": int(top30["new_gate_pass"].sum()) if not top30.empty else 0,
        "top30_prob_edge_loss": _series_stats(top30["prob_edge_loss"]) if not top30.empty else {},
        "largest_edge_loss": _records(
            joined,
            columns=[
                "date",
                "ticker",
                "pred_return_20d",
                "recommendation",
                "old_alpha_win_prob_20d",
                "new_alpha_win_prob_20d",
                "old_prob_edge",
                "new_prob_edge",
                "prob_edge_loss",
                "beta_60",
                "gap_pct",
                "foreign_cumsum_20d_raw",
                "roa_annualized",
                "operating_margin_latest",
            ],
            limit=20,
        ),
    }
    return joined, summary, snapshot


def _write_predictions_for_bundle(
    base_predictions: pd.DataFrame,
    detail: pd.DataFrame,
    snapshot: pd.DataFrame,
    bundle: ClassifierBundle,
    output_dir: Path,
) -> Path:
    merged = base_predictions.copy()
    cols = [
        "ticker",
        "date",
        f"{bundle.label}_alpha_win_prob_20d",
        f"{bundle.label}_alpha_win_prob_percentile",
        f"{bundle.label}_alpha_win_prob_rank",
        f"{bundle.label}_prob_edge",
    ]
    local = detail[cols].copy()
    rename = {
        f"{bundle.label}_alpha_win_prob_20d": "alpha_win_prob_20d",
        f"{bundle.label}_alpha_win_prob_percentile": "alpha_win_prob_percentile",
        f"{bundle.label}_alpha_win_prob_rank": "alpha_win_prob_rank",
        f"{bundle.label}_prob_edge": "alpha_prob_edge_20d",
    }
    local = local.rename(columns=rename)
    merged = merged.merge(local, on=["ticker", "date"], how="left")

    context = snapshot[["ticker", "Date", "Close"]].copy()
    context["ticker"] = context["ticker"].astype(str)
    context["date"] = pd.to_datetime(context["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    context["close"] = pd.to_numeric(context["Close"], errors="coerce")
    context = _attach_liquidity_artifacts(context[["ticker", "date", "close"]], snapshot)
    context = _attach_beta_artifacts(context)
    context = _attach_penalty_context_artifacts(context, snapshot)
    for col in ["price_vs_ma60", "sector"]:
        if col in snapshot.columns and col not in context.columns:
            values = snapshot[["ticker", "Date", col]].copy()
            values["ticker"] = values["ticker"].astype(str)
            values["date"] = pd.to_datetime(values["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
            values = values.drop(columns=["Date"])
            context = context.merge(values, on=["ticker", "date"], how="left")
    context_cols = [
        "ticker",
        "date",
        "close",
        "volume_today",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "beta_60",
        "price_vs_ma60",
        "sector",
        *PENALTY_CONTEXT_COLS,
    ]
    context_cols = [col for col in context_cols if col in context.columns]
    merged = merged.merge(context[context_cols], on=["ticker", "date"], how="left", suffixes=("", "_snapshot"))
    for column in context_cols:
        snapshot_col = f"{column}_snapshot"
        if snapshot_col in merged.columns:
            if column in merged.columns:
                merged[column] = merged[column].where(merged[column].notna(), merged[snapshot_col])
                merged = merged.drop(columns=[snapshot_col])
            else:
                merged = merged.rename(columns={snapshot_col: column})

    merged["alpha_classifier_model_file"] = bundle.model_file_name
    merged["alpha_classifier_threshold"] = bundle.threshold
    merged["alpha_classifier_target_mode"] = bundle.target_mode
    out_path = output_dir / f"predictions_alpha_cls_{bundle.label}_{base_predictions['date'].iloc[0]}.csv"
    merged.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def _build_signal_for_prediction(
    prediction_path: Path,
    prediction_date: str,
    bundle: ClassifierBundle,
    output_dir: Path,
) -> Path:
    import scripts.build_unified_signals as unified_module

    old_mode = unified_module.ALPHA_WIN_GATE_MODE
    old_min = unified_module.ALPHA_WIN_PROB_MIN
    try:
        unified_module.ALPHA_WIN_GATE_MODE = "absolute"
        unified_module.ALPHA_WIN_PROB_MIN = float(bundle.threshold)
        result = build_unified_signals(
            pred_date=prediction_date,
            prediction_20d_path=str(prediction_path),
            top_n_20d=30,
            output_prefix=f"unified_signals_alpha_cls_{bundle.label}",
            penalty_overlay=True,
            penalty_overlay_version="v1",
            disable_t1=True,
            verbose=False,
        )
    finally:
        unified_module.ALPHA_WIN_GATE_MODE = old_mode
        unified_module.ALPHA_WIN_PROB_MIN = old_min

    source = Path(result.output_path)
    target = output_dir / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return target


def _run_replay(signal_files: list[Path], output_dir: Path, exit_policy: str) -> dict[str, Any]:
    args = SimpleNamespace(
        start_date=min(_signal_date(path) for path in signal_files),
        end_date=max(_signal_date(path) for path in signal_files),
        signal_file=[str(path) for path in signal_files],
        output_dir=str(output_dir),
        db_path=None,
        keep_db=False,
        exit_policy=exit_policy,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_replay(args)


def _signal_date(path: Path) -> str:
    stem = path.stem
    return stem.rsplit("_", 1)[-1]


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V2 Alpha Classifier Fixed-Snapshot A/B + Replay",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Dates: `{', '.join(report['scope']['dates'])}`",
        f"- Exit policy: `{report['scope']['exit_policy']}`",
        f"- Snapshot compare CSV: `{report['artifacts']['detail_csv']}`",
        f"- Snapshot sources: `{', '.join(report['scope']['snapshot_sources'])}`",
        "",
        "## Classifier Bundles",
        "",
        "| bundle | target_mode | threshold | trained_at | model_file |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for key in ("old", "new"):
        bundle = report["bundles"][key]
        lines.append(
            f"| {key} | {bundle['target_mode']} | {_format_num(bundle['threshold'], 2)} | "
            f"{bundle['trained_at'] or '-'} | {bundle['model_file'] or '-'} |"
        )

    agg = report["compare"]["aggregate"]
    lines.extend(
        [
            "",
            "## Same-Snapshot A/B Aggregate",
            "",
            "| rows | old pass | new pass | gate changed | old->new fail | new->old pass | avg old edge | avg new edge | avg edge delta |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| {agg['rows']} | {agg['old_pass_count']} | {agg['new_pass_count']} | {agg['gate_changed_count']} | "
            f"{agg['old_pass_new_fail_count']} | {agg['old_fail_new_pass_count']} | "
            f"{_format_num((agg['old_prob_edge'] or {}).get('mean'))} | "
            f"{_format_num((agg['new_prob_edge'] or {}).get('mean'))} | "
            f"{_format_num((agg['prob_edge_delta'] or {}).get('mean'))} |",
            "",
            "## Replay Summary",
            "",
            "| branch | positions | filled | win rate | mtm return | twii | mtm alpha | avg slippage |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for branch in ("old", "new"):
        summary = report["replay"][branch]["summary"]
        lines.append(
            f"| {branch} ({report['bundles'][branch]['target_mode']}) | {summary['position_count']} | "
            f"{summary['filled_position_count']} | {_format_pct(summary['realized_win_rate_pct'])} | "
            f"{_format_pct(summary['mtm_capital_weighted_return_pct'])} | {_format_pct(summary['twii'].get('return_pct'))} | "
            f"{_format_pct(summary['mtm_alpha_vs_twii_pct'])} | {_format_pct(summary['avg_entry_slippage_pct'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Snapshot Summary",
            "",
            "| date | source | rows | old pass | new pass | top30 old pass | top30 new pass | avg edge loss | fingerprint |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for day in report["compare"]["days"]:
        lines.append(
            f"| {day['date']} | {day.get('snapshot_source') or '-'} | {day['joined_rows']} | {day['old_pass_count']} | {day['new_pass_count']} | "
            f"{day['top30_old_pass_count']} | {day['top30_new_pass_count']} | "
            f"{_format_num((day['prob_edge_loss'] or {}).get('mean'))} | {str(day.get('snapshot_fingerprint') or '')[:12]} |"
        )

    lines.extend(
        [
            "",
            "## Readout",
            "",
            f"- Old branch target mode: `{report['bundles']['old']['target_mode']}`",
            f"- New branch target mode: `{report['bundles']['new']['target_mode']}`",
            "- `prob_edge = alpha_win_prob_20d - alpha_classifier_threshold`.",
            "- Same-snapshot compare uses one shared point-in-time feature frame per date for both branches; cache-backed rows are preferred and DuckDB rebuild is the fallback.",
            "- Replay uses the same 20D base prediction files, `Penalty Overlay V1`, `disable_t1=True`, and the same exit policy for both branches.",
        ]
    )
    return "\n".join(lines) + "\n"


def _train_and_compare(
    *,
    dates: list[str],
    output_dir: Path,
    device: str,
    exit_policy: str,
) -> dict[str, Any]:
    old_bundle = _train_bundle("old", "vanilla", device)
    new_bundle = _train_bundle("new", "execution_adjusted", device)
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_frames: list[pd.DataFrame] = []
    day_summaries: list[dict[str, Any]] = []
    old_signal_files: list[Path] = []
    new_signal_files: list[Path] = []

    temp_predictions_dir = Path(tempfile.mkdtemp(prefix="alpha_classifier_ab_predictions_"))
    try:
        for as_of_date in dates:
            _safe_print(f"[alpha-ab] same-snapshot compare {as_of_date}")
            detail, summary, snapshot = _compare_day(as_of_date, old_bundle, new_bundle)
            detail_frames.append(detail)
            day_summaries.append(summary)

            base_predictions = _load_base_predictions(as_of_date)
            old_pred_path = _write_predictions_for_bundle(
                base_predictions,
                detail,
                snapshot,
                old_bundle,
                temp_predictions_dir,
            )
            new_pred_path = _write_predictions_for_bundle(
                base_predictions,
                detail,
                snapshot,
                new_bundle,
                temp_predictions_dir,
            )

            old_signal_files.append(
                _build_signal_for_prediction(
                    old_pred_path,
                    as_of_date,
                    old_bundle,
                    output_dir / "signals_old",
                )
            )
            new_signal_files.append(
                _build_signal_for_prediction(
                    new_pred_path,
                    as_of_date,
                    new_bundle,
                    output_dir / "signals_new",
                )
            )

        detail_df = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
        detail_csv = output_dir / "v2_alpha_classifier_snapshot_ab_detail.csv"
        detail_df.to_csv(detail_csv, index=False, encoding="utf-8-sig")

        aggregate = {
            "rows": int(len(detail_df)),
            "old_pass_count": int(detail_df["old_gate_pass"].sum()) if not detail_df.empty else 0,
            "new_pass_count": int(detail_df["new_gate_pass"].sum()) if not detail_df.empty else 0,
            "gate_changed_count": int(detail_df["gate_changed"].sum()) if not detail_df.empty else 0,
            "old_pass_new_fail_count": int(detail_df["old_pass_new_fail"].sum()) if not detail_df.empty else 0,
            "old_fail_new_pass_count": int(detail_df["old_fail_new_pass"].sum()) if not detail_df.empty else 0,
            "old_prob_edge": _series_stats(detail_df["old_prob_edge"]) if not detail_df.empty else {},
            "new_prob_edge": _series_stats(detail_df["new_prob_edge"]) if not detail_df.empty else {},
            "prob_edge_delta": _series_stats(detail_df["prob_edge_delta"]) if not detail_df.empty else {},
            "prob_edge_loss": _series_stats(detail_df["prob_edge_loss"]) if not detail_df.empty else {},
            "largest_edge_loss": _records(
                detail_df.sort_values("prob_edge_loss", ascending=False, na_position="last"),
                columns=[
                    "date",
                    "ticker",
                    "pred_return_20d",
                    "recommendation",
                    "old_alpha_win_prob_20d",
                    "new_alpha_win_prob_20d",
                    "old_prob_edge",
                    "new_prob_edge",
                    "prob_edge_loss",
                    "beta_60",
                    "gap_pct",
                    "foreign_cumsum_20d_raw",
                    "roa_annualized",
                    "operating_margin_latest",
                ],
                limit=30,
            ),
        }

        _safe_print("[alpha-ab] replay old/vanilla")
        replay_old = _run_replay(
            old_signal_files,
            output_dir=output_dir / "replay_old",
            exit_policy=exit_policy,
        )
        _safe_print("[alpha-ab] replay new/execution_adjusted")
        replay_new = _run_replay(
            new_signal_files,
            output_dir=output_dir / "replay_new",
            exit_policy=exit_policy,
        )

        report = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scope": {
                "dates": dates,
                "exit_policy": exit_policy,
                "method": (
                    "Train vanilla and execution-adjusted alpha classifiers, rebuild one point-in-time "
                    "feature snapshot per date from the same cached/DuckDB feature frame, compare alpha "
                    "probability edges on the same frame, then replay both branches with the same unified "
                    "20D entry pipeline."
                ),
                "snapshot_sources": sorted({str(day.get("snapshot_source") or "unknown") for day in day_summaries}),
            },
            "bundles": {
                "old": {
                    "label": old_bundle.label,
                    "target_mode": old_bundle.target_mode,
                    "threshold": old_bundle.threshold,
                    "trained_at": old_bundle.trained_at,
                    "model_file": old_bundle.model_file_name,
                },
                "new": {
                    "label": new_bundle.label,
                    "target_mode": new_bundle.target_mode,
                    "threshold": new_bundle.threshold,
                    "trained_at": new_bundle.trained_at,
                    "model_file": new_bundle.model_file_name,
                },
            },
            "artifacts": {
                "detail_csv": str(detail_csv),
                "old_replay_markdown": replay_old["markdown"],
                "new_replay_markdown": replay_new["markdown"],
            },
            "compare": {
                "days": day_summaries,
                "aggregate": aggregate,
            },
            "replay": {
                "old": replay_old,
                "new": replay_new,
            },
        }

        json_path = output_dir / "v2_alpha_classifier_snapshot_ab_latest.json"
        md_path = output_dir / "v2_alpha_classifier_snapshot_ab_latest.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        md_path.write_text(_render_markdown(report), encoding="utf-8")
        report["artifacts"]["json"] = str(json_path)
        report["artifacts"]["markdown"] = str(md_path)
        return report
    finally:
        shutil.rmtree(temp_predictions_dir, ignore_errors=True)
        _cleanup_bundle_artifacts(old_bundle)
        _cleanup_bundle_artifacts(new_bundle)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Run V2 alpha classifier fixed-snapshot A/B + replay.")
    parser.add_argument("--date", action="append", dest="dates", help="Prediction date YYYY-MM-DD. Repeatable.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    parser.add_argument("--exit-policy", default=DEFAULT_EXIT_POLICY)
    args = parser.parse_args(argv)

    report = _train_and_compare(
        dates=args.dates or DEFAULT_DATES,
        output_dir=Path(args.output_dir),
        device=args.device,
        exit_policy=args.exit_policy,
    )
    agg = report["compare"]["aggregate"]
    _safe_print(
        "[alpha-ab] "
        f"rows={agg['rows']} "
        f"old_pass={agg['old_pass_count']} new_pass={agg['new_pass_count']} "
        f"edge_delta_mean={_format_num((agg['prob_edge_delta'] or {}).get('mean'))} "
        f"old_alpha={_format_pct(report['replay']['old']['summary'].get('mtm_alpha_vs_twii_pct'))} "
        f"new_alpha={_format_pct(report['replay']['new']['summary'].get('mtm_alpha_vs_twii_pct'))}"
    )
    _safe_print(f"[alpha-ab] markdown={report['artifacts']['markdown']}")
    return report


if __name__ == "__main__":
    main()
