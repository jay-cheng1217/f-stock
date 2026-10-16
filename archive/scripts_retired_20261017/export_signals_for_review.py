"""One-off export: unified_signals + forward N-day returns for external review.

Produces a single CSV that contains, for every daily Top-N pick in
ml/models/unified_signals_YYYY-MM-DD.csv:
  - prediction-time context (rank, scores, penalties, gates)
  - forward 1/3/5/10/20-day close-to-close returns
  - 10d / 20d max gain & max loss during the holding window

The script reads CSV files and per-ticker daily K (日K資料/) only.
It does NOT modify production state.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
SIGNAL_DIR = BASE / "ml" / "models"
DAILY_K_DIR = BASE / "日K資料"
OUT_PATH = BASE / "exports" / "signals_with_forward_returns.csv"
SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")

KEEP_COLS = [
    "prediction_date",
    "ticker",
    "sector",
    "group_name",
    "signal_type",
    "target_weight_ratio",
    "rank_20d",
    "two_stage_rank",
    "rank_t1",
    "pred_return_20d",
    "risk_adjusted_return",
    "risk_adjusted_return_pre_penalty",
    "leaderboard_score_pre_penalty",
    "penalty_overlay_score",
    "penalty_overlay_total",
    "penalty_foreign_selling",
    "penalty_gap_risk",
    "penalty_quality",
    "penalty_beta",
    "penalty_hard_block",
    "penalty_overlay_reason",
    "guardrail_blocked",
    "guardrail_trigger_reason",
    "alpha_win_prob_20d",
    "prob_edge",
    "up_prob",
    "down_prob",
    "hit_prob_3pct",
    "close_ref",
    "volume_today",
    "avg_20d_volume",
    "price_vs_ma5",
    "price_vs_ma20",
    "price_vs_ma60",
    "beta_60",
    "pe_ratio",
    "market_regime_state",
    "market_regime_action",
    "tradability_blocked",
    "tradability_reason",
    "is_disposition",
    "is_attention",
    "is_full_delivery",
    "is_suspended",
    "production_gate_status",
    "production_gate_reason",
]


def load_signals() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(SIGNAL_DIR.glob("unified_signals_*.csv")):
        m = SIGNAL_RE.match(path.name)
        if not m:
            continue
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
        existing = [c for c in KEEP_COLS if c in df.columns]
        frames.append(df[existing])
    if not frames:
        raise SystemExit("No unified_signals files found.")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["prediction_date"] = pd.to_datetime(out["prediction_date"]).dt.date
    return out


def daily_k_close(ticker: str) -> pd.DataFrame | None:
    path = DAILY_K_DIR / f"{ticker}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, usecols=["Date", "Open", "High", "Low", "Close"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    return df


def forward_metrics(k: pd.DataFrame, pred_date) -> dict[str, float | None]:
    idx = k.index[k["Date"] == pred_date]
    if len(idx) == 0:
        return {}
    i = int(idx[0])
    base = float(k.at[i, "Close"])
    out: dict[str, float | None] = {}
    for horizon in (1, 3, 5, 10, 20):
        j = i + horizon
        if j < len(k):
            out[f"return_{horizon}d"] = float(k.at[j, "Close"]) / base - 1.0
        else:
            out[f"return_{horizon}d"] = None
    for horizon in (10, 20):
        j_end = min(i + horizon, len(k) - 1)
        if j_end <= i:
            out[f"max_gain_{horizon}d"] = None
            out[f"max_loss_{horizon}d"] = None
            continue
        window = k.iloc[i + 1 : j_end + 1]
        if window.empty:
            out[f"max_gain_{horizon}d"] = None
            out[f"max_loss_{horizon}d"] = None
        else:
            out[f"max_gain_{horizon}d"] = float(window["High"].max()) / base - 1.0
            out[f"max_loss_{horizon}d"] = float(window["Low"].min()) / base - 1.0
    return out


def main() -> int:
    signals = load_signals()
    signals = signals.sort_values(["prediction_date", "ticker"]).reset_index(drop=True)

    ticker_cache: dict[str, pd.DataFrame | None] = {}
    records: list[dict] = []
    for row in signals.itertuples(index=False):
        ticker = str(getattr(row, "ticker"))
        pred_date = getattr(row, "prediction_date")
        if ticker not in ticker_cache:
            ticker_cache[ticker] = daily_k_close(ticker)
        k = ticker_cache[ticker]
        rec = row._asdict()
        if k is None:
            for h in (1, 3, 5, 10, 20):
                rec[f"return_{h}d"] = None
            for h in (10, 20):
                rec[f"max_gain_{h}d"] = None
                rec[f"max_loss_{h}d"] = None
        else:
            rec.update(forward_metrics(k, pred_date))
        records.append(rec)

    out = pd.DataFrame.from_records(records)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"wrote rows={len(out)} path={OUT_PATH}")
    print(f"date range: {out['prediction_date'].min()} -> {out['prediction_date'].max()}")
    print(f"tickers: {out['ticker'].nunique()}")
    print(f"with return_5d: {out['return_5d'].notna().sum()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
