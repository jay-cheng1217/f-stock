"""Send a daily manual position risk-cut reminder for Champion open positions.

This script is intentionally read-only. It reads the Champion paper book,
today's unified signals, and daily K files, then writes log/json artifacts and
sends a UTF-8 email reminder. It never mutates the paper portfolio.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR
from scripts.exdiv_utils import cum_dividend
from scripts.send_daily_email import load_email_settings, send_email
from scripts.taiwan_trading_calendar import is_taiwan_trading_day, previous_taiwan_trading_day


DEFAULT_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_TO_EMAIL = "jaycheng821217@gmail.com"
MODEL_ERA = "v2_incident_window"
DAILY_K_DIR = BASE_DIR / "日K資料"
LOG_DIR = BASE_DIR / "logs"
UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
BUY_RECOMMENDATIONS = {"強力買進", "建議買進"}
HARD_GUARDRAIL_KEYWORDS = (
    "OVERHEAT_RISK",
    "HIGH_BETA_RISK",
    "LOW_LIQUIDITY",
    "DISPOSITION",
    "EXPENSIVE_MOMENTUM",
    "CHIP_DIVERGENCE",
    "FUNDAMENTAL_GUARD",
    "CRASH",
    "EXTREME_PREDICTION",
)


@dataclass
class PositionDecision:
    ticker: str
    name: str | None
    category: str
    label: str
    reasons: list[str]
    guardrail_reasons: list[str]
    entry_date: str | None = None
    target_weight: float | None = None
    unrealized_return: float | None = None
    close: float | None = None
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    rsi14: float | None = None
    return_1d: float | None = None
    rank_20d: float | None = None
    recommendation_20d: str | None = None
    in_signal_pool: bool = False


@dataclass
class RiskCutReport:
    as_of_date: str
    signal_date: str
    signal_source: str
    db_latest_date: str
    counts: dict[str, int]
    decisions: list[PositionDecision]
    hard_guardrail_details: list[str]
    text: str
    html: str


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _boolish(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value) != 0.0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _date_str(value: date | datetime | pd.Timestamp) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Invalid date: {value}")
    return parsed.date()


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def _ticker(value: Any) -> str:
    raw = _clean_text(value)
    if raw.endswith(".0"):
        raw = raw[:-2]
    return raw.zfill(4) if raw.isdigit() and len(raw) < 4 else raw


def _latest_signal_path(as_of: date) -> tuple[date, Path] | None:
    candidates: list[tuple[date, Path]] = []
    for path in Path(MODEL_DIR).glob("unified_signals_*.csv"):
        match = UNIFIED_SIGNAL_RE.match(path.name)
        if not match:
            continue
        parsed = pd.to_datetime(match.group(1), errors="coerce")
        if pd.isna(parsed):
            continue
        day = parsed.date()
        if day <= as_of:
            candidates.append((day, path))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1]


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Champion DB not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _latest_db_activity_date(conn: sqlite3.Connection) -> date | None:
    values: list[str] = []
    for sql in (
        "SELECT MAX(prediction_date) AS d FROM unified_runs",
        "SELECT MAX(entry_date) AS d FROM unified_positions",
        "SELECT MAX(exit_date) AS d FROM unified_positions",
        "SELECT MAX(mark_date) AS d FROM unified_marks",
    ):
        try:
            row = conn.execute(sql).fetchone()
        except sqlite3.Error:
            continue
        if row and row["d"]:
            values.append(str(row["d"]))
    parsed = [pd.to_datetime(item, errors="coerce") for item in values]
    parsed = [item for item in parsed if not pd.isna(item)]
    if not parsed:
        return None
    return max(item.date() for item in parsed)


def _load_open_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM unified_positions
        WHERE status = 'open'
        ORDER BY entry_date ASC, ticker ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _load_signals(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].map(_ticker)
    return df


def _signal_map(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if df.empty or "ticker" not in df.columns:
        return {}
    return {str(row["ticker"]): dict(row) for _, row in df.iterrows()}


def _load_daily_k_snapshot(ticker: str, as_of: date) -> dict[str, Any]:
    path = DAILY_K_DIR / f"{ticker}.csv"
    if not path.exists():
        return {"missing": True, "source": str(path)}
    df = pd.read_csv(path)
    if "Date" not in df.columns:
        return {"missing": True, "source": str(path), "error": "missing Date column"}
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df.get("Close"), errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    for window in (5, 20, 60):
        column = f"MA_{window}"
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
        fallback = df["Close"].rolling(window, min_periods=window).mean()
        df[column] = df[column].fillna(fallback)
    if "RSI_14" not in df.columns:
        df["RSI_14"] = pd.NA
    df["RSI_14"] = pd.to_numeric(df["RSI_14"], errors="coerce").astype("float64")
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi_fallback = 100 - (100 / (1 + rs))
    df["RSI_14"] = df["RSI_14"].fillna(rsi_fallback)
    subset = df[df["Date"].dt.date <= as_of]
    if subset.empty:
        return {"missing": True, "source": str(path), "error": f"no row <= {as_of}"}
    row = subset.iloc[-1]
    prev = subset.iloc[-2] if len(subset) >= 2 else None
    close = _finite(row.get("Close"))
    prev_close = _finite(prev.get("Close")) if prev is not None else None
    return_1d = close / prev_close - 1.0 if close is not None and prev_close else None
    return {
        "missing": False,
        "date": _date_str(pd.Timestamp(row.get("Date"))),
        "close": close,
        "ma5": _finite(row.get("MA_5")),
        "ma20": _finite(row.get("MA_20")),
        "ma60": _finite(row.get("MA_60")),
        "rsi14": _finite(row.get("RSI_14")),
        "return_1d": return_1d,
        "source": str(path),
    }


def _hard_guardrail_reasons(signal_row: dict[str, Any] | None) -> list[str]:
    if not signal_row:
        return []
    reasons: list[str] = []
    candidates = [
        ("guardrail_blocked", "guardrail_trigger_reason"),
        ("guardrail_blocked_20d", "guardrail_trigger_reason_20d"),
        ("tradability_blocked", "tradability_reason"),
    ]
    for flag_col, reason_col in candidates:
        reason = _clean_text(signal_row.get(reason_col))
        if not reason:
            continue
        if not _boolish(signal_row.get(flag_col)):
            continue
        if any(keyword in reason for keyword in HARD_GUARDRAIL_KEYWORDS):
            reasons.append(f"{reason_col}={reason}")
    for flag_col in ("expensive_momentum_risk", "expensive_momentum_risk_20d"):
        if _boolish(signal_row.get(flag_col)):
            reasons.append(f"{flag_col}=1")
    return reasons


def classify_position(
    position: dict[str, Any],
    signal_row: dict[str, Any] | None,
    k_snapshot: dict[str, Any],
) -> PositionDecision:
    ticker = _ticker(position.get("ticker"))
    name = _clean_text(signal_row.get("name") if signal_row else position.get("name")) or None
    in_signal_pool = signal_row is not None
    recommendation = _clean_text(signal_row.get("recommendation_20d") if signal_row else None) or None
    rank_20d = _finite(signal_row.get("rank_20d") if signal_row else None)
    entry_price = _finite(position.get("entry_price"))
    close = _finite(k_snapshot.get("close"))
    ma5 = _finite(k_snapshot.get("ma5"))
    ma20 = _finite(k_snapshot.get("ma20"))
    ma60 = _finite(k_snapshot.get("ma60"))
    rsi14 = _finite(k_snapshot.get("rsi14"))
    return_1d = _finite(k_snapshot.get("return_1d"))
    unrealized_return = close / entry_price - 1.0 if close is not None and entry_price else None
    dividend_adjustment_warning: str | None = None
    # 除息還原以實際 K 線快照日為截止，不可用執行日跨越尚未載入的市場資料。
    if unrealized_return is not None and position.get("entry_date"):
        snapshot_date = _clean_text(k_snapshot.get("date"))
        if not snapshot_date:
            unrealized_return = None
            dividend_adjustment_warning = "日 K 快照日期缺失，停損報酬暫不判定"
        else:
            try:
                dv = cum_dividend(ticker, str(position["entry_date"]), snapshot_date)
                unrealized_return = (close + dv) / entry_price - 1.0
            except Exception as exc:  # noqa: BLE001 - 禁止退回未還原價造成除息誤停損
                unrealized_return = None
                dividend_adjustment_warning = f"除權息還原失敗，停損報酬暫不判定：{exc}"
    guardrail_reasons = _hard_guardrail_reasons(signal_row)

    red: list[str] = []
    yellow: list[str] = []
    green: list[str] = []
    if dividend_adjustment_warning:
        yellow.append(dividend_adjustment_warning)
    bull_alignment = ma5 is not None and ma20 is not None and ma60 is not None and ma5 > ma20 > ma60

    if in_signal_pool and recommendation not in BUY_RECOMMENDATIONS:
        red.append(f"recommendation_20d={recommendation or '缺失'}，非買進")
    if close is None or ma5 is None or ma20 is None:
        red.append("日K close/MA5/MA20 缺值")
    else:
        if close < ma5 and close < ma20:
            reason = f"close {_pct(close / ma5 - 1.0)} vs MA5 且 {_pct(close / ma20 - 1.0)} vs MA20，雙均線跌破"
            if bull_alignment:
                yellow.append(f"BULL alignment 下回檔：{reason}")
            else:
                red.append(reason)
        if ma5 < ma20:
            red.append("MA5 < MA20，短中期翻空")
    if in_signal_pool and rank_20d is not None and rank_20d > 30:
        red.append(f"rank_20d={rank_20d:.0f} > 30，不在 Top30")
    if unrealized_return is not None and unrealized_return < -0.10:
        red.append(f"持倉收盤累計報酬 {_pct(unrealized_return)} < -10%，逼近硬停損")
    if guardrail_reasons:
        red.extend([f"hard guardrail: {reason}" for reason in guardrail_reasons])

    if not red:
        if bull_alignment and close is not None and ma5 is not None and close < ma5:
            yellow.append("BULL alignment 但 close < MA5，短線跌破")
        if close is not None and ma5 is not None and rsi14 is not None and close > ma5 and rsi14 > 75:
            yellow.append(f"close > MA5 但 RSI_14={rsi14:.1f} > 75，過熱")
        if return_1d is not None and return_1d < -0.03:
            yellow.append(f"1D return {_pct(return_1d)} < -3%，急跌")

    if not red and not yellow:
        if bull_alignment:
            green.append("BULL alignment (MA5 > MA20 > MA60)")
        if close is not None and ma5 is not None and close > ma5:
            green.append("close > MA5")
        if rsi14 is not None and rsi14 <= 75:
            green.append(f"RSI_14={rsi14:.1f} <= 75")
        if in_signal_pool and rank_20d is not None and rank_20d <= 30:
            green.append(f"rank_20d={rank_20d:.0f} <= 30")
        if in_signal_pool and recommendation in BUY_RECOMMENDATIONS:
            green.append(f"recommendation_20d={recommendation}")
        if not green:
            green.append("未觸發紅/黃條件，暫列保留")

    if red:
        category = "red"
        label = "🔴 建議降部位"
        reasons = red
    elif yellow:
        category = "yellow"
        label = "🟡 觀察"
        reasons = yellow
    else:
        category = "green"
        label = "🟢 保留"
        reasons = green

    return PositionDecision(
        ticker=ticker,
        name=name,
        category=category,
        label=label,
        reasons=reasons,
        guardrail_reasons=guardrail_reasons,
        entry_date=_clean_text(position.get("entry_date")) or None,
        target_weight=_finite(position.get("target_weight")),
        unrealized_return=unrealized_return,
        close=close,
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        rsi14=rsi14,
        return_1d=return_1d,
        rank_20d=rank_20d,
        recommendation_20d=recommendation,
        in_signal_pool=in_signal_pool,
    )


def _render_section(title: str, items: list[PositionDecision]) -> list[str]:
    lines = [f"{title}（n={len(items)}）"]
    if not items:
        lines.append("無")
        return lines
    for item in items:
        lines.append(f"{item.ticker}: {'，'.join(item.reasons)}")
    return lines


def _render_html_section(title: str, items: list[PositionDecision], color: str) -> str:
    if not items:
        body = "<p>無</p>"
    else:
        rows = []
        for item in items:
            reasons = html.escape("，".join(item.reasons))
            rows.append(f"<li><strong>{html.escape(item.ticker)}</strong>: {reasons}</li>")
        body = "<ul>" + "".join(rows) + "</ul>"
    return (
        f"<section style='border-left:4px solid {color};padding:8px 12px;margin:12px 0;'>"
        f"<h3 style='margin:0 0 8px 0;'>{html.escape(title)}（n={len(items)}）</h3>{body}</section>"
    )


def _render_report(
    as_of: date,
    signal_date: date,
    signal_path: Path,
    db_latest_date: date,
    decisions: list[PositionDecision],
) -> tuple[str, str, dict[str, int], list[str]]:
    red = [item for item in decisions if item.category == "red"]
    yellow = [item for item in decisions if item.category == "yellow"]
    green = [item for item in decisions if item.category == "green"]
    guardrails = sorted(
        {reason for item in decisions for reason in item.guardrail_reasons}
    )
    counts = {"red": len(red), "yellow": len(yellow), "green": len(green), "total": len(decisions)}

    lines = [f"Champion 今日人工降部位提醒 — {as_of.isoformat()}", ""]
    lines.extend(_render_section("🔴 建議降部位", red))
    lines.append("")
    lines.extend(_render_section("🟡 觀察", yellow))
    lines.append("")
    lines.extend(_render_section("🟢 保留", green))
    lines.append("")
    lines.append("底部備註：")
    lines.append("- 系統不會自動執行此建議，僅提醒")
    lines.append(f"- Hard guardrail 觸發明細：{'; '.join(guardrails) if guardrails else '無'}")
    lines.append(f"- 資料截止日：{signal_date.isoformat()}")
    lines.append(f"- Signal source：{signal_path.name}")
    lines.append(f"- Model era：{MODEL_ERA} (Stage1 cleanup incident window)")
    text = "\n".join(lines)

    html_body = f"""
<html><body style="font-family:'Noto Sans TC','Microsoft JhengHei',Arial,sans-serif;line-height:1.55;">
<h2>Champion 今日人工降部位提醒 — {html.escape(as_of.isoformat())}</h2>
{_render_html_section("🔴 建議降部位", red, "#dc2626")}
{_render_html_section("🟡 觀察", yellow, "#d97706")}
{_render_html_section("🟢 保留", green, "#16a34a")}
<hr>
<p><strong>底部備註：</strong></p>
<ul>
  <li>系統不會自動執行此建議，僅提醒。</li>
  <li>Hard guardrail 觸發明細：{html.escape('; '.join(guardrails) if guardrails else '無')}</li>
  <li>資料截止日：{html.escape(signal_date.isoformat())}</li>
  <li>Signal source：{html.escape(signal_path.name)}</li>
  <li>Model era：<strong>{html.escape(MODEL_ERA)}</strong> (Stage1 cleanup incident window)</li>
</ul>
</body></html>
""".strip()
    return text, html_body, counts, guardrails


def build_report(as_of: date, db_path: Path) -> RiskCutReport:
    latest_signal = _latest_signal_path(as_of)
    if latest_signal is None:
        raise FileNotFoundError(f"No unified_signals file on or before {as_of}")
    signal_date, signal_path = latest_signal
    signal_df = _load_signals(signal_path)
    signals = _signal_map(signal_df)

    with _open_db(db_path) as conn:
        latest_db = _latest_db_activity_date(conn)
        if latest_db is None:
            raise RuntimeError("Champion paper DB has no activity date.")
        positions = _load_open_positions(conn)

    decisions: list[PositionDecision] = []
    for position in positions:
        ticker = _ticker(position.get("ticker"))
        snapshot = _load_daily_k_snapshot(ticker, signal_date)
        decisions.append(classify_position(position, signals.get(ticker), snapshot))

    text, html_body, counts, guardrails = _render_report(
        as_of=as_of,
        signal_date=signal_date,
        signal_path=signal_path,
        db_latest_date=latest_db,
        decisions=decisions,
    )
    return RiskCutReport(
        as_of_date=as_of.isoformat(),
        signal_date=signal_date.isoformat(),
        signal_source=str(signal_path),
        db_latest_date=latest_db.isoformat(),
        counts=counts,
        decisions=decisions,
        hard_guardrail_details=guardrails,
        text=text,
        html=html_body,
    )


def _write_outputs(report: RiskCutReport) -> tuple[Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of_date.replace("-", "")
    log_path = LOG_DIR / f"position_risk_cut_{stamp}.log"
    json_path = LOG_DIR / f"position_risk_cut_{stamp}.json"
    log_path.write_text(report.text + "\n", encoding="utf-8")
    payload = asdict(report)
    payload.pop("html", None)
    payload.pop("text", None)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log_path, json_path


def _write_skip_log(day: date, reason: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = day.isoformat().replace("-", "")
    path = LOG_DIR / f"position_risk_cut_{stamp}.log"
    path.write_text(f"[position-risk-cut] skipped: {reason} ({day.isoformat()})\n", encoding="utf-8")
    return path


def _send_report(report: RiskCutReport, to_email: str) -> bool:
    settings = load_email_settings()
    if settings is None:
        print("[position-risk-cut] email skipped: SMTP settings missing")
        return False
    settings.to_emails = [to_email]
    subject = f"[Champion] 今日人工降部位提醒 {report.as_of_date}"
    send_email(settings, subject, report.html)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send Champion daily position risk-cut reminder.")
    parser.add_argument("--as-of-date", help="Report date, defaults to today.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Champion paper DB path.")
    parser.add_argument("--to", default=DEFAULT_TO_EMAIL, help="Recipient email address.")
    parser.add_argument("--force", action="store_true", help="Bypass trading-day and freshness skip checks.")
    parser.add_argument("--dry-run", action="store_true", help="Write outputs and print report, but do not send email.")
    args = parser.parse_args(argv)

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    requested_day = _parse_date(args.as_of_date)
    run_day = requested_day or date.today()
    if not args.force and not is_taiwan_trading_day(run_day):
        path = _write_skip_log(run_day, "non-trading day")
        print(f"[position-risk-cut] skipped: non-trading day log={path}")
        return 0

    latest_signal = _latest_signal_path(run_day)
    if latest_signal is None:
        path = _write_skip_log(run_day, "unified signals missing")
        print(f"[position-risk-cut] skipped: signals_missing log={path}")
        return 0
    signal_date, _signal_path = latest_signal

    db_path = Path(args.db_path)
    with _open_db(db_path) as conn:
        latest_db = _latest_db_activity_date(conn)
    if latest_db is None:
        path = _write_skip_log(run_day, "paper portfolio DB has no activity date")
        print(f"[position-risk-cut] skipped: db_empty log={path}")
        return 0
    if not args.force:
        required_db_date = previous_taiwan_trading_day(run_day)
        if signal_date < required_db_date or latest_db < required_db_date:
            path = _write_skip_log(
                run_day,
                f"phase-2 incomplete; latest_signal={signal_date}, latest_db={latest_db}, required>={required_db_date}",
            )
            print(
                "[position-risk-cut] skipped: phase2_incomplete "
                f"latest_signal={signal_date} latest_db={latest_db} required>={required_db_date} log={path}"
            )
            return 0

    report = build_report(run_day, db_path)
    log_path, json_path = _write_outputs(report)
    print(report.text)
    print(f"\n[position-risk-cut] wrote log={log_path}")
    print(f"[position-risk-cut] wrote json={json_path}")

    if args.dry_run:
        print("[position-risk-cut] dry-run: email not sent")
        return 0

    sent = _send_report(report, args.to)
    print(f"[position-risk-cut] email_sent={sent} to={args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
