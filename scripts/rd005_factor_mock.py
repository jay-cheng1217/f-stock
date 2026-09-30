"""Build RD-005 constrained holding-level factor mock artifacts.

This is an offline analysis helper for the SA-approved mock window. It reads
the Champion paper-book ledger and market data, then writes report artifacts.
It does not change model, gate, order, or attribution production behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR  # noqa: E402
from scripts.champion_nav_attribution import DEFAULT_CHAMPION_DB_PATH, _load_twii_daily_returns  # noqa: E402

DEFAULT_START = "2026-04-23"
DEFAULT_END = "2026-04-29"
DEFAULT_INDEX_PATH = Path(INDEX_DIR) / "index_TWII.csv"
DEFAULT_TITLE = "RD-005 Factor Exposure Mock (2026-05-17)"
DEFAULT_PRODUCTION_IMPACT = "none; offline mock only."
TOP_SECTOR_LIMIT = 3
MIN_EFFECTIVE_OBS = 20


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _load_positions_and_marks(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not db_path.exists():
        raise FileNotFoundError(f"Champion DB not found: {db_path}")
    with sqlite3.connect(db_path) as conn:
        positions = pd.read_sql_query("SELECT * FROM unified_positions ORDER BY id", conn)
        marks = pd.read_sql_query("SELECT * FROM unified_marks ORDER BY position_id, mark_date", conn)
    return positions, marks


def _holding_day_frame(positions: pd.DataFrame, marks: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if positions.empty or marks.empty:
        return pd.DataFrame()

    pos = positions.copy()
    mk = marks.copy()
    pos["id"] = pd.to_numeric(pos["id"], errors="coerce").astype("Int64")
    mk["position_id"] = pd.to_numeric(mk["position_id"], errors="coerce").astype("Int64")
    mk["mark_date"] = pd.to_datetime(mk["mark_date"], errors="coerce").dt.normalize()
    merged = mk.merge(pos, left_on="position_id", right_on="id", how="left", suffixes=("_mark", "_position"))
    merged = merged.dropna(subset=["position_id", "mark_date", "ticker"])
    merged = merged.sort_values(["position_id", "mark_date"])

    merged["target_weight"] = pd.to_numeric(merged.get("target_weight"), errors="coerce").fillna(0.0)
    merged["close_return_pct"] = pd.to_numeric(merged.get("close_return_pct"), errors="coerce").fillna(0.0)
    merged["realized_return_pct"] = pd.to_numeric(merged.get("realized_return_pct"), errors="coerce")
    exit_date = pd.to_datetime(merged.get("exit_date"), errors="coerce").dt.normalize()
    merged["is_exit_row"] = (
        pd.to_numeric(merged.get("is_exit_day"), errors="coerce").fillna(0).astype(int).eq(1)
        | (exit_date == merged["mark_date"])
    )
    merged["effective_return_pct"] = np.where(
        merged["is_exit_row"] & merged["realized_return_pct"].notna(),
        merged["realized_return_pct"],
        merged["close_return_pct"],
    )
    merged["prev_return_pct"] = merged.groupby("position_id")["effective_return_pct"].shift(1).fillna(0.0)
    merged["holding_return_pct"] = merged["effective_return_pct"] - merged["prev_return_pct"]
    merged["weighted_return_pct"] = merged["target_weight"] * merged["holding_return_pct"]
    frame = merged[(merged["mark_date"] >= start_ts) & (merged["mark_date"] <= end_ts)].copy()
    frame["date"] = frame["mark_date"]
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(4)
    frame["sector"] = frame.get("sector", "UNKNOWN").fillna("UNKNOWN").astype(str)
    return frame[
        [
            "date",
            "position_id",
            "ticker",
            "sector",
            "target_weight",
            "holding_return_pct",
            "weighted_return_pct",
        ]
    ].reset_index(drop=True)


def _load_daily_return_csv(ticker: str, start: str, end: str) -> pd.DataFrame:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", usecols=["Date", "Close"])
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df["close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    lookback = pd.Timestamp(start).normalize() - pd.Timedelta(days=14)
    end_ts = pd.Timestamp(end).normalize()
    df = df[(df["date"] >= lookback) & (df["date"] <= end_ts)].copy()
    df["return_pct"] = df["close"].pct_change()
    start_ts = pd.Timestamp(start).normalize()
    return df[(df["date"] >= start_ts) & (df["date"] <= end_ts)][["date", "return_pct"]]


def _sector_return_frame(holding_frame: pd.DataFrame, start: str, end: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[pd.DataFrame] = []
    ticker_sector = holding_frame[["ticker", "sector"]].drop_duplicates()
    for row in ticker_sector.to_dict("records"):
        returns = _load_daily_return_csv(row["ticker"], start, end)
        if returns.empty:
            continue
        returns["sector"] = row["sector"]
        rows.append(returns)
    if not rows:
        return pd.DataFrame(columns=["date", "sector", "sector_return_pct"]), {"missing_sector_returns": True}
    raw = pd.concat(rows, ignore_index=True)
    sector_daily = raw.groupby(["date", "sector"], as_index=False)["return_pct"].mean()
    sector_daily = sector_daily.rename(columns={"return_pct": "sector_return_pct"})
    diagnostics = {
        "sector_return_source": "holding ticker equal-weight daily K proxy",
        "sector_return_rows": int(len(sector_daily)),
        "sector_count": int(sector_daily["sector"].nunique()),
    }
    return sector_daily, diagnostics


def _cluster_bootstrap(frame: pd.DataFrame, draws: int, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"draws": 0, "error": "empty frame"}
    rng = np.random.default_rng(seed)
    dates = frame["date"].drop_duplicates().to_numpy()
    sectors = frame["sector_group"].drop_duplicates().to_numpy()
    residuals: list[float] = []
    market_effects: list[float] = []
    sector_effects: list[float] = []
    for i in range(draws):
        if i % 2 == 0:
            sampled = rng.choice(dates, size=len(dates), replace=True)
            sample = pd.concat([frame[frame["date"].eq(day)] for day in sampled], ignore_index=True)
        else:
            sampled = rng.choice(sectors, size=len(sectors), replace=True)
            sample = pd.concat([frame[frame["sector_group"].eq(sector)] for sector in sampled], ignore_index=True)
        residuals.append(float(sample["weighted_factor_residual_pct"].sum()))
        market_effects.append(float(sample["weighted_market_component_pct"].sum()))
        sector_effects.append(float(sample["weighted_sector_component_pct"].sum()))

    def summarize(values: list[float]) -> dict[str, float]:
        arr = np.asarray(values, dtype=float)
        pos = float(np.mean(arr > 0.0))
        neg = float(np.mean(arr < 0.0))
        return {
            "median": float(np.median(arr)),
            "p05": float(np.quantile(arr, 0.05)),
            "p95": float(np.quantile(arr, 0.95)),
            "sign_stability": max(pos, neg),
        }

    return {
        "draws": draws,
        "residual_after_factor_pct": summarize(residuals),
        "market_component_pct": summarize(market_effects),
        "sector_component_pct": summarize(sector_effects),
    }


def _factor_contributions(usable: pd.DataFrame) -> list[dict[str, Any]]:
    if usable.empty:
        return []

    daily_weight = usable.groupby("date")["target_weight"].sum()
    rows = [
        {
            "factor": "market_0_5_load",
            "contribution_pct": float(usable["weighted_market_component_pct"].sum()),
            "active_exposure_pct": float((daily_weight * 0.5).mean()),
            "description": "0.5 * TWII daily return",
        },
        {
            "factor": "sector_total_0_5_load",
            "contribution_pct": float(usable["weighted_sector_component_pct"].sum()),
            "active_exposure_pct": float((daily_weight * 0.5).mean()),
            "description": "0.5 * held-ticker equal-weight sector proxy",
        },
    ]

    for sector_group, group in usable.groupby("sector_group"):
        daily_sector_weight = group.groupby("date")["target_weight"].sum()
        rows.append(
            {
                "factor": f"sector_group_{sector_group}",
                "contribution_pct": float(group["weighted_sector_component_pct"].sum()),
                "active_exposure_pct": float((daily_sector_weight * 0.5).mean()),
                "description": "sector component contribution for this locked sector group",
            }
        )
    return rows


def build_mock(start: str, end: str, db_path: Path, index_path: Path, draws: int, seed: int) -> dict[str, Any]:
    positions, marks = _load_positions_and_marks(db_path)
    frame = _holding_day_frame(positions, marks, start, end)
    if frame.empty:
        return {"status": "failed", "reason": "no holding-day rows"}

    twii = _load_twii_daily_returns(start, end, index_path=index_path).rename(columns={"twii_daily_return_pct": "market_return_pct"})
    sector_returns, sector_diag = _sector_return_frame(frame, start, end)
    frame = frame.merge(twii, on="date", how="left").merge(sector_returns, on=["date", "sector"], how="left")
    missing_market = int(frame["market_return_pct"].isna().sum())
    missing_sector = int(frame["sector_return_pct"].isna().sum())
    frame["market_return_pct"] = pd.to_numeric(frame["market_return_pct"], errors="coerce")
    frame["sector_return_pct"] = pd.to_numeric(frame["sector_return_pct"], errors="coerce")
    usable = frame.dropna(subset=["market_return_pct", "sector_return_pct", "holding_return_pct", "target_weight"]).copy()

    top_sectors = (
        usable.groupby("sector")["target_weight"].sum().abs().sort_values(ascending=False).head(TOP_SECTOR_LIMIT).index.tolist()
    )
    usable["sector_group"] = np.where(usable["sector"].isin(top_sectors), usable["sector"], "OTHER")
    usable["factor_return_pct"] = 0.5 * usable["market_return_pct"] + 0.5 * usable["sector_return_pct"]
    usable["factor_residual_pct"] = usable["holding_return_pct"] - usable["factor_return_pct"]
    usable["weighted_factor_return_pct"] = usable["target_weight"] * usable["factor_return_pct"]
    usable["weighted_factor_residual_pct"] = usable["target_weight"] * usable["factor_residual_pct"]
    usable["weighted_market_component_pct"] = usable["target_weight"] * 0.5 * usable["market_return_pct"]
    usable["weighted_sector_component_pct"] = usable["target_weight"] * 0.5 * usable["sector_return_pct"]

    effective_obs = int(len(usable))
    parameter_count = 2 + int(usable["sector_group"].nunique())
    parameter_limit = max(1, effective_obs // 5)
    bootstrap = _cluster_bootstrap(usable, draws=draws, seed=seed) if effective_obs else {"draws": 0}
    residual_abs = abs(float(usable["weighted_factor_residual_pct"].sum())) if effective_obs else None
    factor_component_sign_stability = [
        bootstrap.get("market_component_pct", {}).get("sign_stability", 0.0),
        bootstrap.get("sector_component_pct", {}).get("sign_stability", 0.0),
    ]
    kill_reasons: list[str] = []
    if effective_obs < MIN_EFFECTIVE_OBS:
        kill_reasons.append("fewer_than_20_effective_holding_day_observations")
    if missing_market > 0 or usable["market_return_pct"].isna().any():
        kill_reasons.append("missing_market_factor_rows")
    if missing_sector > 0:
        kill_reasons.append("missing_sector_factor_rows")
    if parameter_count > parameter_limit:
        kill_reasons.append("parameter_count_above_one_fifth_effective_observations")
    if all(value < 0.60 for value in factor_component_sign_stability):
        kill_reasons.append("bootstrap_sign_stability_below_60pct_for_all_factor_components")

    top_tickers = (
        usable.groupby("ticker")["weighted_factor_residual_pct"]
        .sum()
        .sort_values(key=lambda s: s.abs(), ascending=False)
        .head(10)
        .reset_index()
        .to_dict("records")
    )
    daily = (
        usable.groupby("date")[
            [
                "weighted_return_pct",
                "weighted_factor_return_pct",
                "weighted_factor_residual_pct",
                "weighted_market_component_pct",
                "weighted_sector_component_pct",
            ]
        ]
        .sum()
        .reset_index()
    )
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    return {
        "status": "pass_for_review" if not kill_reasons else "current_data_insufficient",
        "start_date": start,
        "end_date": end,
        "method": "fixed 50/50 market-plus-sector constrained holding-level mock",
        "observation_count": int(len(frame)),
        "effective_observation_count": effective_obs,
        "position_count": int(usable["position_id"].nunique()) if effective_obs else 0,
        "ticker_count": int(usable["ticker"].nunique()) if effective_obs else 0,
        "sector_count": int(usable["sector"].nunique()) if effective_obs else 0,
        "sector_groups": sorted(usable["sector_group"].dropna().unique().tolist()) if effective_obs else [],
        "parameter_count": parameter_count,
        "parameter_limit": parameter_limit,
        "missing_data": {
            "market_factor_rows": missing_market,
            "sector_factor_rows": missing_sector,
            **sector_diag,
        },
        "totals": {
            "weighted_holding_return_pct": float(usable["weighted_return_pct"].sum()) if effective_obs else None,
            "weighted_factor_return_pct": float(usable["weighted_factor_return_pct"].sum()) if effective_obs else None,
            "residual_after_factor_pct": float(usable["weighted_factor_residual_pct"].sum()) if effective_obs else None,
            "abs_residual_after_factor_pct": residual_abs,
        },
        "bootstrap": bootstrap,
        "factor_contributions": _factor_contributions(usable),
        "top_ticker_residuals": top_tickers,
        "daily": daily.to_dict("records"),
        "kill_reasons": kill_reasons,
        "can_distinguish_factor_from_residual": bool(not kill_reasons and residual_abs is not None),
    }


def write_artifacts(
    summary: dict[str, Any],
    report_dir: Path,
    prefix: str,
    title: str = DEFAULT_TITLE,
    production_impact: str = DEFAULT_PRODUCTION_IMPACT,
) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    summary["title"] = title
    summary["production_impact"] = production_impact
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    totals = summary.get("totals", {})
    bootstrap = summary.get("bootstrap", {})
    residual_boot = bootstrap.get("residual_after_factor_pct", {})
    lines = [
        f"# {title}",
        "",
        "## Scope",
        f"- Window: {summary.get('start_date')} to {summary.get('end_date')}",
        f"- Production impact: {production_impact}",
        f"- Status: `{summary.get('status')}`",
        "- Method lock: fixed 50/50 market-plus-sector constrained holding-level factor attribution.",
        "- Action conclusion: none; PM must open a separate ticket for any production action.",
        "",
        "## Data Checks",
        f"- Holding-day observations: {summary.get('observation_count')}",
        f"- Effective observations: {summary.get('effective_observation_count')}",
        f"- Positions / tickers / sectors: {summary.get('position_count')} / {summary.get('ticker_count')} / {summary.get('sector_count')}",
        f"- Parameter count / limit: {summary.get('parameter_count')} / {summary.get('parameter_limit')}",
        f"- Missing market factor rows: {summary.get('missing_data', {}).get('market_factor_rows')}",
        f"- Missing sector factor rows: {summary.get('missing_data', {}).get('sector_factor_rows')}",
        "",
        "## Mock Result",
        f"- Weighted holding return: {_pct(totals.get('weighted_holding_return_pct'))}",
        f"- Weighted factor return: {_pct(totals.get('weighted_factor_return_pct'))}",
        f"- Residual after factor: {_pct(totals.get('residual_after_factor_pct'))}",
        f"- Bootstrap residual median: {_pct(residual_boot.get('median'))}",
        f"- Bootstrap residual 90% interval: {_pct(residual_boot.get('p05'))} to {_pct(residual_boot.get('p95'))}",
        f"- Bootstrap residual sign stability: {residual_boot.get('sign_stability', 0.0):.1%}",
        "",
        "## Factor Contributions",
        "| factor | contribution | active exposure | description |",
        "|---|---:|---:|---|",
    ]
    for row in summary.get("factor_contributions", []):
        lines.append(
            f"| {row['factor']} | {_pct(row.get('contribution_pct'))} | "
            f"{_pct(row.get('active_exposure_pct'))} | {row.get('description', '')} |"
        )
    lines.extend(
        [
            "",
            "## Daily Contributions",
            "| date | holding return | factor return | market contribution | sector contribution | residual |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.get("daily", []):
        lines.append(
            f"| {row['date']} | {_pct(row.get('weighted_return_pct'))} | "
            f"{_pct(row.get('weighted_factor_return_pct'))} | "
            f"{_pct(row.get('weighted_market_component_pct'))} | "
            f"{_pct(row.get('weighted_sector_component_pct'))} | "
            f"{_pct(row.get('weighted_factor_residual_pct'))} |"
        )
    lines.extend(
        [
        "",
        "## Decision",
        f"- Can distinguish factor exposure from residual: `{summary.get('can_distinguish_factor_from_residual')}`",
        f"- Kill reasons: {', '.join(summary.get('kill_reasons') or []) or 'none'}",
        "- Production recommendation: none.",
        "",
        "## Top Ticker Residuals",
        "| ticker | weighted residual |",
        "|---|---:|",
        ]
    )
    for row in summary.get("top_ticker_residuals", []):
        lines.append(f"| {row['ticker']} | {_pct(row.get('weighted_factor_residual_pct'))} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build RD-005 factor exposure mock artifacts.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--index-path", default=str(DEFAULT_INDEX_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default="rd005_factor_mock_20260517")
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260517)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--production-impact", default=DEFAULT_PRODUCTION_IMPACT)
    args = parser.parse_args(argv)

    summary = build_mock(
        args.start,
        args.end,
        db_path=Path(args.champion_db),
        index_path=Path(args.index_path),
        draws=args.bootstrap_draws,
        seed=args.seed,
    )
    paths = write_artifacts(
        summary,
        Path(args.report_dir),
        args.prefix,
        title=args.title,
        production_impact=args.production_impact,
    )
    print(f"[rd005-factor-mock] status={summary['status']} md={paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
