"""Cross-model confirmation: merge T+1 and 20D signals.

When both the short-term (T+1) and medium-term (20D) models agree on a
stock, the combined signal is stronger than either alone.  This module
provides lightweight utilities to compute dual-timeframe agreement.
"""

from __future__ import annotations

import glob
import os
from typing import Any

import numpy as np
import pandas as pd

from ml.config import MODEL_DIR


def _latest_csv(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(MODEL_DIR, pattern)))
    return files[-1] if files else None


def load_cross_confirmed(
    t1_df: pd.DataFrame | None = None,
    d20_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge T+1 and 20D predictions, compute agreement signals.

    Returns a DataFrame with one row per ticker, containing columns from
    both models plus cross-confirmation metrics.
    """
    if t1_df is None:
        t1_path = _latest_csv("predictions_t1_*.csv")
        if t1_path is None:
            return pd.DataFrame()
        t1_df = pd.read_csv(t1_path, dtype={"ticker": str})

    if d20_df is None:
        d20_path = _latest_csv("predictions_*.csv")
        if d20_path is None:
            return pd.DataFrame()
        d20_df = pd.read_csv(d20_path, dtype={"ticker": str})
        # Exclude T+1 files
        if d20_path and "predictions_t1_" in os.path.basename(d20_path):
            candidates = [
                f for f in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
                if "predictions_t1_" not in os.path.basename(f)
            ]
            if not candidates:
                return pd.DataFrame()
            d20_df = pd.read_csv(candidates[-1], dtype={"ticker": str})

    # Rename columns to avoid collisions
    t1_cols = {
        "hit_prob_3pct": "t1_prob",
        "t1_score": "t1_score",
        "recommendation": "t1_recommendation",
        "selected_for_trade": "t1_selected",
        "selection_rank": "t1_rank",
        "setup_tags": "t1_setup_tags",
        "risk_tags": "t1_risk_tags",
    }
    d20_cols = {
        "pred_return_20d": "d20_pred_return",
        "recommendation": "d20_recommendation",
        "signal": "d20_signal",
        "risk_tags": "d20_risk_tags",
    }

    t1_keep = ["ticker"] + [c for c in t1_cols if c in t1_df.columns]
    t1 = t1_df[t1_keep].rename(columns=t1_cols).copy()

    d20_keep = ["ticker"] + [c for c in d20_cols if c in d20_df.columns]
    d20 = d20_df[d20_keep].rename(columns=d20_cols).copy()

    merged = pd.merge(t1, d20, on="ticker", how="inner")
    if merged.empty:
        return merged

    # --- Cross-confirmation signals ---

    # T+1 bullish: use selection status or top probability
    t1_selected = merged["t1_selected"].fillna(False).astype(bool) if "t1_selected" in merged.columns else pd.Series(False, index=merged.index)
    t1_bullish = t1_selected  # use the model's own selection as bullish signal

    # 20D bullish: recommendation contains buy
    d20_bullish = pd.Series(False, index=merged.index)
    if "d20_recommendation" in merged.columns:
        d20_bullish = merged["d20_recommendation"].fillna("").str.contains("買進", na=False)
    elif "d20_signal" in merged.columns:
        d20_bullish = merged["d20_signal"].fillna("") == "UP"

    # 20D bearish
    d20_bearish = pd.Series(False, index=merged.index)
    if "d20_recommendation" in merged.columns:
        d20_bearish = merged["d20_recommendation"].fillna("").str.contains("賣出|觀望", na=False)

    # Agreement levels
    merged["cross_both_buy"] = t1_bullish & d20_bullish
    merged["cross_conflict"] = (t1_bullish & d20_bearish) | (~t1_bullish & d20_bullish)

    # Combined confidence score (simple weighted average)
    t1_norm = merged["t1_prob"].fillna(0.5) if "t1_prob" in merged.columns else 0.5
    d20_norm = pd.Series(0.5, index=merged.index)
    if "d20_pred_return" in merged.columns:
        # Normalize 20D predicted return to [0, 1] range
        ret = merged["d20_pred_return"].fillna(0)
        d20_norm = (ret.clip(-0.10, 0.10) / 0.10 + 1) / 2  # maps [-10%, +10%] to [0, 1]

    merged["cross_score"] = (0.5 * t1_norm + 0.5 * d20_norm).round(4)

    # Agreement label
    def _label(row):
        if row.get("cross_both_buy"):
            return "雙重確認買進"
        if row.get("cross_conflict"):
            return "訊號矛盾"
        return "中性"

    merged["cross_label"] = merged.apply(_label, axis=1)

    # Sort by cross_score descending
    merged = merged.sort_values("cross_score", ascending=False).reset_index(drop=True)

    return merged


def get_dual_confirmed_tickers(top_n: int = 10) -> list[dict[str, Any]]:
    """Return top N tickers confirmed by both models (for API/email use)."""
    merged = load_cross_confirmed()
    if merged.empty:
        return []

    confirmed = merged[merged["cross_both_buy"]].head(top_n)
    results = []
    for _, row in confirmed.iterrows():
        results.append({
            "ticker": row["ticker"],
            "t1_prob": round(float(row.get("t1_prob", 0)), 4),
            "t1_rank": int(row["t1_rank"]) if pd.notna(row.get("t1_rank")) else None,
            "d20_pred_return": round(float(row.get("d20_pred_return", 0)), 4) if pd.notna(row.get("d20_pred_return")) else None,
            "d20_recommendation": str(row.get("d20_recommendation", "")),
            "cross_score": round(float(row.get("cross_score", 0)), 4),
            "cross_label": str(row.get("cross_label", "")),
            "t1_setup_tags": str(row.get("t1_setup_tags", "")),
        })
    return results
