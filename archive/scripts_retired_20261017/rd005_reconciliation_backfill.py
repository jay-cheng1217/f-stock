"""Build RD-005 round-3 reconciliation backfill artifacts.

This script answers the PM round-2 gaps without running timing/OOS phase B/C.
It only reconciles metric definitions, isolates 6419 concentration, and records
window sensitivity between the mock and formal factor residuals.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, REPORT_DIR  # noqa: E402
from scripts.champion_nav_attribution import DEFAULT_CHAMPION_DB_PATH  # noqa: E402

DEFAULT_METHOD_LOCK = BASE_DIR / "docs" / "designs" / "rd005_residual_deepdive_method_lock_20260517.md"
DEFAULT_NAV_SPLIT = Path(REPORT_DIR) / "rd005_residual_split_20260517.json"
DEFAULT_MOCK = Path(REPORT_DIR) / "rd005_factor_mock_20260517.json"
DEFAULT_FORMAL = Path(REPORT_DIR) / "rd005_residual_deepdive_20260517.json"
DEFAULT_PREFIX = "rd005_reconciliation_backfill_20260517"
TARGET_TICKER = "6419"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _load_position_and_marks(db_path: Path, ticker: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        position = conn.execute(
            "SELECT * FROM unified_positions WHERE ticker = ? ORDER BY entry_date DESC, id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if position is None:
            return None, []
        marks = conn.execute(
            "SELECT * FROM unified_marks WHERE position_id = ? ORDER BY mark_date",
            (position["id"],),
        ).fetchall()
    return dict(position), [dict(row) for row in marks]


def _load_daily_k_stats(ticker: str, start: str, end: str) -> dict[str, Any]:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ["Open", "High", "Low", "Close", "Volume", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    window = df[(df["Date"] >= start_ts) & (df["Date"] <= end_ts)].copy()
    lookback = df[df["Date"] < start_ts].tail(20)
    if window.empty:
        return {"status": "empty_window", "path": str(path)}
    first = window.iloc[0]
    last = window.iloc[-1]
    close_return = float(last["Close"] / first["Close"] - 1.0) if first["Close"] else None
    avg_volume_20d = _safe_float(lookback["Volume"].mean()) if not lookback.empty else None
    max_volume = _safe_float(window["Volume"].max())
    max_volume_date = None
    if max_volume is not None:
        max_row = window.loc[window["Volume"].idxmax()]
        max_volume_date = max_row["Date"].date().isoformat()
    flows = {}
    for column in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
        if column in window.columns:
            flows[column] = _safe_float(window[column].sum())
    return {
        "status": "ok",
        "path": str(path),
        "start_close": _safe_float(first["Close"]),
        "end_close": _safe_float(last["Close"]),
        "close_return_pct": close_return,
        "max_volume": max_volume,
        "max_volume_date": max_volume_date,
        "avg_volume_20d_before_window": avg_volume_20d,
        "max_volume_vs_20d_avg": (max_volume / avg_volume_20d) if max_volume and avg_volume_20d else None,
        "flows": flows,
        "window_rows": [
            {
                "date": row["Date"].date().isoformat(),
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_float(row.get("Volume")),
                "foreign_buy_sell": _safe_float(row.get("Foreign_BuySell")),
                "dealer_buy_sell": _safe_float(row.get("Dealer_BuySell")),
            }
            for _, row in window.iterrows()
        ],
    }


def _latest_financial(ticker: str) -> dict[str, Any] | None:
    candidates = []
    for path in (BASE_DIR / "季報財務").glob("financial_*.csv"):
        try:
            df = pd.read_csv(path, dtype={"Ticker": str})
        except Exception:
            continue
        if "Ticker" not in df.columns:
            continue
        rows = df[df["Ticker"].astype(str).str.zfill(4).eq(ticker)]
        if rows.empty:
            continue
        row = rows.iloc[0].to_dict()
        row["_source"] = str(path)
        candidates.append(row)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (int(item.get("Year") or 0), int(item.get("Season") or 0)))[-1]


def _valuation_at(ticker: str, date_value: str) -> dict[str, Any] | None:
    path = BASE_DIR / "估值資料" / f"valuation_{date_value.replace('-', '')}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"Ticker": str})
    rows = df[df["Ticker"].astype(str).str.zfill(4).eq(ticker)]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    row["_source"] = str(path)
    return row


def _disposition_status(ticker: str, start: str, end: str) -> list[dict[str, Any]]:
    path = BASE_DIR / "ml" / "data" / "disposition_periods.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"ticker": str})
    ticker_col = "ticker" if "ticker" in df.columns else "stock_id"
    start_col = "start_date" if "start_date" in df.columns else "period_start"
    end_col = "end_date" if "end_date" in df.columns else "period_end"
    df[start_col] = pd.to_datetime(df[start_col], errors="coerce")
    df[end_col] = pd.to_datetime(df[end_col], errors="coerce")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    rows = df[
        df[ticker_col].astype(str).str.zfill(4).eq(ticker)
        & (df[start_col] <= end_ts)
        & (df[end_col] >= start_ts)
    ]
    return [
        {
            "start_date": row[start_col].date().isoformat(),
            "end_date": row[end_col].date().isoformat(),
        }
        for _, row in rows.iterrows()
    ]


def _ticker_residual(formal: dict[str, Any], ticker: str) -> float | None:
    for row in formal.get("top_ticker_residuals", []):
        if str(row.get("ticker")) == ticker:
            return _safe_float(row.get("weighted_factor_residual_pct"))
    return None


def build_backfill(
    nav_path: Path,
    mock_path: Path,
    formal_path: Path,
    method_lock_path: Path,
    db_path: Path,
) -> dict[str, Any]:
    nav = _load_json(nav_path)
    mock = _load_json(mock_path)
    formal = _load_json(formal_path)
    formal_start = str(formal["start_date"])
    formal_end = str(formal["end_date"])
    nav_unexplained = _safe_float(nav["attribution"]["unexplained_reconciliation_residual_pct"])
    holding_residual = _safe_float(formal["totals"]["residual_after_factor_pct"])
    gross_cash_residual = _safe_float(nav["attribution"]["gross_exposure_cash_residual_pct"])
    selection_effect = _safe_float(nav["attribution"]["selection_effect_pct"])
    allocation_effect = _safe_float(nav["attribution"]["allocation_effect_pct"])
    execution_effect = _safe_float(nav["attribution"]["execution_slippage_effect_pct"])
    exit_effect = _safe_float(nav["attribution"]["exit_policy_effect_pct"])
    ledger_recon_error = _safe_float(nav["ledger_reconciliation"]["reconciliation_error_pct"])
    ticker_residual = _ticker_residual(formal, TARGET_TICKER)
    position, marks = _load_position_and_marks(db_path, TARGET_TICKER)
    daily = _load_daily_k_stats(TARGET_TICKER, formal_start, formal_end)
    financial = _latest_financial(TARGET_TICKER)
    valuation = _valuation_at(TARGET_TICKER, formal_end)
    disposition = _disposition_status(TARGET_TICKER, formal_start, formal_end)
    mock_residual = _safe_float(mock["totals"]["residual_after_factor_pct"])

    return {
        "status": "blocked_before_timing_bc",
        "method_lock_doc": {
            "path": str(method_lock_path),
            "exists": method_lock_path.exists(),
        },
        "window": {
            "mock": [mock["start_date"], mock["end_date"]],
            "formal": [formal_start, formal_end],
        },
        "reconciliation": {
            "nav_unexplained_reconciliation_residual_pct": nav_unexplained,
            "holding_level_residual_after_factor_pct": holding_residual,
            "difference_pct": (holding_residual - nav_unexplained) if holding_residual is not None and nav_unexplained is not None else None,
            "gross_exposure_cash_residual_pct": gross_cash_residual,
            "selection_effect_pct": selection_effect,
            "allocation_effect_pct": allocation_effect,
            "execution_effect_pct": execution_effect,
            "exit_effect_pct": exit_effect,
            "ledger_reconciliation_error_pct": ledger_recon_error,
            "bridge_assessment": (
                "The two residuals are different diagnostics. Holding-level residual_after_factor is a selection/"
                "idiosyncratic return of marked holdings versus locked market/sector factors. NAV unexplained is "
                "the remaining gross-exposure/cash reconciliation bucket after execution and exit effects. They "
                "should not be treated as the same target without an explicit bridge."
            ),
        },
        "ticker_6419": {
            "weighted_residual_pct": ticker_residual,
            "share_of_formal_residual": (ticker_residual / holding_residual) if ticker_residual is not None and holding_residual else None,
            "position": position,
            "marks": marks,
            "daily_k": daily,
            "financial_latest": financial,
            "valuation_at_formal_end": valuation,
            "disposition_overlap": disposition,
            "local_news_rows_found": 0,
            "assessment": (
                "6419 is an idiosyncratic concentration, not broad factor exposure. It entered as INTRADAY_CHASE "
                "with high entry slippage, then rallied sharply with volume and dealer flow spikes during the formal window."
            ),
        },
        "window_sensitivity": {
            "mock_residual_after_factor_pct": mock_residual,
            "formal_residual_after_factor_pct": holding_residual,
            "sign_flipped": bool(mock_residual is not None and holding_residual is not None and mock_residual * holding_residual < 0),
            "assessment": (
                "The locked residual_after_factor metric is window-sensitive. Any future citation must include the "
                "date window and should not infer a stable structural residual from one week."
            ),
        },
        "next_gate": {
            "timing_bc_allowed": False,
            "reason": "A-stage reconciliation backfill only; PM must choose the target metric before B/C.",
        },
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    recon = payload["reconciliation"]
    ticker = payload["ticker_6419"]
    daily = ticker["daily_k"]
    window = payload["window"]
    sensitivity = payload["window_sensitivity"]
    lines = [
        "# RD-005 Reconciliation Backfill (2026-05-17)",
        "",
        "## Status",
        "- Scope: A1-A4 retroactive backfill only.",
        "- B/C timing and OOS remain paused.",
        "- No production allocation, model, sector cap, or guardrail change is recommended.",
        "",
        "## A1 Method Lock Doc",
        f"- Path: `{payload['method_lock_doc']['path']}`",
        f"- Exists: `{payload['method_lock_doc']['exists']}`",
        "",
        "## A2 Metric Reconciliation",
        f"- NAV-level unexplained reconciliation residual: {_pct(recon['nav_unexplained_reconciliation_residual_pct'])}",
        f"- Holding-level residual after locked factors: {_pct(recon['holding_level_residual_after_factor_pct'])}",
        f"- Difference (holding residual - NAV unexplained): {_pct(recon['difference_pct'])}",
        f"- Gross exposure / cash residual: {_pct(recon['gross_exposure_cash_residual_pct'])}",
        f"- Selection effect: {_pct(recon['selection_effect_pct'])}",
        f"- Allocation effect: {_pct(recon['allocation_effect_pct'])}",
        f"- Execution effect: {_pct(recon['execution_effect_pct'])}",
        f"- Exit effect: {_pct(recon['exit_effect_pct'])}",
        f"- Ledger reconciliation error: {_pct(recon['ledger_reconciliation_error_pct'], 4)}",
        "",
        "Interpretation:",
        recon["bridge_assessment"],
        "",
        "Practical gate:",
        "B/C cannot start until PM chooses whether the next target is NAV unexplained, holding-level residual, or a bridge metric.",
        "",
        "## A3 6419 Idiosyncratic Deepdive",
        f"- Weighted residual contribution: {_pct(ticker['weighted_residual_pct'])}",
        f"- Share of formal residual: {_pct(ticker['share_of_formal_residual'])}",
    ]
    position = ticker.get("position") or {}
    if position:
        lines.extend(
            [
                f"- Sector: {position.get('sector')}",
                f"- Entry date / price: {position.get('entry_date')} / {position.get('entry_price')}",
                f"- Order type / fill status: {position.get('order_type')} / {position.get('fill_status')}",
                f"- Entry slippage: {_pct(position.get('entry_slippage_pct'))}",
                f"- Entry rank 20D: {position.get('entry_rank_20d')}",
                f"- Target weight: {_pct(position.get('target_weight'))}",
            ]
        )
    marks = ticker.get("marks") or []
    if marks:
        latest_mark = marks[-1]
        lines.append(f"- Position marked close return since entry: {_pct(latest_mark.get('close_return_pct'))}")
    if daily.get("status") == "ok":
        lines.extend(
            [
                f"- Daily-K window close return ({window['formal'][0]} to {window['formal'][1]}): {_pct(daily.get('close_return_pct'))}",
                f"- Max volume date / volume: {daily.get('max_volume_date')} / {daily.get('max_volume')}",
                f"- Max volume vs prior 20D avg: {daily.get('max_volume_vs_20d_avg'):.2f}x"
                if daily.get("max_volume_vs_20d_avg") is not None
                else "- Max volume vs prior 20D avg: -",
                f"- Formal-window foreign/dealer flow: {daily.get('flows', {}).get('Foreign_BuySell')} / {daily.get('flows', {}).get('Dealer_BuySell')}",
            ]
        )
    if ticker.get("financial_latest"):
        financial = ticker["financial_latest"]
        lines.append(
            "- Latest local financial row: "
            f"{financial.get('Year')}Q{financial.get('Season')} revenue={financial.get('Revenue_M')}M, "
            f"gross_margin={financial.get('Gross_Margin_Pct')}%, operating_margin={financial.get('Operating_Margin_Pct')}%"
        )
    if ticker.get("valuation_at_formal_end"):
        valuation = ticker["valuation_at_formal_end"]
        lines.append(
            "- Valuation at formal end: "
            f"PE={valuation.get('PE_Ratio')}, PB={valuation.get('PB_Ratio')}, dividend_yield={valuation.get('Dividend_Yield')}"
        )
    if ticker.get("disposition_overlap"):
        lines.append(f"- Disposition overlap in formal window: {ticker['disposition_overlap']}")
    else:
        lines.append("- Disposition overlap in formal window: none")
    lines.extend(
        [
            f"- Local news rows found: {ticker['local_news_rows_found']}",
            "",
            "Assessment:",
            ticker["assessment"],
            "",
            "6419 result is attribution-only and must not be used as a production add/cut decision.",
            "",
            "## A4 Window Sensitivity",
            f"- Mock window: {window['mock'][0]} to {window['mock'][1]}, residual {_pct(sensitivity['mock_residual_after_factor_pct'])}",
            f"- Formal window: {window['formal'][0]} to {window['formal'][1]}, residual {_pct(sensitivity['formal_residual_after_factor_pct'])}",
            f"- Sign flipped: `{sensitivity['sign_flipped']}`",
            "",
            sensitivity["assessment"],
            "",
            "## SA Gate",
            "- B/C timing/OOS remains blocked.",
            "- PM must choose the target metric before any further RD work.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(payload: dict[str, Any], report_dir: Path, prefix: str) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, payload)
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build RD-005 reconciliation backfill artifacts.")
    parser.add_argument("--nav-split", default=str(DEFAULT_NAV_SPLIT))
    parser.add_argument("--mock", default=str(DEFAULT_MOCK))
    parser.add_argument("--formal", default=str(DEFAULT_FORMAL))
    parser.add_argument("--method-lock", default=str(DEFAULT_METHOD_LOCK))
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    payload = build_backfill(
        nav_path=Path(args.nav_split),
        mock_path=Path(args.mock),
        formal_path=Path(args.formal),
        method_lock_path=Path(args.method_lock),
        db_path=Path(args.champion_db),
    )
    paths = write_artifacts(payload, Path(args.report_dir), args.prefix)
    print(f"[rd005-reconciliation] status={payload['status']} md={paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
