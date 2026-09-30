"""Legacy cross-confirm helpers backed by unified daily signal artifacts."""

from __future__ import annotations

import glob
import os
import re
from typing import Any

import pandas as pd

from ml.config import MODEL_DIR

UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
SIGNAL_TYPE_DUAL = "Dual"


def _latest_unified_signal_path() -> str | None:
    candidates = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "unified_signals_*.csv")))
        if UNIFIED_SIGNAL_RE.match(os.path.basename(path))
    ]
    return candidates[-1] if candidates else None


def _load_latest_unified_df() -> pd.DataFrame:
    signal_path = _latest_unified_signal_path()
    if signal_path is None:
        return pd.DataFrame()

    return pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})


def _prepare_legacy_cross_confirm_df(unified_df: pd.DataFrame) -> pd.DataFrame:
    if unified_df.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "close_ref",
                "signal_type",
                "t1_prob",
                "t1_rank",
                "d20_pred_return",
                "d20_recommendation",
                "t1_setup_tags",
                "cross_score",
                "cross_both_buy",
                "cross_conflict",
                "cross_label",
            ]
        )

    df = unified_df.copy()
    for column in ["hit_prob_3pct", "rank_t1", "pred_return_20d", "risk_adjusted_return", "target_units"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    df["signal_type"] = df.get("signal_type", "").fillna("")
    df["close_ref"] = pd.to_numeric(df.get("close_ref"), errors="coerce")
    df["t1_prob"] = pd.to_numeric(df.get("hit_prob_3pct"), errors="coerce")
    df["t1_rank"] = pd.to_numeric(df.get("rank_t1"), errors="coerce")
    df["d20_pred_return"] = pd.to_numeric(df.get("pred_return_20d"), errors="coerce")
    df["d20_recommendation"] = df.get("recommendation_20d", "").fillna("")
    df["t1_setup_tags"] = df.get("setup_tags_t1", "").fillna("")
    df["cross_score"] = pd.to_numeric(df.get("risk_adjusted_return"), errors="coerce")
    df["cross_both_buy"] = df["signal_type"].eq(SIGNAL_TYPE_DUAL)
    df["cross_conflict"] = False
    df["cross_label"] = df["signal_type"].map(
        lambda value: "Dual" if value == SIGNAL_TYPE_DUAL else (str(value) if value else "")
    )

    sort_columns: list[str] = []
    ascending: list[bool] = []
    for column, direction in [
        ("cross_both_buy", False),
        ("target_units", False),
        ("cross_score", False),
        ("t1_prob", False),
        ("t1_rank", True),
        ("ticker", True),
    ]:
        if column in df.columns:
            sort_columns.append(column)
            ascending.append(direction)
    if sort_columns:
        df = df.sort_values(sort_columns, ascending=ascending, na_position="last").reset_index(drop=True)

    return df


def load_cross_confirmed(
    t1_df: pd.DataFrame | None = None,
    d20_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Expose unified signals through the legacy cross-confirm interface.

    The old API contract is intentionally preserved for zero-downtime migration.
    `t1_df` and `d20_df` are retained only for backward compatibility; when a
    DataFrame containing `signal_type` is provided, it is treated as the unified
    artifact directly.
    """

    candidate_df = t1_df if t1_df is not None and "signal_type" in t1_df.columns else None
    if candidate_df is None and d20_df is not None and "signal_type" in d20_df.columns:
        candidate_df = d20_df
    if candidate_df is None:
        candidate_df = _load_latest_unified_df()

    return _prepare_legacy_cross_confirm_df(candidate_df)


def get_dual_confirmed_tickers(top_n: int = 10) -> list[dict[str, Any]]:
    """Return top N Dual names for legacy email/API consumers."""

    merged = load_cross_confirmed()
    if merged.empty:
        return []

    confirmed = merged[merged["cross_both_buy"]].head(top_n)
    results = []
    for _, row in confirmed.iterrows():
        results.append(
            {
                "ticker": str(row.get("ticker", "")),
                "close_ref": round(float(row.get("close_ref", 0)), 2) if pd.notna(row.get("close_ref")) else None,
                "t1_prob": round(float(row.get("t1_prob", 0)), 4) if pd.notna(row.get("t1_prob")) else None,
                "t1_rank": int(row["t1_rank"]) if pd.notna(row.get("t1_rank")) else None,
                "d20_pred_return": (
                    round(float(row.get("d20_pred_return", 0)), 4)
                    if pd.notna(row.get("d20_pred_return"))
                    else None
                ),
                "d20_recommendation": str(row.get("d20_recommendation", "")),
                "cross_score": (
                    round(float(row.get("cross_score", 0)), 4)
                    if pd.notna(row.get("cross_score"))
                    else None
                ),
                "cross_label": str(row.get("cross_label", "")),
                "t1_setup_tags": str(row.get("t1_setup_tags", "")),
            }
        )
    return results
