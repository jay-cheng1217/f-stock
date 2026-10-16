"""DIAG-002: document Champion entry gates and one ticker gate trace."""

from __future__ import annotations

import argparse
import inspect
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from scripts import build_unified_signals as bus


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not np.isfinite(number):
        return None
    return number


def _line_no(func) -> int:
    return int(inspect.getsourcelines(func)[1])


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _gate_catalog() -> list[dict[str, str]]:
    return [
        {
            "gate": "20D candidate filter / sector cap",
            "line": f"ml.predict.apply_sector_cap, build_unified_signals.py:{_line_no(bus._build_20d_candidates)}",
            "condition": "recommendation in buy set, institutional sell pressure filter, max 20% per sector in Top30",
            "action": "selection only; non-selected names never reach unified entry artifact",
        },
        {
            "gate": "Penalty overlay hard block",
            "line": f"build_unified_signals.py:{_line_no(bus._build_20d_candidates)}",
            "condition": "penalty_overlay enabled and penalty_hard_block == true",
            "action": "removed before Top30 candidate selection",
        },
        {
            "gate": "T+1 risk tag block",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_t1_risk_blocks)}",
            "condition": "T+1 risk_tags or dual 20D hard risk tags map to a hard block reason",
            "action": "drop T1-only or downgrade Dual to 20D-only",
        },
        {
            "gate": "EXPENSIVE_MOMENTUM_RISK",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_expensive_momentum_gate)}",
            "condition": "PE > 50 and price_vs_ma60 > 25%",
            "action": "cap target_weight_ratio to 3%; not a full block",
        },
        {
            "gate": "LOW_LIQUIDITY",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_liquidity_gate)}",
            "condition": f"20D avg_20d_volume < {bus.D20_MIN_AVG_20D_VOLUME:.0f} or avg_20d_amount < {bus.D20_MIN_AVG_20D_AMOUNT:.0f}",
            "action": "full block to signal_type NONE",
        },
        {
            "gate": "T1_LIQUIDITY_BLOCKED",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_liquidity_gate)}",
            "condition": f"T1 avg_5d_volume < {bus.T1_MIN_AVG_5D_VOLUME:.0f} or avg_5d_amount < {bus.T1_MIN_AVG_5D_AMOUNT:.0f}",
            "action": "block T1-only or downgrade Dual to 20D-only",
        },
        {
            "gate": "OVERHEAT_RISK / HIGH_BETA_RISK / MISSING_RISK_CONTEXT",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_overheat_risk_gate)}",
            "condition": "price_vs_ma60 > sector threshold; beta_60 > 1.80; or missing price_vs_ma60/beta context",
            "action": "full block to signal_type NONE",
        },
        {
            "gate": "LOW_ALPHA_WIN_PROB",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_alpha_win_prob_gate)}",
            "condition": f"ALPHA_WIN_GATE_MODE={bus.ALPHA_WIN_GATE_MODE}; threshold/prob/rank based when enabled",
            "action": "full block or Dual downgrade; currently disabled unless env enables it",
        },
        {
            "gate": "MARKET_REGIME_CLOSED / MARKET_REGIME_CAUTION",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_market_regime_gate)}",
            "condition": "market regime action BLOCK, or LIMIT_20D_TOP10_AND_BLOCK_T1 with rank_20d > 10",
            "action": "full block to signal_type NONE",
        },
        {
            "gate": "DISPOSITION_PERIOD",
            "line": f"build_unified_signals.py:{_line_no(bus._apply_tradability_gate)}",
            "condition": "ticker is in official disposition-period list for planned entry date",
            "action": "full block to signal_type NONE",
        },
    ]


def _ticker_trace(signal_path: Path, ticker: str) -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})
    row_df = df[df["ticker"].astype(str) == str(ticker)]
    if row_df.empty:
        raise ValueError(f"{ticker} not found in {signal_path}")
    row = row_df.iloc[0]
    raw_reasons = row.get("tradability_reason")
    reasons = "" if pd.isna(raw_reasons) else str(raw_reasons).strip()
    reason_set = {item.strip() for item in reasons.split(";") if item.strip()}
    avg_vol = _safe_float(row.get("avg_20d_volume_20d"))
    avg_amt = _safe_float(row.get("avg_20d_amount_20d"))
    p60 = _safe_float(row.get("price_vs_ma60_20d"))
    beta = _safe_float(row.get("beta_60_20d"))
    threshold = _safe_float(row.get("overheat_threshold"))
    pe = _safe_float(row.get("expensive_momentum_pe_ratio"))
    exp_p60 = _safe_float(row.get("expensive_momentum_price_vs_ma60"))
    target_weight = _safe_float(row.get("target_weight_ratio"))
    trace = [
        {
            "gate": "candidate_selection",
            "result": "PASS" if pd.notna(row.get("rank_20d")) else "FAIL",
            "detail": f"rank_20d={row.get('rank_20d')} recommendation={row.get('recommendation_20d')}",
        },
        {
            "gate": "expensive_momentum",
            "result": "TRIGGER_CAP" if "EXPENSIVE_MOMENTUM_RISK" in reason_set else "PASS",
            "detail": f"pe={pe} price_vs_ma60={_fmt_pct(exp_p60)} target_weight={_fmt_pct(target_weight)}",
        },
        {
            "gate": "liquidity",
            "result": "FAIL" if "LOW_LIQUIDITY" in reason_set else "PASS",
            "detail": f"avg20_volume={avg_vol} avg20_amount={avg_amt}",
        },
        {
            "gate": "overheat_beta_context",
            "result": "FAIL" if reason_set & {"OVERHEAT_RISK", "HIGH_BETA_RISK", "MISSING_RISK_CONTEXT"} else "PASS",
            "detail": f"price_vs_ma60={_fmt_pct(p60)} threshold={_fmt_pct(threshold)} beta_60={beta} group={row.get('overheat_threshold_group')}",
        },
        {
            "gate": "alpha_win_prob",
            "result": "FAIL" if "LOW_ALPHA_WIN_PROB" in reason_set else "PASS_OR_DISABLED",
            "detail": f"mode={row.get('alpha_win_gate_mode')} threshold={row.get('alpha_win_gate_threshold')}",
        },
        {
            "gate": "market_regime",
            "result": "FAIL" if reason_set & {"MARKET_REGIME_CAUTION", "MARKET_REGIME_CLOSED"} else "PASS",
            "detail": f"state={row.get('market_regime_state')} action={row.get('market_regime_action')} rank_20d={row.get('rank_20d')}",
        },
        {
            "gate": "disposition",
            "result": "FAIL" if "DISPOSITION_PERIOD" in reason_set else "PASS",
            "detail": f"is_disposition={row.get('is_disposition')}",
        },
        {
            "gate": "final_signal",
            "result": str(row.get("signal_type")),
            "detail": f"tradability_status={row.get('tradability_status')} reasons={reasons or '-'}",
        },
    ]
    missing_technical = [
        "price_vs_ma5_at_entry / entry below MA5",
        "MA5 slope or MA5 direction",
        "MA10/MA20 slope confirmation",
        "MACD histogram direction / MACD cross state",
        "volume trend confirmation beyond absolute liquidity floors",
        "institutional accumulation confirmation or sell-pressure veto at final gate",
        "gap-down / weak open filter at entry execution time",
    ]
    return pd.DataFrame(trace), missing_technical


def run(args: argparse.Namespace) -> dict[str, str]:
    signal_path = Path(args.signal_file)
    catalog = pd.DataFrame(_gate_catalog())
    trace, missing_technical = _ticker_trace(signal_path, args.ticker)
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.output_date or datetime.now().strftime("%Y%m%d")
    md_path = report_dir / f"diag002_entry_gate_audit_{stamp}.md"

    lines = [
        "# DIAG-002 Entry Gate Audit",
        "",
        f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Signal file: `{signal_path}`",
        f"- Ticker trace: `{args.ticker}`",
        "",
        "## Production Entry Gates",
        "",
        "| Gate | Code line | Condition | Action |",
        "|---|---|---|---|",
    ]
    for row in catalog.to_dict("records"):
        lines.append(f"| {row['gate']} | `{row['line']}` | {row['condition']} | {row['action']} |")
    lines.extend(
        [
            "",
            "## Missing Technical Entry Checks",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in missing_technical)
    lines.extend(
        [
            "",
            f"## {args.ticker} Gate Trace",
            "",
            "| Gate | Result | Detail |",
            "|---|---|---|",
        ]
    )
    for row in trace.to_dict("records"):
        lines.append(f"| {row['gate']} | {row['result']} | {row['detail']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(trace.to_string(index=False))
    print(f"[diag002] wrote {md_path}")
    return {"md": str(md_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-file", default="ml/models/unified_signals_2026-05-08.csv")
    parser.add_argument("--ticker", default="4577")
    parser.add_argument("--output-date", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
