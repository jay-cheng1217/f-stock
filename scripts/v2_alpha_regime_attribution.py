"""Attribute V2 unified picks against TWII alpha, regime, and overheat risk.

The output is a research report. It does not change production signals or the
portfolio ledger. It reads the execution replay positions artifact, enriches it
with point-in-time market/stock snapshots, and compares counterfactual overlays.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR

DEFAULT_POSITIONS_CSV = Path(REPORT_DIR) / "unified_execution_replay_positions_latest.csv"
LATEST_JSON = "v2_alpha_regime_attribution_latest.json"
LATEST_MD = "v2_alpha_regime_attribution_latest.md"
LATEST_CSV = "v2_alpha_regime_attribution_latest.csv"

SIGNAL_FILE_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
TRADE_COST_HURDLE = 0.012
OVERHEAT_MA60_START = 0.20
OVERHEAT_RETURN20_START = 0.25
BETA_START = 1.20
MAX_PRICE_VS_MA60 = 0.40
MAX_RETURN_20D = 0.45
MAX_BETA_60 = 1.80
NET_SCORE_TOP_QUANTILE = 0.75


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build V2 alpha/regime/overheat attribution report."
    )
    parser.add_argument("--positions-csv", default=str(DEFAULT_POSITIONS_CSV))
    parser.add_argument("--output-dir", default=REPORT_DIR)
    return parser.parse_args(argv)


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _pct(value: Any, digits: int = 2) -> str:
    numeric = _to_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric * 100:.{digits}f}%"


def _num(value: Any, digits: int = 4) -> str:
    numeric = _to_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:.{digits}f}"


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _weighted_average(values: pd.Series, weights: pd.Series) -> float | None:
    frame = pd.DataFrame({"value": values, "weight": weights}).dropna()
    if frame.empty:
        return None
    frame = frame[np.isfinite(frame["value"]) & np.isfinite(frame["weight"]) & (frame["weight"] > 0)]
    if frame.empty:
        return None
    weight_sum = float(frame["weight"].sum())
    if weight_sum <= 0:
        return None
    return float((frame["value"] * frame["weight"]).sum() / weight_sum)


def _safe_mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def _safe_median(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.median())


def _load_positions(path: str | Path) -> pd.DataFrame:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Execution replay positions CSV not found: {csv_path}")
    df = pd.read_csv(csv_path, dtype={"ticker": str})
    if df.empty:
        raise ValueError(f"Execution replay positions CSV is empty: {csv_path}")
    for column in [
        "target_weight",
        "planned_entry_ref_price",
        "entry_price",
        "realized_return_pct",
        "max_drawdown_pct",
        "entry_slippage_pct",
        "latest_mark_close",
        "unrealized_return_pct",
        "effective_return_pct",
    ]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _signal_file_for_date(prediction_date: str) -> Path | None:
    path = Path(MODEL_DIR) / f"unified_signals_{prediction_date}.csv"
    if path.exists():
        return path
    return None


def _load_signal_context(positions: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(str(value) for value in positions["prediction_date"].dropna().unique())
    frames: list[pd.DataFrame] = []
    wanted = [
        "prediction_date",
        "ticker",
        "pred_return_20d",
        "risk_adjusted_return",
        "prob_edge",
        "up_prob",
        "flat_prob",
        "down_prob",
        "rank_20d",
        "rank_t1",
        "sector",
        "risk_tags_20d",
        "risk_tags_t1",
        "signal_type_before_tradability",
    ]
    for prediction_date in dates:
        path = _signal_file_for_date(prediction_date)
        if path is None:
            continue
        df = pd.read_csv(path, dtype={"ticker": str})
        available = [column for column in wanted if column in df.columns]
        if not {"prediction_date", "ticker"}.issubset(available):
            continue
        frames.append(df[available].copy())

    if not frames:
        return positions

    context = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["prediction_date", "ticker"],
        keep="last",
    )
    for column in [
        "pred_return_20d",
        "risk_adjusted_return",
        "prob_edge",
        "up_prob",
        "flat_prob",
        "down_prob",
        "rank_20d",
        "rank_t1",
    ]:
        if column in context.columns:
            context[column] = pd.to_numeric(context[column], errors="coerce")

    merge_cols = [column for column in context.columns if column not in positions.columns]
    merge_cols = ["prediction_date", "ticker", *merge_cols]
    return positions.merge(context[merge_cols], on=["prediction_date", "ticker"], how="left")


def _load_twii() -> pd.DataFrame:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    if not path.exists():
        raise FileNotFoundError(f"TWII index file not found: {path}")
    df = pd.read_csv(path)
    required = {"Date", "Open", "Close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ["Open", "Close", "High", "Low"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)
    df["twii_return_1d"] = df["Close"].pct_change()
    df["twii_ma20"] = df["Close"].rolling(20, min_periods=20).mean()
    df["twii_ma60"] = df["Close"].rolling(60, min_periods=60).mean()
    df["twii_return_20d"] = df["Close"] / df["Close"].shift(20) - 1.0
    return df


def _twii_snapshot(twii: pd.DataFrame, prediction_date: str) -> dict[str, Any]:
    date_value = pd.to_datetime(prediction_date)
    subset = twii[twii["Date"] <= date_value]
    if subset.empty:
        return {
            "regime_state": "UNKNOWN",
            "twii_close": None,
            "twii_ma20": None,
            "twii_ma60": None,
            "twii_return_20d": None,
        }
    row = subset.iloc[-1]
    close = _to_float(row.get("Close"))
    ma20 = _to_float(row.get("twii_ma20"))
    ma60 = _to_float(row.get("twii_ma60"))
    return_20d = _to_float(row.get("twii_return_20d"))

    if close is None or ma20 is None or ma60 is None:
        regime = "UNKNOWN"
    elif close > ma20 and ma20 >= ma60:
        regime = "OPEN"
    elif close > ma60:
        regime = "CAUTION"
    else:
        regime = "CLOSED"

    return {
        "regime_state": regime,
        "twii_close": close,
        "twii_ma20": ma20,
        "twii_ma60": ma60,
        "twii_return_20d": return_20d,
    }


def _twii_trade_return(twii: pd.DataFrame, entry_date: Any, end_date: Any) -> float | None:
    if pd.isna(entry_date) or pd.isna(end_date):
        return None
    start = pd.to_datetime(entry_date, errors="coerce")
    end = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(start) or pd.isna(end):
        return None
    start_rows = twii[twii["Date"] >= start].head(1)
    end_rows = twii[twii["Date"] <= end].tail(1)
    if start_rows.empty or end_rows.empty:
        return None
    start_open = _to_float(start_rows.iloc[0].get("Open"))
    end_close = _to_float(end_rows.iloc[0].get("Close"))
    if start_open is None or start_open <= 0 or end_close is None:
        return None
    return end_close / start_open - 1.0


def _load_stock(ticker: str) -> pd.DataFrame | None:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if "Date" not in df.columns or "Close" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ["Open", "High", "Low", "Close", "MA_20", "MA_60"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    if "MA_20" not in df.columns:
        df["MA_20"] = df["Close"].rolling(20, min_periods=20).mean()
    if "MA_60" not in df.columns:
        df["MA_60"] = df["Close"].rolling(60, min_periods=60).mean()
    df["stock_return_1d"] = df["Close"].pct_change()
    df["stock_return_20d"] = df["Close"] / df["Close"].shift(20) - 1.0
    df["volatility_20d"] = df["stock_return_1d"].rolling(20, min_periods=10).std()
    return df


def _stock_snapshot(
    stock_cache: dict[str, pd.DataFrame | None],
    twii: pd.DataFrame,
    ticker: str,
    prediction_date: str,
) -> dict[str, Any]:
    if ticker not in stock_cache:
        stock_cache[ticker] = _load_stock(ticker)
    stock = stock_cache[ticker]
    if stock is None or stock.empty:
        return {
            "stock_close": None,
            "price_vs_ma20": None,
            "price_vs_ma60": None,
            "stock_return_20d": None,
            "stock_volatility_20d": None,
            "beta_60": None,
        }

    date_value = pd.to_datetime(prediction_date)
    subset = stock[stock["Date"] <= date_value]
    if subset.empty:
        return {
            "stock_close": None,
            "price_vs_ma20": None,
            "price_vs_ma60": None,
            "stock_return_20d": None,
            "stock_volatility_20d": None,
            "beta_60": None,
        }

    row = subset.iloc[-1]
    close = _to_float(row.get("Close"))
    ma20 = _to_float(row.get("MA_20"))
    ma60 = _to_float(row.get("MA_60"))
    price_vs_ma20 = close / ma20 - 1.0 if close and ma20 and ma20 > 0 else None
    price_vs_ma60 = close / ma60 - 1.0 if close and ma60 and ma60 > 0 else None

    merged = stock[["Date", "stock_return_1d"]].merge(
        twii[["Date", "twii_return_1d"]],
        on="Date",
        how="inner",
    )
    beta_window = merged[merged["Date"] <= date_value].dropna().tail(60)
    beta = None
    if len(beta_window) >= 30:
        market_var = float(beta_window["twii_return_1d"].var())
        if market_var > 0:
            beta = float(beta_window["stock_return_1d"].cov(beta_window["twii_return_1d"]) / market_var)

    return {
        "stock_close": close,
        "price_vs_ma20": price_vs_ma20,
        "price_vs_ma60": price_vs_ma60,
        "stock_return_20d": _to_float(row.get("stock_return_20d")),
        "stock_volatility_20d": _to_float(row.get("volatility_20d")),
        "beta_60": beta,
    }


def _enrich_positions(positions: pd.DataFrame) -> pd.DataFrame:
    twii = _load_twii()
    stock_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    for _, row in positions.iterrows():
        prediction_date = str(row.get("prediction_date"))
        ticker = str(row.get("ticker"))
        twii_info = _twii_snapshot(twii, prediction_date)
        stock_info = _stock_snapshot(stock_cache, twii, ticker, prediction_date)
        end_date = row.get("exit_date")
        if pd.isna(end_date) or not str(end_date):
            end_date = row.get("latest_mark_date") or row.get("last_mark_date")
        twii_hold_return = _twii_trade_return(twii, row.get("entry_date"), end_date)
        rows.append(
            {
                "id": row.get("id"),
                **twii_info,
                **stock_info,
                "twii_holding_return_pct": twii_hold_return,
            }
        )

    enriched = positions.merge(pd.DataFrame(rows), on="id", how="left")
    enriched["effective_alpha_pct"] = (
        pd.to_numeric(enriched.get("effective_return_pct"), errors="coerce")
        - pd.to_numeric(enriched.get("twii_holding_return_pct"), errors="coerce")
    )

    score_basis = pd.to_numeric(enriched.get("risk_adjusted_return"), errors="coerce")
    fallback = pd.to_numeric(enriched.get("pred_return_20d"), errors="coerce")
    score_basis = score_basis.where(score_basis.notna(), fallback)
    expected_twii = pd.to_numeric(enriched.get("twii_return_20d"), errors="coerce").clip(lower=0.0)
    price_vs_ma60 = pd.to_numeric(enriched.get("price_vs_ma60"), errors="coerce")
    return_20d = pd.to_numeric(enriched.get("stock_return_20d"), errors="coerce")
    beta_60 = pd.to_numeric(enriched.get("beta_60"), errors="coerce")

    overheat_penalty = (price_vs_ma60 - OVERHEAT_MA60_START).clip(lower=0.0) * 0.50
    momentum_penalty = (return_20d - OVERHEAT_RETURN20_START).clip(lower=0.0) * 0.25
    beta_penalty = (beta_60 - BETA_START).clip(lower=0.0) * 0.02
    enriched["expected_twii_20d_pct"] = expected_twii
    enriched["overheat_penalty_pct"] = overheat_penalty.fillna(0.0)
    enriched["momentum_penalty_pct"] = momentum_penalty.fillna(0.0)
    enriched["beta_penalty_pct"] = beta_penalty.fillna(0.0)
    enriched["net_alpha_score"] = (
        score_basis
        - expected_twii.fillna(0.0)
        - TRADE_COST_HURDLE
        - enriched["overheat_penalty_pct"]
        - enriched["momentum_penalty_pct"]
        - enriched["beta_penalty_pct"]
    )
    enriched["strict_positive_net_alpha_keep"] = (
        enriched["entry_signal_type"].ne("T1_only")
        & enriched["regime_state"].isin(["OPEN", "CAUTION"])
        & enriched["net_alpha_score"].gt(0)
        & pd.to_numeric(enriched["price_vs_ma60"], errors="coerce").le(MAX_PRICE_VS_MA60)
        & pd.to_numeric(enriched["stock_return_20d"], errors="coerce").le(MAX_RETURN_20D)
        & pd.to_numeric(enriched["beta_60"], errors="coerce").le(MAX_BETA_60)
    )
    return enriched


def _evaluated(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["effective_return_pct"].notna()].copy()


def _summarize_subset(name: str, df: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    subset = df[mask].copy()
    evaluated = _evaluated(subset)
    stopped = subset[subset.get("status", pd.Series(dtype="object")).eq("stopped_out")]
    missed = subset[subset.get("status", pd.Series(dtype="object")).eq("missed_entry_gap_up")]
    alpha = pd.to_numeric(evaluated.get("effective_alpha_pct"), errors="coerce")
    returns = pd.to_numeric(evaluated.get("effective_return_pct"), errors="coerce")
    weights = pd.to_numeric(evaluated.get("target_weight"), errors="coerce")

    return {
        "name": name,
        "position_count": int(len(subset)),
        "evaluated_count": int(len(evaluated)),
        "open_count": int((subset.get("status") == "open").sum()) if "status" in subset else 0,
        "missed_count": int(len(missed)),
        "stopped_count": int(len(stopped)),
        "stopped_rate_pct": float(len(stopped) / len(evaluated)) if len(evaluated) else None,
        "capital_weighted_return_pct": _weighted_average(returns, weights),
        "capital_weighted_alpha_pct": _weighted_average(alpha, weights),
        "avg_return_pct": _safe_mean(returns),
        "median_return_pct": _safe_median(returns),
        "win_rate_pct": float((returns > 0).mean()) if len(returns.dropna()) else None,
        "alpha_win_rate_pct": float((alpha > 0).mean()) if len(alpha.dropna()) else None,
        "avg_twii_holding_return_pct": _safe_mean(
            pd.to_numeric(evaluated.get("twii_holding_return_pct"), errors="coerce")
        ),
        "avg_entry_slippage_pct": _safe_mean(
            pd.to_numeric(evaluated.get("entry_slippage_pct"), errors="coerce")
        ),
        "avg_price_vs_ma60_pct": _safe_mean(
            pd.to_numeric(evaluated.get("price_vs_ma60"), errors="coerce")
        ),
        "avg_beta_60": _safe_mean(pd.to_numeric(evaluated.get("beta_60"), errors="coerce")),
        "avg_net_alpha_score_pct": _safe_mean(
            pd.to_numeric(evaluated.get("net_alpha_score"), errors="coerce")
        ),
    }


def _bucket_summary(df: pd.DataFrame, bucket_col: str, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evaluated = _evaluated(df)
    if evaluated.empty or bucket_col not in evaluated.columns:
        return rows
    for bucket, group in evaluated.groupby(bucket_col, dropna=False, observed=False):
        weights = pd.to_numeric(group.get("target_weight"), errors="coerce")
        returns = pd.to_numeric(group.get("effective_return_pct"), errors="coerce")
        alpha = pd.to_numeric(group.get("effective_alpha_pct"), errors="coerce")
        rows.append(
            {
                "bucket_type": label,
                "bucket": str(bucket),
                "count": int(len(group)),
                "capital_weighted_return_pct": _weighted_average(returns, weights),
                "capital_weighted_alpha_pct": _weighted_average(alpha, weights),
                "avg_return_pct": _safe_mean(returns),
                "avg_alpha_pct": _safe_mean(alpha),
                "alpha_win_rate_pct": float((alpha > 0).mean()) if len(alpha.dropna()) else None,
                "stopped_count": int((group.get("status") == "stopped_out").sum()),
                "avg_entry_slippage_pct": _safe_mean(
                    pd.to_numeric(group.get("entry_slippage_pct"), errors="coerce")
                ),
            }
        )
    return rows


def _add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["price_vs_ma60_bucket"] = pd.cut(
        pd.to_numeric(result["price_vs_ma60"], errors="coerce"),
        bins=[-np.inf, 0.0, 0.20, 0.40, np.inf],
        labels=["<=0%", "0-20%", "20-40%", ">40%"],
    )
    result["beta_60_bucket"] = pd.cut(
        pd.to_numeric(result["beta_60"], errors="coerce"),
        bins=[-np.inf, 0.8, 1.2, 1.8, np.inf],
        labels=["<=0.8", "0.8-1.2", "1.2-1.8", ">1.8"],
    )
    result["net_alpha_score_bucket"] = pd.cut(
        pd.to_numeric(result["net_alpha_score"], errors="coerce"),
        bins=[-np.inf, 0.0, 0.03, 0.08, np.inf],
        labels=["<=0", "0-3%", "3-8%", ">8%"],
    )
    non_t1_regime = result["entry_signal_type"].ne("T1_only") & result["regime_state"].isin(
        ["OPEN", "CAUTION"]
    )
    eligible_scores = pd.to_numeric(
        result.loc[non_t1_regime, "net_alpha_score"],
        errors="coerce",
    ).dropna()
    threshold = float(eligible_scores.quantile(NET_SCORE_TOP_QUANTILE)) if not eligible_scores.empty else np.nan
    result["net_score_top_quartile_threshold"] = threshold
    result["proposed_overlay_keep"] = (
        non_t1_regime
        & pd.to_numeric(result["net_alpha_score"], errors="coerce").ge(threshold)
        & pd.notna(threshold)
    )
    return result


def _build_variant_summaries(df: pd.DataFrame) -> list[dict[str, Any]]:
    non_t1 = df["entry_signal_type"].ne("T1_only")
    evaluated = df["effective_return_pct"].notna()
    variants = [
        ("A_current_all", pd.Series(True, index=df.index)),
        ("B_20d_and_dual_only", non_t1),
        ("C_20d_regime_not_closed", non_t1 & df["regime_state"].isin(["OPEN", "CAUTION"])),
        ("D_net_score_top_quartile_overlay", df["proposed_overlay_keep"]),
        ("E_strict_positive_net_alpha_overlay", df["strict_positive_net_alpha_keep"]),
        (
            "F_ma60_20_to_40_band",
            non_t1
            & pd.to_numeric(df["price_vs_ma60"], errors="coerce").gt(0.20)
            & pd.to_numeric(df["price_vs_ma60"], errors="coerce").le(0.40),
        ),
        ("G_low_overheat_20d_subset", non_t1 & pd.to_numeric(df["price_vs_ma60"], errors="coerce").le(0.20)),
    ]
    summaries = [_summarize_subset(name, df, mask) for name, mask in variants]

    # A diagnostic oracle row is useful to quantify how much of the problem is
    # stock selection. It is explicitly not implementable because it uses future
    # same-window TWII alpha.
    summaries.append(
        _summarize_subset(
            "ORACLE_positive_actual_alpha_diagnostic",
            df,
            evaluated & pd.to_numeric(df["effective_alpha_pct"], errors="coerce").gt(0),
        )
    )
    return summaries


def _corr(df: pd.DataFrame, left: str, right: str) -> float | None:
    if left not in df.columns or right not in df.columns:
        return None
    clean = df[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(clean) < 3:
        return None
    value = float(clean[left].corr(clean[right]))
    return value if math.isfinite(value) else None


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], limit: int | None = None) -> list[str]:
    output = [f"| {' | '.join(header for header, _ in columns)} |"]
    output.append(f"| {' | '.join(['---'] * len(columns))} |")
    limited = rows if limit is None else rows[:limit]
    if not limited:
        output.append(f"| {' | '.join(['n/a'] * len(columns))} |")
        return output
    for row in limited:
        values = []
        for _, key in columns:
            value = row.get(key)
            if key.endswith("_pct") or key in {
                "capital_weighted_return_pct",
                "capital_weighted_alpha_pct",
                "avg_return_pct",
                "avg_alpha_pct",
                "alpha_win_rate_pct",
                "avg_entry_slippage_pct",
                "avg_price_vs_ma60_pct",
                "avg_net_alpha_score_pct",
                "win_rate_pct",
                "stopped_rate_pct",
                "avg_twii_holding_return_pct",
                "median_return_pct",
            }:
                values.append(_pct(value))
            elif isinstance(value, float):
                values.append(_num(value))
            else:
                values.append("" if value is None else str(value))
        output.append(f"| {' | '.join(values)} |")
    return output


def _row_table(df: pd.DataFrame, columns: list[str], limit: int = 12) -> list[str]:
    rows = [f"| {' | '.join(columns)} |", f"| {' | '.join(['---'] * len(columns))} |"]
    if df.empty:
        rows.append(f"| {' | '.join(['n/a'] * len(columns))} |")
        return rows
    for _, row in df.head(limit).iterrows():
        values = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, float):
                values.append(_pct(value) if column.endswith("_pct") or "return" in column or "alpha" in column or "price_vs" in column else _num(value))
            elif pd.isna(value):
                values.append("")
            else:
                values.append(str(value))
        rows.append(f"| {' | '.join(values)} |")
    return rows


def _write_markdown(
    path: Path,
    summary: dict[str, Any],
    variants: list[dict[str, Any]],
    buckets: list[dict[str, Any]],
    enriched: pd.DataFrame,
) -> None:
    worst_alpha = _evaluated(enriched).sort_values("effective_alpha_pct", na_position="last")
    best_alpha = _evaluated(enriched).sort_values("effective_alpha_pct", ascending=False, na_position="last")
    proposed = enriched[enriched["proposed_overlay_keep"]].copy()
    blocked = enriched[
        enriched["entry_signal_type"].ne("T1_only")
        & enriched["effective_return_pct"].notna()
        & ~enriched["proposed_overlay_keep"]
    ].sort_values("effective_alpha_pct", na_position="last")

    lines = [
        "# V2 Alpha / Regime Attribution Latest",
        "",
        "This is a research attribution report. It does not change production signals.",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Positions source: {summary['positions_source']}",
        f"- Position count: {summary['position_count']}",
        f"- Evaluated positions: {summary['evaluated_count']}",
        f"- Trade cost hurdle used in net alpha score: {_pct(TRADE_COST_HURDLE)}",
        f"- Net score top-quartile threshold: {_pct(summary['net_score_top_quartile_threshold'])}",
        "",
        "## Variant Comparison",
        "",
        *_table(
            variants,
            [
                ("variant", "name"),
                ("positions", "position_count"),
                ("eval", "evaluated_count"),
                ("ret", "capital_weighted_return_pct"),
                ("alpha", "capital_weighted_alpha_pct"),
                ("alpha win", "alpha_win_rate_pct"),
                ("stop rate", "stopped_rate_pct"),
                ("slip", "avg_entry_slippage_pct"),
                ("p/ma60", "avg_price_vs_ma60_pct"),
                ("beta", "avg_beta_60"),
                ("net score", "avg_net_alpha_score_pct"),
            ],
        ),
        "",
        "## Correlations",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| pred_return_20d vs actual alpha | {_num(summary['corr_pred_return_vs_alpha'])} |",
        f"| risk_adjusted_return vs actual alpha | {_num(summary['corr_risk_adjusted_vs_alpha'])} |",
        f"| net_alpha_score vs actual alpha | {_num(summary['corr_net_alpha_score_vs_alpha'])} |",
        f"| price_vs_ma60 vs actual alpha | {_num(summary['corr_price_vs_ma60_vs_alpha'])} |",
        f"| beta_60 vs actual alpha | {_num(summary['corr_beta_vs_alpha'])} |",
        "",
        "## Bucket Attribution",
        "",
        *_table(
            buckets,
            [
                ("type", "bucket_type"),
                ("bucket", "bucket"),
                ("count", "count"),
                ("ret", "capital_weighted_return_pct"),
                ("alpha", "capital_weighted_alpha_pct"),
                ("alpha win", "alpha_win_rate_pct"),
                ("stops", "stopped_count"),
                ("slip", "avg_entry_slippage_pct"),
            ],
        ),
        "",
        "## Proposed Overlay Keeps",
        "",
        *_row_table(
            proposed.sort_values("net_alpha_score", ascending=False),
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "regime_state",
                "net_alpha_score",
                "effective_return_pct",
                "effective_alpha_pct",
                "price_vs_ma60",
                "beta_60",
            ],
        ),
        "",
        "## Worst Blocked By Proposed Overlay",
        "",
        *_row_table(
            blocked,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "regime_state",
                "net_alpha_score",
                "effective_return_pct",
                "effective_alpha_pct",
                "price_vs_ma60",
                "beta_60",
            ],
        ),
        "",
        "## Worst Actual Alpha",
        "",
        *_row_table(
            worst_alpha,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "status",
                "exit_reason",
                "effective_return_pct",
                "twii_holding_return_pct",
                "effective_alpha_pct",
                "price_vs_ma60",
                "beta_60",
            ],
        ),
        "",
        "## Best Actual Alpha",
        "",
        *_row_table(
            best_alpha,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "status",
                "exit_reason",
                "effective_return_pct",
                "twii_holding_return_pct",
                "effective_alpha_pct",
                "price_vs_ma60",
                "beta_60",
            ],
        ),
        "",
        "## Interpretation Notes",
        "",
        "- `A_current_all` is the execution replay population, including older T+1 artifacts still present on disk.",
        "- `D_net_score_top_quartile_overlay` is a research ranking overlay, not a production gate.",
        "- `E_strict_positive_net_alpha_overlay` shows absolute net-alpha calibration is currently too strict if it has no rows.",
        "- The oracle row uses future realized alpha and is only a diagnostic upper bound.",
        "- TWII holding return is approximated as TWII entry-date open to end-date close.",
        "- A better variant with fewer positions still needs a continuous NAV replay before promotion.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    positions_path = Path(args.positions_csv)
    positions = _load_positions(positions_path)
    positions = _load_signal_context(positions)
    enriched = _add_buckets(_enrich_positions(positions))

    evaluated = _evaluated(enriched)
    variants = _build_variant_summaries(enriched)
    buckets = []
    buckets.extend(_bucket_summary(enriched, "regime_state", "regime"))
    buckets.extend(_bucket_summary(enriched, "price_vs_ma60_bucket", "price_vs_ma60"))
    buckets.extend(_bucket_summary(enriched, "beta_60_bucket", "beta_60"))
    buckets.extend(_bucket_summary(enriched, "net_alpha_score_bucket", "net_alpha_score"))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "positions_source": str(positions_path),
        "position_count": int(len(enriched)),
        "evaluated_count": int(len(evaluated)),
        "corr_pred_return_vs_alpha": _corr(evaluated, "pred_return_20d", "effective_alpha_pct"),
        "corr_risk_adjusted_vs_alpha": _corr(evaluated, "risk_adjusted_return", "effective_alpha_pct"),
        "corr_net_alpha_score_vs_alpha": _corr(evaluated, "net_alpha_score", "effective_alpha_pct"),
        "corr_price_vs_ma60_vs_alpha": _corr(evaluated, "price_vs_ma60", "effective_alpha_pct"),
        "corr_beta_vs_alpha": _corr(evaluated, "beta_60", "effective_alpha_pct"),
        "thresholds": {
            "trade_cost_hurdle": TRADE_COST_HURDLE,
            "overheat_ma60_start": OVERHEAT_MA60_START,
            "overheat_return20_start": OVERHEAT_RETURN20_START,
            "beta_start": BETA_START,
            "max_price_vs_ma60": MAX_PRICE_VS_MA60,
            "max_return_20d": MAX_RETURN_20D,
            "max_beta_60": MAX_BETA_60,
            "net_score_top_quantile": NET_SCORE_TOP_QUANTILE,
        },
        "net_score_top_quartile_threshold": (
            _to_float(enriched["net_score_top_quartile_threshold"].dropna().iloc[0])
            if enriched["net_score_top_quartile_threshold"].notna().any()
            else None
        ),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / LATEST_CSV
    json_path = output_dir / LATEST_JSON
    md_path = output_dir / LATEST_MD

    enriched.to_csv(csv_path, index=False, encoding="utf-8-sig")
    payload = {
        "summary": summary,
        "variants": variants,
        "buckets": buckets,
        "csv": str(csv_path),
    }
    json_path.write_text(
        json.dumps(_clean_json(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(md_path, summary, variants, buckets, enriched)

    return {
        "summary": summary,
        "variants": variants,
        "buckets": buckets,
        "csv": str(csv_path),
        "json": str(json_path),
        "markdown": str(md_path),
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    result = build_report(args)
    variant_map = {item["name"]: item for item in result["variants"]}
    current = variant_map.get("A_current_all", {})
    proposed = variant_map.get("D_net_score_top_quartile_overlay", {})
    print(
        "[v2-alpha] "
        f"positions={result['summary']['position_count']} "
        f"eval={result['summary']['evaluated_count']} "
        f"current_alpha={_pct(current.get('capital_weighted_alpha_pct'))} "
        f"proposed_alpha={_pct(proposed.get('capital_weighted_alpha_pct'))} "
        f"proposed_count={proposed.get('evaluated_count', 0)}"
    )
    print(f"[v2-alpha] report={result['markdown']}")
    print(f"[v2-alpha] csv={result['csv']}")
    return result


if __name__ == "__main__":
    main()
