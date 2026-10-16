"""Detailed REQ-009 regime-vs-production overlap audit for selected months.

Research-only. It rebuilds the requested monthly regime folds, scores the full
month-end snapshot, and compares the regime Top30 with saved production daily
prediction artifacts on the same date.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR, REGIME_FEATURE_COLS  # noqa: E402
from ml.dataset import build_dataset, get_feature_columns  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402
from scripts.train_v2 import V2_EARLY_STOPPING, V2_EMBARGO_TRADING_DAYS, V2_NUM_ROUNDS, V2_PARAMS, V2_TRAIN_MONTHS  # noqa: E402


@dataclass(frozen=True)
class FoldResult:
    month: str
    prediction_date: str
    full: pd.DataFrame
    top30: pd.DataFrame


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def _load_sector_map() -> dict[str, str]:
    path = BASE_DIR / "ml" / "data" / "sector_mapping.csv"
    df = pd.read_csv(path, dtype={"Ticker": str})
    return dict(zip(df["Ticker"].astype(str), df["Sector"].fillna("").astype(str)))


def _load_twii_returns() -> pd.DataFrame:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df["twii_return"] = pd.to_numeric(df["Close"], errors="coerce").pct_change()
    return df[["Date", "twii_return"]].dropna()


def _load_stock_returns(ticker: str) -> pd.DataFrame | None:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["stock_return"] = pd.to_numeric(df["Close"], errors="coerce").pct_change()
    return df[["Date", "stock_return"]].dropna().sort_values("Date")


def _beta_60(ticker: str, as_of_date: str, twii: pd.DataFrame, cache: dict[str, pd.DataFrame | None]) -> float | None:
    if ticker not in cache:
        cache[ticker] = _load_stock_returns(ticker)
    stock = cache[ticker]
    if stock is None or stock.empty:
        return None
    cutoff = pd.Timestamp(as_of_date)
    joined = stock.merge(twii, on="Date", how="inner")
    joined = joined[joined["Date"] <= cutoff].tail(60)
    if len(joined) < 20:
        return None
    variance = float(joined["twii_return"].var())
    if variance <= 0 or not math.isfinite(variance):
        return None
    covariance = float(joined["stock_return"].cov(joined["twii_return"]))
    beta = covariance / variance
    return beta if math.isfinite(beta) else None


def _size_bucket(amount: Any, q50: float, q80: float) -> str:
    number = _safe_float(amount)
    if number is None:
        return "unknown"
    if number >= q80:
        return "large_liquidity_proxy"
    if number >= q50:
        return "mid_liquidity_proxy"
    return "small_liquidity_proxy"


def _train_fold(dataset: pd.DataFrame, feature_cols: list[str], month: str) -> FoldResult:
    target_col = "trade_excess_return_20d"
    test_start = pd.Timestamp(f"{month}-01")
    test_end = test_start + pd.offsets.MonthEnd(0)
    train_start = test_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())

    test = dataset[(dataset["Date"] >= test_start) & (dataset["Date"] <= test_end)]
    if test.empty:
        raise ValueError(f"No test rows for {month}")
    first_test_date = pd.Timestamp(test["Date"].min())
    test_start_idx = unique_dates.get_indexer([first_test_date])[0]
    if test_start_idx < V2_EMBARGO_TRADING_DAYS:
        raise ValueError(f"Not enough embargo history for {month}")
    embargo_cutoff = unique_dates[test_start_idx - V2_EMBARGO_TRADING_DAYS]
    train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < embargo_cutoff)]
    if len(train) < 10000:
        raise ValueError(f"Too few train rows for {month}: {len(train)}")

    params = V2_PARAMS.copy()
    params.pop("device", None)
    params["n_jobs"] = -1
    train_data = lgb.Dataset(train[feature_cols].values, label=train[target_col].values)
    val_data = lgb.Dataset(test[feature_cols].values, label=test[target_col].values, reference=train_data)
    model = lgb.train(
        params,
        train_data,
        num_boost_round=V2_NUM_ROUNDS,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(V2_EARLY_STOPPING, verbose=False)],
    )

    scored = test[
        [
            "ticker",
            "Date",
            "Close",
            "Volume",
            "AMOUNT_MA_20",
            "price_vs_ma60",
            "trade_return_20d",
            "trade_excess_return_20d",
            *[c for c in REGIME_FEATURE_COLS if c in test.columns],
        ]
    ].copy()
    scored["regime_pred_return"] = model.predict(test[feature_cols].values)
    snapshot_date = pd.Timestamp(scored["Date"].max()).date().isoformat()
    snapshot = scored[scored["Date"] == scored["Date"].max()].copy()
    snapshot = snapshot.sort_values("regime_pred_return", ascending=False).reset_index(drop=True)
    snapshot["regime_rank"] = np.arange(1, len(snapshot) + 1)
    return FoldResult(month=month, prediction_date=snapshot_date, full=snapshot, top30=snapshot.head(30).copy())


def _production_snapshot(prediction_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = Path(MODEL_DIR) / f"predictions_{prediction_date}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    prod = pd.read_csv(path, dtype={"ticker": str})
    prod = prod.sort_values("leaderboard_score", ascending=False).reset_index(drop=True)
    prod["production_raw_rank"] = np.arange(1, len(prod) + 1)
    selected = apply_sector_cap(prod, top_n=30).head(30).copy()
    selected["production_rank"] = np.arange(1, len(selected) + 1)
    return prod, selected


def run(months: list[str], output_prefix: str) -> dict[str, str]:
    dataset = build_dataset(verbose=False).dropna(subset=["trade_excess_return_20d"]).copy()
    feature_cols = [c for c in get_feature_columns() if c in dataset.columns]
    sector_map = _load_sector_map()
    twii = _load_twii_returns()
    stock_return_cache: dict[str, pd.DataFrame | None] = {}

    detail_rows: list[dict[str, Any]] = []
    sector_rows: list[dict[str, Any]] = []
    for month in months:
        fold = _train_fold(dataset, feature_cols, month)
        prod_full, prod_top30 = _production_snapshot(fold.prediction_date)
        full_by_ticker = fold.full.set_index("ticker")
        top_regime = set(fold.top30["ticker"].astype(str))
        top_prod = set(prod_top30["ticker"].astype(str))
        prod_full_by_ticker = prod_full.set_index("ticker")
        prod_top_by_ticker = prod_top30.set_index("ticker")
        q50 = float(pd.to_numeric(fold.full["AMOUNT_MA_20"], errors="coerce").quantile(0.50))
        q80 = float(pd.to_numeric(fold.full["AMOUNT_MA_20"], errors="coerce").quantile(0.80))

        for ticker in sorted(top_regime | top_prod):
            regime_row = full_by_ticker.loc[ticker] if ticker in full_by_ticker.index else None
            prod_row = prod_full_by_ticker.loc[ticker] if ticker in prod_full_by_ticker.index else None
            in_regime = ticker in top_regime
            in_prod = ticker in top_prod
            if in_regime and in_prod:
                source = "both"
                exclusion_reason = ""
            elif in_regime:
                source = "regime_only"
                raw_rank = int(prod_row["production_raw_rank"]) if prod_row is not None else None
                if raw_rank is not None and raw_rank <= 30:
                    exclusion_reason = f"production_sector_cap_removed_raw_rank_{raw_rank}"
                elif raw_rank is not None:
                    exclusion_reason = f"production_score_rank_{raw_rank}_below_top30"
                else:
                    exclusion_reason = "missing_from_production_prediction"
            else:
                source = "production_only"
                regime_rank = int(regime_row["regime_rank"]) if regime_row is not None else None
                exclusion_reason = (
                    f"regime_rank_{regime_rank}_below_top30"
                    if regime_rank is not None
                    else "missing_from_regime_snapshot"
                )

            sector = (
                str(prod_row.get("sector"))
                if prod_row is not None and pd.notna(prod_row.get("sector"))
                else sector_map.get(ticker, "")
            )
            amount = regime_row.get("AMOUNT_MA_20") if regime_row is not None else None
            beta = _beta_60(ticker, fold.prediction_date, twii, stock_return_cache)
            detail_rows.append(
                {
                    "month": month,
                    "prediction_date": fold.prediction_date,
                    "ticker": ticker,
                    "source": source,
                    "sector": sector,
                    "beta_60_calc": beta,
                    "amount_ma20": _safe_float(amount),
                    "size_type": _size_bucket(amount, q50, q80),
                    "regime_rank": int(regime_row["regime_rank"]) if regime_row is not None else None,
                    "regime_pred_return": _safe_float(regime_row.get("regime_pred_return")) if regime_row is not None else None,
                    "trade_return_20d": _safe_float(regime_row.get("trade_return_20d")) if regime_row is not None else None,
                    "trade_excess_return_20d": _safe_float(regime_row.get("trade_excess_return_20d")) if regime_row is not None else None,
                    "production_rank": int(prod_top_by_ticker.loc[ticker]["production_rank"]) if ticker in prod_top_by_ticker.index else None,
                    "production_raw_rank": int(prod_row["production_raw_rank"]) if prod_row is not None else None,
                    "production_pred_return_20d": _safe_float(prod_row.get("pred_return_20d")) if prod_row is not None else None,
                    "production_leaderboard_score": _safe_float(prod_row.get("leaderboard_score")) if prod_row is not None else None,
                    "exclusion_reason": exclusion_reason,
                }
            )

        for label, tickers in [("regime_top30", top_regime), ("production_top30", top_prod)]:
            sectors = [sector_map.get(t, "") for t in tickers]
            if label == "production_top30":
                sectors = [
                    str(prod_full_by_ticker.loc[t].get("sector"))
                    if t in prod_full_by_ticker.index and pd.notna(prod_full_by_ticker.loc[t].get("sector"))
                    else sector_map.get(t, "")
                    for t in tickers
                ]
            counts = pd.Series(sectors).value_counts()
            for sector, count in counts.items():
                sector_rows.append(
                    {
                        "month": month,
                        "prediction_date": fold.prediction_date,
                        "portfolio": label,
                        "sector": sector,
                        "count": int(count),
                        "weight": float(count / 30),
                    }
                )

    detail = pd.DataFrame(detail_rows)
    sector_summary = pd.DataFrame(sector_rows)
    out_base = Path(REPORT_DIR) / output_prefix
    detail_path = out_base.with_name(f"{output_prefix}_detail.csv")
    sector_path = out_base.with_name(f"{output_prefix}_sector.csv")
    json_path = out_base.with_suffix(".json")
    md_path = out_base.with_suffix(".md")
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    sector_summary.to_csv(sector_path, index=False, encoding="utf-8-sig")

    summary = (
        detail.groupby(["month", "source"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["month", "source"])
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": months,
        "detail_csv": str(detail_path),
        "sector_csv": str(sector_path),
        "summary": summary.to_dict("records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# REQ-009 Regime Overlap Detail Audit",
        "",
        f"- Months: {', '.join(months)}",
        f"- Detail: `{detail_path}`",
        f"- Sector summary: `{sector_path}`",
        "",
        "## Overlap Summary",
        "",
        "| Month | Both | Regime only | Production only |",
        "|---|---:|---:|---:|",
    ]
    for month in months:
        subset = detail[detail["month"] == month]
        counts = subset["source"].value_counts()
        lines.append(
            f"| {month} | {int(counts.get('both', 0))} | {int(counts.get('regime_only', 0))} | {int(counts.get('production_only', 0))} |"
        )
    lines.extend(
        [
            "",
            "## Production-only Exclusion Reasons",
            "",
            "| Month | Ticker | Sector | Beta | Size type | Reason |",
            "|---|---|---|---:|---|---|",
        ]
    )
    prod_only = detail[detail["source"] == "production_only"].sort_values(["month", "production_rank"])
    for row in prod_only.to_dict("records"):
        lines.append(
            f"| {row['month']} | {row['ticker']} | {row['sector']} | {_safe_float(row['beta_60_calc']) or 0:.2f} | {row['size_type']} | {row['exclusion_reason']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[regime-overlap-detail] wrote {md_path}")
    return {"md": str(md_path), "detail_csv": str(detail_path), "sector_csv": str(sector_path), "json": str(json_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REQ-009 regime overlap detail audit.")
    parser.add_argument("--months", nargs="+", default=["2026-03", "2026-04"])
    parser.add_argument("--output-prefix", default="req009_regime_overlap_detail_20260508")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.months, args.output_prefix)
