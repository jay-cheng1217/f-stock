"""Backtest Agent Arena Candidate Lab strategies.

This script is separate from the live Arena. It does not add agents to
`AGENTS`, does not create paper positions, and does not alter production gates.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream: Any) -> Any:
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
DAILY_K_DIR = BASE_DIR / "日K資料"
EPS_DIR = BASE_DIR / "季報財務"
VALUATION_DIR = BASE_DIR / "估值資料"
REPORT_DIR = BASE_DIR / "ml" / "reports"

STARTING_CAPITAL = 300_000.0
BUY_COST = 0.002
SELL_COST = 0.002
ROUND_TRIP_COST = BUY_COST + SELL_COST
DEFAULT_START = "2024-01-01"
FINANCIAL_LAG_DAYS = 75


@dataclass(frozen=True)
class CandidateSpec:
    agent_id: str
    name: str
    source_repo: str
    thesis: str
    holding_days: int
    rebalance_every: int
    max_positions: int = 8
    min_turnover_m: float = 20.0
    min_price: float = 10.0
    max_price: float = 500.0


CANDIDATES: tuple[CandidateSpec, ...] = (
    CandidateSpec(
        "efficient_frontier_allocator",
        "Efficient Frontier Allocator",
        "tejtw/TEJ_API_Python_Efficient_Frontier_ProgramSample",
        "Efficient-frontier inspired risk-adjusted ranking proxy.",
        holding_days=20,
        rebalance_every=20,
    ),
    CandidateSpec(
        "var_budget_momentum",
        "VaR Budget Momentum",
        "tejtw/TEJ_API_Python_VaRStandard_ProgramSample",
        "Momentum filtered by realized VaR and drawdown budget.",
        holding_days=10,
        rebalance_every=10,
    ),
    CandidateSpec(
        "eps_growth_value",
        "EPS Growth Value",
        "tejtw/TEJ_API_Python_EPS",
        "Point-in-time EPS growth plus reasonable valuation.",
        holding_days=20,
        rebalance_every=20,
    ),
    CandidateSpec(
        "dividend_eps_quality",
        "Dividend EPS Quality",
        "tejtw/TEJ_API_Python_EPS_dividend_check",
        "Positive EPS, dividend yield, and valuation quality.",
        holding_days=20,
        rebalance_every=20,
    ),
)

NOT_BACKTESTED = [
    {
        "agent_id": "taifex_macro_regime_beta_overlay",
        "name": "TAIFEX Macro Regime Beta Overlay",
        "source_repo": "chrisworsey55/atlas-gic",
        "status": "NOT_BACKTESTED",
        "reason": (
            "Needs historical point-in-time TAIFEX put/call, institutional positioning, "
            "and futures OI. Current public_market_context stores latest/recent snapshots only."
        ),
        "next_action": "Collect TAIFEX context nightly for a forward shadow period or backfill historical official data if available.",
    }
]


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _max_drawdown_pct(equity_values: list[float]) -> float:
    if not equity_values:
        return 0.0
    arr = np.asarray(equity_values, dtype="float64")
    peaks = np.maximum.accumulate(arr)
    drawdowns = arr / np.where(peaks == 0, np.nan, peaks) - 1.0
    return float(np.nanmin(drawdowns) * 100.0)


def _admission_verdict(total_return_pct: float, avg_trade_return_pct: float, trade_count: int, max_drawdown_pct: float) -> tuple[str, str]:
    if trade_count < 80:
        return "BENCH_FAILED", "trade_count below 80; sample too small"
    if total_return_pct <= 5.0:
        return "BENCH_FAILED", "total return did not clear +5% admission floor"
    if avg_trade_return_pct <= 0.0:
        return "BENCH_FAILED", "average trade return is not positive"
    if max_drawdown_pct <= -65.0:
        return "BENCH_FAILED", "max drawdown worse than -65%"
    return "ADMITTED", "backtest profitable with adequate sample and drawdown control"


def _load_daily_k_frame(start_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    usecols = [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Foreign_BuySell",
        "Trust_BuySell",
        "Dealer_BuySell",
        "MA_20",
        "MA_60",
        "VOL_MA_20",
        "ATR_14",
    ]
    for path in sorted(DAILY_K_DIR.glob("*.csv")):
        ticker = path.stem.strip().zfill(4)
        if not ticker[:1].isdigit():
            continue
        try:
            df = pd.read_csv(path, usecols=lambda col: col in usecols)
        except Exception:
            continue
        if df.empty or "Date" not in df or "Close" not in df:
            continue
        df["ticker"] = ticker
        frames.append(df)
    if not frames:
        raise RuntimeError("no daily_k CSV files loaded")
    out = pd.concat(frames, ignore_index=True)
    out["dt"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out[out["dt"] >= pd.Timestamp(start_date) - pd.Timedelta(days=120)].copy()
    rename = {
        "Open": "open_px",
        "High": "high_px",
        "Low": "low_px",
        "Close": "close_px",
        "Volume": "volume",
        "Foreign_BuySell": "foreign_bs",
        "Trust_BuySell": "trust_bs",
        "Dealer_BuySell": "dealer_bs",
        "MA_20": "ma20",
        "MA_60": "ma60",
        "VOL_MA_20": "vol_ma20",
        "ATR_14": "atr14",
    }
    out = out.rename(columns=rename)
    for column in out.columns:
        if column not in {"Date", "ticker", "dt"}:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.sort_values(["ticker", "dt"]).reset_index(drop=True)
    grouped = out.groupby("ticker", group_keys=False)
    out["ret_5"] = grouped["close_px"].pct_change(5)
    out["ret_10"] = grouped["close_px"].pct_change(10)
    out["ret_20"] = grouped["close_px"].pct_change(20)
    out["ret_60"] = grouped["close_px"].pct_change(60)
    out["daily_ret"] = grouped["close_px"].pct_change()
    out["vol_20"] = grouped["daily_ret"].rolling(20).std().reset_index(level=0, drop=True)
    out["vol_60"] = grouped["daily_ret"].rolling(60).std().reset_index(level=0, drop=True)
    out["turnover_m"] = out["close_px"] * out["volume"] / 1_000_000.0
    out["avg_turnover_20m"] = grouped["turnover_m"].rolling(20).mean().reset_index(level=0, drop=True)
    out["price_vs_ma20"] = out["close_px"] / out["ma20"].replace(0, np.nan) - 1.0
    out["price_vs_ma60"] = out["close_px"] / out["ma60"].replace(0, np.nan) - 1.0
    out["atr_pct"] = out["atr14"] / out["close_px"].replace(0, np.nan)
    out["vol_ratio"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)
    out["inst_total"] = out[["foreign_bs", "trust_bs", "dealer_bs"]].fillna(0.0).sum(axis=1)
    out["inst_5_norm"] = (
        grouped["inst_total"].rolling(5).sum().reset_index(level=0, drop=True)
        / grouped["volume"].rolling(20).sum().reset_index(level=0, drop=True).replace(0, np.nan)
    )
    out["entry_open"] = grouped["open_px"].shift(-1)
    out["next_date"] = grouped["dt"].shift(-1)
    return out.replace([np.inf, -np.inf], np.nan)


def _quarter_end(year: int, season: int) -> pd.Timestamp:
    month = {1: 3, 2: 6, 3: 9, 4: 12}[season]
    return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)


def _load_eps_features() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in EPS_DIR.glob("eps_*Q*.csv"):
        match = re.match(r"eps_(\d{4})Q([1-4])\.csv$", path.name)
        if not match:
            continue
        try:
            df = pd.read_csv(path, dtype={"Ticker": str})
        except Exception:
            continue
        if df.empty or "Ticker" not in df or "EPS_Basic" not in df:
            continue
        df["Year"] = int(match.group(1))
        df["Season"] = int(match.group(2))
        frames.append(df[["Ticker", "Year", "Season", "EPS_Basic"]])
    if not frames:
        return pd.DataFrame(columns=["ticker", "available_date", "eps", "eps_yoy"])
    eps = pd.concat(frames, ignore_index=True)
    eps["ticker"] = eps["Ticker"].astype(str).str.zfill(4)
    eps["eps"] = pd.to_numeric(eps["EPS_Basic"], errors="coerce")
    eps = eps.dropna(subset=["eps", "Year", "Season"])
    eps["quarter_end"] = [_quarter_end(int(y), int(s)) for y, s in zip(eps["Year"], eps["Season"])]
    eps["available_date"] = eps["quarter_end"] + pd.Timedelta(days=FINANCIAL_LAG_DAYS)
    eps = eps.sort_values(["ticker", "Year", "Season"])
    prior = eps[["ticker", "Year", "Season", "eps"]].copy()
    prior["Year"] = prior["Year"] + 1
    prior = prior.rename(columns={"eps": "eps_prior_yoy"})
    eps = eps.merge(prior, on=["ticker", "Year", "Season"], how="left")
    eps["eps_yoy"] = eps["eps"] - eps["eps_prior_yoy"]
    return eps[["ticker", "available_date", "eps", "eps_yoy"]]


def _latest_eps_for_date(eps: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    if eps.empty:
        return pd.DataFrame(columns=["ticker", "eps", "eps_yoy"])
    available = eps[eps["available_date"] <= signal_date].copy()
    if available.empty:
        return pd.DataFrame(columns=["ticker", "eps", "eps_yoy"])
    return available.sort_values(["ticker", "available_date"]).groupby("ticker", as_index=False).tail(1)


def _valuation_for_date(signal_date: pd.Timestamp) -> pd.DataFrame:
    target = signal_date.strftime("%Y%m%d")
    candidates: list[Path] = []
    for path in VALUATION_DIR.glob("valuation_*.csv"):
        match = re.match(r"valuation_(\d{8})\.csv$", path.name)
        if match and match.group(1) <= target:
            candidates.append(path)
    if not candidates:
        return pd.DataFrame(columns=["ticker", "PE_Ratio", "PB_Ratio", "Dividend_Yield"])
    path = sorted(candidates)[-1]
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
    except Exception:
        return pd.DataFrame(columns=["ticker", "PE_Ratio", "PB_Ratio", "Dividend_Yield"])
    frame["ticker"] = frame["Ticker"].astype(str).str.zfill(4)
    for col in ["PE_Ratio", "PB_Ratio", "Dividend_Yield"]:
        frame[col] = pd.to_numeric(frame.get(col), errors="coerce")
    return frame[["ticker", "PE_Ratio", "PB_Ratio", "Dividend_Yield"]]


def _base_universe(day: pd.DataFrame, spec: CandidateSpec) -> pd.DataFrame:
    return day[
        (day["avg_turnover_20m"] >= spec.min_turnover_m)
        & (day["close_px"] >= spec.min_price)
        & (day["close_px"] <= spec.max_price)
        & (day["volume"] >= 100_000)
    ].copy()


def _score_candidates(day: pd.DataFrame, spec: CandidateSpec, eps: pd.DataFrame) -> pd.DataFrame:
    candidates = _base_universe(day, spec)
    if candidates.empty:
        return candidates.assign(score=pd.Series(dtype="float64"), reason=pd.Series(dtype="object"))
    if spec.agent_id in {"eps_growth_value", "dividend_eps_quality"}:
        signal_date = pd.Timestamp(candidates["dt"].iloc[0])
        eps_latest = _latest_eps_for_date(eps, signal_date)
        valuation = _valuation_for_date(signal_date)
        candidates = candidates.merge(eps_latest, on="ticker", how="left").merge(valuation, on="ticker", how="left")

    if spec.agent_id == "efficient_frontier_allocator":
        score = (
            candidates["ret_60"] / (candidates["vol_60"] + 0.01)
            + 0.35 * candidates["ret_20"] / (candidates["vol_20"] + 0.01)
            - 2.0 * candidates["vol_60"]
            + 0.15 * candidates["inst_5_norm"]
        )
        mask = (candidates["ret_60"] > 0.0) & (candidates["vol_60"] < 0.055) & (candidates["price_vs_ma60"] > -0.04)
        reason = "efficient-frontier proxy: positive return per realized risk"
    elif spec.agent_id == "var_budget_momentum":
        realized_var_proxy = 1.65 * candidates["vol_20"]
        score = (
            candidates["ret_20"] / (realized_var_proxy + 0.01)
            + 0.40 * candidates["ret_10"]
            - 1.50 * candidates["atr_pct"].fillna(candidates["vol_20"])
        )
        mask = (candidates["ret_20"] > 0.0) & (realized_var_proxy < 0.075) & (candidates["price_vs_ma20"] > -0.03)
        reason = "VaR-budget proxy: momentum inside realized risk budget"
    elif spec.agent_id == "eps_growth_value":
        pe = candidates["PE_Ratio"]
        pb = candidates["PB_Ratio"]
        score = (
            0.8 * candidates["eps_yoy"].fillna(0.0)
            + 0.4 * candidates["eps"].fillna(0.0)
            + 0.5 * candidates["ret_20"].fillna(0.0)
            - 0.02 * pe.fillna(50.0)
            - 0.15 * pb.fillna(5.0)
        )
        mask = (
            candidates["eps"].gt(0)
            & candidates["eps_yoy"].gt(0)
            & pe.gt(0)
            & pe.lt(35)
            & pb.gt(0)
            & pb.lt(4.0)
            & candidates["ret_20"].gt(-0.10)
        )
        reason = "EPS YoY growth with reasonable PE/PB"
    elif spec.agent_id == "dividend_eps_quality":
        dy = candidates["Dividend_Yield"]
        pe = candidates["PE_Ratio"]
        pb = candidates["PB_Ratio"]
        score = (
            0.45 * dy.fillna(0.0)
            + 0.40 * candidates["eps"].fillna(0.0)
            + 0.30 * candidates["eps_yoy"].fillna(0.0)
            + 0.20 * candidates["ret_20"].fillna(0.0)
            - 0.015 * pe.fillna(50.0)
            - 0.10 * pb.fillna(5.0)
        )
        mask = (
            candidates["eps"].gt(0)
            & dy.gt(2.5)
            & pe.gt(0)
            & pe.lt(40)
            & pb.gt(0)
            & pb.lt(4.0)
            & candidates["price_vs_ma20"].gt(-0.08)
        )
        reason = "positive EPS plus dividend yield and valuation quality"
    else:
        score = pd.Series(np.nan, index=candidates.index)
        mask = pd.Series(False, index=candidates.index)
        reason = "unknown"
    return candidates.assign(score=score, reason=reason).loc[mask].dropna(subset=["score"]).sort_values("score", ascending=False)


def _backtest_candidate(feature_df: pd.DataFrame, eps: pd.DataFrame, spec: CandidateSpec, *, start_date: str, end_date: str) -> dict[str, Any]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    subset = feature_df[(feature_df["dt"] >= start) & (feature_df["dt"] <= end)].copy()
    dates = sorted(subset["dt"].dropna().unique())
    all_dates = sorted(feature_df["dt"].dropna().unique())
    date_index = {str(pd.Timestamp(item).date()): idx for idx, item in enumerate(all_dates)}
    day_frames = {str(pd.Timestamp(dt).date()): frame.reset_index(drop=True) for dt, frame in subset.groupby("dt", sort=True)}
    day_indexed = {key: frame.set_index("ticker", drop=False) for key, frame in day_frames.items()}
    cash = STARTING_CAPITAL
    positions: list[dict[str, Any]] = []
    equity_curve: list[float] = []
    trade_returns: list[float] = []
    win_count = 0

    for idx, signal_date in enumerate(dates):
        signal_date_str = str(pd.Timestamp(signal_date).date())
        day_lookup = day_indexed.get(signal_date_str)
        for position in list(positions):
            ticker = str(position["ticker"])
            row = day_lookup.loc[ticker] if day_lookup is not None and ticker in day_lookup.index else None
            price = _safe_float(row.get("close_px")) if row is not None else _safe_float(position.get("last_price"))
            if price is None:
                continue
            position["last_price"] = price
            elapsed = date_index.get(signal_date_str, 0) - date_index.get(str(position["entry_date"]), 0)
            trade_return = price / float(position["entry_price"]) - 1.0
            should_exit = elapsed >= spec.holding_days or trade_return <= -0.09 or trade_return >= 0.18
            if not should_exit:
                continue
            cash += float(position["shares"]) * price * (1.0 - SELL_COST)
            realized = (price * (1.0 - SELL_COST)) / (float(position["entry_price"]) * (1.0 + BUY_COST)) - 1.0
            trade_returns.append(realized)
            win_count += int(realized > 0)
            positions.remove(position)

        if idx % spec.rebalance_every == 0:
            held = {str(position["ticker"]) for position in positions}
            day = day_frames.get(signal_date_str, pd.DataFrame())
            picks = _score_candidates(day, spec, eps)
            if held:
                picks = picks[~picks["ticker"].isin(held)]
            picks = picks.head(max(0, spec.max_positions - len(positions)))
            if not picks.empty:
                slot_cash = cash / max(1, len(picks))
                for _, pick in picks.iterrows():
                    entry_price = _safe_float(pick.get("entry_open"))
                    next_date = pick.get("next_date")
                    if entry_price is None or entry_price <= 0 or pd.isna(next_date):
                        continue
                    gross_notional = slot_cash / (1.0 + BUY_COST)
                    if gross_notional < 1_000 or cash < gross_notional * (1.0 + BUY_COST):
                        continue
                    shares = gross_notional / entry_price
                    cash -= gross_notional * (1.0 + BUY_COST)
                    positions.append(
                        {
                            "ticker": str(pick["ticker"]),
                            "entry_date": str(pd.Timestamp(next_date).date()),
                            "entry_price": entry_price,
                            "shares": shares,
                            "last_price": entry_price,
                        }
                    )

        market_value = 0.0
        for position in positions:
            ticker = str(position["ticker"])
            row = day_lookup.loc[ticker] if day_lookup is not None and ticker in day_lookup.index else None
            price = _safe_float(row.get("close_px")) if row is not None else _safe_float(position.get("last_price"))
            if price is not None:
                position["last_price"] = price
                market_value += float(position["shares"]) * price
        equity_curve.append(cash + market_value)

    ending_equity = equity_curve[-1] if equity_curve else STARTING_CAPITAL
    total_return_pct = float((ending_equity / STARTING_CAPITAL - 1.0) * 100.0)
    trade_count = len(trade_returns)
    avg_trade_return_pct = float(np.mean(trade_returns) * 100.0) if trade_returns else 0.0
    win_rate_pct = float(win_count / trade_count * 100.0) if trade_count else 0.0
    max_dd = _max_drawdown_pct(equity_curve)
    status, reason = _admission_verdict(total_return_pct, avg_trade_return_pct, trade_count, max_dd)
    return {
        **asdict(spec),
        "status": "BACKTESTED",
        "backtest_start": str(start.date()),
        "backtest_end": str(end.date()),
        "starting_capital": STARTING_CAPITAL,
        "ending_equity": round(float(ending_equity), 2),
        "total_return_pct": round(total_return_pct, 4),
        "avg_trade_return_pct": round(avg_trade_return_pct, 4),
        "win_rate_pct": round(win_rate_pct, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "trade_count": trade_count,
        "admission_status": status,
        "admission_reason": reason,
        "method_note": "Candidate Lab fixed-hold no-lookahead backtest; not live Arena capital.",
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    rows = report["results"]
    lines = [
        f"# Candidate Agent Backtest ({report['asof']})",
        "",
        "## Verdict",
        "",
        "- This is Candidate Lab only; no agent was added to Main League or paper capital.",
        "- Backtest uses signal close -> next open entry with costs; fixed-hold/stop/take-profit exit.",
        "- TAIFEX macro candidate is not fully backtested because historical TAIFEX context is not yet collected.",
        "",
        "## Results",
        "",
        "| Agent | Status | Return | MDD | Avg Trade | Trades | Admission | Reason |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        if row.get("status") == "NOT_BACKTESTED":
            lines.append(
                f"| {row['agent_id']} | NOT_BACKTESTED |  |  |  |  | NOT_READY | {row['reason']} |"
            )
        else:
            lines.append(
                "| {agent_id} | BACKTESTED | {total_return_pct:+.2f}% | {max_drawdown_pct:+.2f}% | "
                "{avg_trade_return_pct:+.2f}% | {trade_count} | {admission_status} | {admission_reason} |".format(**row)
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_candidate_backtests(*, asof: str, start_date: str) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    feature_df = _load_daily_k_frame(start_date)
    max_holding = max(spec.holding_days for spec in CANDIDATES)
    all_dates = sorted(feature_df["dt"].dropna().unique())
    end_idx = max(0, len(all_dates) - max_holding - 1)
    end_date = str(pd.Timestamp(all_dates[end_idx]).date())
    eps = _load_eps_features()
    results: list[dict[str, Any]] = [
        _backtest_candidate(feature_df, eps, spec, start_date=start_date, end_date=end_date)
        for spec in CANDIDATES
    ]
    results.extend(NOT_BACKTESTED)
    report = {
        "status": "ok",
        "asof": asof,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "Candidate Lab P0 agents from source-library rescreen",
        "backtest_start": start_date,
        "backtest_end": end_date,
        "results": results,
        "summary": {
            "backtested_count": sum(1 for row in results if row.get("status") == "BACKTESTED"),
            "not_backtested_count": sum(1 for row in results if row.get("status") == "NOT_BACKTESTED"),
            "admitted_count": sum(1 for row in results if row.get("admission_status") == "ADMITTED"),
        },
    }
    json_path = REPORT_DIR / f"candidate_agent_backtest_{asof}.json"
    md_path = REPORT_DIR / f"candidate_agent_backtest_{asof}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(report, md_path)
    return {**report, "json_path": str(json_path.relative_to(BASE_DIR)), "md_path": str(md_path.relative_to(BASE_DIR))}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest Candidate Lab agents.")
    parser.add_argument("--asof", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--start-date", default=DEFAULT_START)
    args = parser.parse_args(argv)
    report = run_candidate_backtests(asof=args.asof, start_date=args.start_date)
    print(
        json.dumps(
            {
                "status": report["status"],
                "summary": report["summary"],
                "json_path": report["json_path"],
                "md_path": report["md_path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
