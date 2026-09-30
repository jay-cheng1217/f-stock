"""Send the daily Champion health report.

The report is intentionally light-weight: it reads today's already-generated
artifacts and the paper book, writes a local log, and sends one UTF-8 email.
It does not rebuild predictions, replay positions, or modify production data.
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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import INDEX_DIR, MODEL_DIR
from ml.market_regime import evaluate_market_regime
from scripts.send_daily_email import load_email_settings, send_email
from scripts.taiwan_trading_calendar import is_taiwan_trading_day, previous_taiwan_trading_day


DEFAULT_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_TO_EMAIL = "jaycheng821217@gmail.com"
LOG_DIR = BASE_DIR / "logs"
UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
TWII_FILE = Path(INDEX_DIR) / "index_TWII.csv"
MARKET_INDEX_FILES = {
    "TWII": "index_TWII.csv",
    "S&P500": "index_GSPC.csv",
    "SOX": "index_SOX.csv",
    "VIX": "index_VIX.csv",
}


@dataclass
class HealthReport:
    as_of_date: str
    status: str
    status_label: str
    suggestion: str
    text: str
    html: str
    data: dict[str, Any]


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _pct(value: Any, digits: int = 2) -> str:
    numeric = _finite(value)
    if numeric is None:
        return "n/a"
    return f"{numeric * 100:.{digits}f}%"


def _num(value: Any, digits: int = 2) -> str:
    numeric = _finite(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:.{digits}f}"


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


def _load_twii() -> pd.DataFrame:
    if not TWII_FILE.exists():
        raise FileNotFoundError(f"TWII file not found: {TWII_FILE}")
    return _load_index_file(TWII_FILE)


def _load_index_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Index file not found: {path}")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    df["MA20"] = df["Close"].rolling(20, min_periods=20).mean()
    df["MA60"] = df["Close"].rolling(60, min_periods=60).mean()
    df["daily_return"] = df["Close"] / df["Close"].shift(1) - 1.0
    return df


def _describe_price_index_return(value: float | None) -> str:
    """Describe a price-index daily move from its numeric return only."""
    numeric = _finite(value)
    if numeric is None:
        return "資料不足"
    if numeric > 0.01:
        return "強勢"
    if numeric >= -0.01:
        return "平盤整理"
    if numeric >= -0.03:
        return "偏弱"
    return "顯著下跌"


def _describe_vix_return(value: float | None) -> str:
    """Describe VIX by volatility pressure, never by a positive market template."""
    numeric = _finite(value)
    if numeric is None:
        return "資料不足"
    if numeric > 0.05:
        return "波動顯著升溫"
    if numeric > 0.01:
        return "波動升溫"
    if numeric >= -0.01:
        return "波動持平"
    if numeric >= -0.05:
        return "波動降溫"
    return "波動顯著降溫"


def _market_index_snapshot(label: str, as_of: date) -> dict[str, Any]:
    path = Path(INDEX_DIR) / MARKET_INDEX_FILES[label]
    df = _load_index_file(path)
    row = _twii_row(df, as_of)
    close = _finite(row.get("Close"))
    daily_return = _finite(row.get("daily_return"))
    ma20 = _finite(row.get("MA20"))
    ma60 = _finite(row.get("MA60"))
    if label == "VIX":
        description = _describe_vix_return(daily_return)
    else:
        description = _describe_price_index_return(daily_return)
    return {
        "label": label,
        "date": _date_str(pd.Timestamp(row.get("Date"))),
        "close": close,
        "daily_return": daily_return,
        "ma20": ma20,
        "ma60": ma60,
        "vs_ma20": close / ma20 - 1.0 if close is not None and ma20 else None,
        "vs_ma60": close / ma60 - 1.0 if close is not None and ma60 else None,
        "description": description,
    }


def _market_snapshots(as_of: date) -> dict[str, dict[str, Any]]:
    return {label: _market_index_snapshot(label, as_of) for label in MARKET_INDEX_FILES}


def _resolve_as_of_date(twii: pd.DataFrame, requested: date | None) -> date:
    if requested:
        subset = twii[twii["Date"].dt.date <= requested]
    else:
        today = date.today()
        subset = twii[twii["Date"].dt.date <= today]
    if subset.empty:
        raise ValueError("No TWII data available for requested date.")
    return pd.Timestamp(subset.iloc[-1]["Date"]).date()


def _twii_row(twii: pd.DataFrame, as_of: date) -> pd.Series:
    subset = twii[twii["Date"].dt.date <= as_of]
    if subset.empty:
        raise ValueError(f"No TWII data on or before {as_of}")
    return subset.iloc[-1]


def _twii_return_between(twii: pd.DataFrame, start: date, end: date) -> float | None:
    end_subset = twii[twii["Date"].dt.date <= end]
    start_subset = twii[twii["Date"].dt.date <= start]
    if end_subset.empty or start_subset.empty:
        return None
    end_close = _finite(end_subset.iloc[-1]["Close"])
    start_close = _finite(start_subset.iloc[-1]["Close"])
    if end_close is None or start_close is None or start_close <= 0:
        return None
    return end_close / start_close - 1.0


def _latest_signal_path(as_of: date) -> Path | None:
    candidates: list[tuple[date, Path]] = []
    for path in Path(MODEL_DIR).glob("unified_signals_*.csv"):
        match = UNIFIED_SIGNAL_RE.match(path.name)
        if not match:
            continue
        signal_date = pd.to_datetime(match.group(1), errors="coerce")
        if pd.isna(signal_date):
            continue
        day = signal_date.date()
        if day <= as_of:
            candidates.append((day, path))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _load_signal_df(as_of: date) -> tuple[Path | None, pd.DataFrame]:
    path = _latest_signal_path(as_of)
    if path is None:
        return None, pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    return path, df


def _open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Champion DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _latest_db_activity_date(conn: sqlite3.Connection) -> date | None:
    values: list[str] = []
    queries = [
        "SELECT MAX(prediction_date) AS d FROM unified_runs",
        "SELECT MAX(entry_date) AS d FROM unified_positions",
        "SELECT MAX(exit_date) AS d FROM unified_positions",
        "SELECT MAX(mark_date) AS d FROM unified_marks",
    ]
    for sql in queries:
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


def _run_stats(conn: sqlite3.Connection, as_of: date) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT prediction_date, total_candidates, total_units, new_positions, upgraded_positions, cash_deployed
        FROM unified_runs
        WHERE prediction_date <= ?
        ORDER BY prediction_date DESC, id DESC
        LIMIT 1
        """,
        (_date_str(as_of),),
    ).fetchone()
    return dict(row) if row else {}


def _exit_reason_counts(conn: sqlite3.Connection, as_of: date) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT COALESCE(exit_reason, 'UNKNOWN') AS reason, COUNT(*) AS n
        FROM unified_positions
        WHERE exit_date = ?
        GROUP BY COALESCE(exit_reason, 'UNKNOWN')
        ORDER BY n DESC
        """,
        (_date_str(as_of),),
    ).fetchall()
    return {str(row["reason"]): int(row["n"]) for row in rows}


def _latest_marks(conn: sqlite3.Connection, as_of: date) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT p.id AS position_id, p.ticker, p.status, p.target_weight, p.entry_date,
               p.exit_date, p.realized_return_pct, m.mark_date, m.close_return_pct
        FROM unified_positions p
        JOIN (
            SELECT position_id, MAX(mark_date) AS mark_date
            FROM unified_marks
            WHERE mark_date <= ?
            GROUP BY position_id
        ) lm ON lm.position_id = p.id
        JOIN unified_marks m
          ON m.position_id = lm.position_id AND m.mark_date = lm.mark_date
        WHERE p.status = 'open'
        """,
        conn,
        params=(_date_str(as_of),),
    )
    for col in ("target_weight", "close_return_pct"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _portfolio_stats(conn: sqlite3.Connection, as_of: date) -> dict[str, Any]:
    open_count = int(
        conn.execute("SELECT COUNT(*) AS n FROM unified_positions WHERE status = 'open'").fetchone()["n"]
    )
    pending_count = int(
        conn.execute("SELECT COUNT(*) AS n FROM unified_positions WHERE status = 'pending'").fetchone()["n"]
    )
    marks = _latest_marks(conn, as_of)
    weighted_mtm = None
    max_loss: dict[str, Any] | None = None
    if not marks.empty:
        valid = marks.dropna(subset=["target_weight", "close_return_pct"])
        if not valid.empty:
            weighted_mtm = float((valid["target_weight"] * valid["close_return_pct"]).sum())
            worst = valid.sort_values("close_return_pct").iloc[0]
            max_loss = {
                "ticker": str(worst["ticker"]),
                "return_pct": float(worst["close_return_pct"]),
                "mark_date": str(worst["mark_date"]),
            }
    return {
        "open_count": open_count,
        "pending_count": pending_count,
        "weighted_mtm": weighted_mtm,
        "max_loss": max_loss,
    }


def _signal_quality(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "stage1_median": None,
            "price_vs_ma5_median": None,
            "rank_top10_count": 0,
            "rank_11_30_count": 0,
            "active_count": 0,
            "source_score_column": None,
        }

    working = df.copy()
    if "target_units" in working.columns:
        working["target_units"] = pd.to_numeric(working["target_units"], errors="coerce").fillna(0)
        active = working[working["target_units"] > 0].copy()
    else:
        active = pd.DataFrame()
    if active.empty:
        active = working.copy()

    score_column = None
    for candidate in ("stage1_score", "pred_return_20d", "risk_adjusted_return", "prob_edge"):
        if candidate in active.columns:
            score_column = candidate
            break
    stage1_median = None
    if score_column is not None:
        stage1_median = _finite(pd.to_numeric(active[score_column], errors="coerce").median())

    price_vs_ma5_median = None
    if "price_vs_ma5" in active.columns:
        price_vs_ma5_median = _finite(pd.to_numeric(active["price_vs_ma5"], errors="coerce").median())

    rank_column = "rank_20d"
    if "two_stage_rank" in active.columns:
        two_stage_ranks = pd.to_numeric(active["two_stage_rank"], errors="coerce")
        # Some historical/replay signal files carry the two_stage_rank column but
        # leave most values blank. For the health report, use the complete 20D
        # rank instead of showing a misleading 0/1 split.
        if int(two_stage_ranks.notna().sum()) >= max(1, int(len(active) * 0.8)):
            rank_column = "two_stage_rank"
    ranks = pd.to_numeric(active.get(rank_column), errors="coerce") if rank_column in active.columns else pd.Series(dtype=float)
    return {
        "stage1_median": stage1_median,
        "price_vs_ma5_median": price_vs_ma5_median,
        "rank_top10_count": int(((ranks >= 1) & (ranks <= 10)).sum()),
        "rank_11_30_count": int(((ranks >= 11) & (ranks <= 30)).sum()),
        "active_count": int(len(active)),
        "source_score_column": score_column,
        "rank_column": rank_column,
    }


def _last_closed_positions(conn: sqlite3.Connection, limit: int = 20) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT ticker, exit_date, exit_reason, realized_return_pct, target_weight
        FROM unified_positions
        WHERE status IN ('closed', 'stopped_out')
          AND realized_return_pct IS NOT NULL
        ORDER BY COALESCE(exit_date, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        conn,
        params=(limit,),
    )
    if not df.empty:
        df["realized_return_pct"] = pd.to_numeric(df["realized_return_pct"], errors="coerce")
        df["target_weight"] = pd.to_numeric(df["target_weight"], errors="coerce")
    return df


def _weekly_alpha(conn: sqlite3.Connection, twii: pd.DataFrame, as_of: date) -> dict[str, Any]:
    week_start = as_of - timedelta(days=as_of.weekday())
    while not is_taiwan_trading_day(week_start):
        week_start += timedelta(days=1)
    realized = pd.read_sql_query(
        """
        SELECT ticker, target_weight, realized_return_pct
        FROM unified_positions
        WHERE exit_date >= ? AND exit_date <= ? AND realized_return_pct IS NOT NULL
        """,
        conn,
        params=(_date_str(week_start), _date_str(as_of)),
    )
    for col in ("target_weight", "realized_return_pct"):
        if col in realized.columns:
            realized[col] = pd.to_numeric(realized[col], errors="coerce")

    marks = _latest_marks(conn, as_of)
    portfolio_ret = 0.0
    if not realized.empty:
        portfolio_ret += float((realized["target_weight"] * realized["realized_return_pct"]).sum())
    if not marks.empty:
        valid = marks.dropna(subset=["target_weight", "close_return_pct"])
        if not valid.empty:
            portfolio_ret += float((valid["target_weight"] * valid["close_return_pct"]).sum())

    twii_ret = _twii_return_between(twii, week_start, as_of)
    alpha = portfolio_ret - twii_ret if twii_ret is not None else None
    return {
        "week_start": _date_str(week_start),
        "portfolio_return": portfolio_ret,
        "twii_return": twii_ret,
        "alpha": alpha,
    }


def _recent_status_streak(current_status: str, as_of: date) -> int:
    streak = 1
    for path in sorted(LOG_DIR.glob("daily_health_*.json"), reverse=True):
        match = re.search(r"daily_health_(\d{8})\.json$", path.name)
        if not match:
            continue
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        if day >= as_of:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == current_status:
            streak += 1
        else:
            break
    return streak


def _classify_status(last20_win_rate: float | None, stage1_median: float | None, as_of: date) -> tuple[str, str, str]:
    red = (
        (last20_win_rate is not None and last20_win_rate < 0.30)
        or (stage1_median is not None and stage1_median < 0.040)
    )
    warn = (
        (last20_win_rate is not None and 0.30 <= last20_win_rate < 0.35)
        or (stage1_median is not None and 0.040 <= stage1_median < 0.050)
    )
    normal = (
        (last20_win_rate is not None and last20_win_rate >= 0.35)
        and (stage1_median is not None and stage1_median >= 0.050)
    )
    if red:
        status = "red"
        label = "🔴 警示"
    elif warn or not normal:
        status = "yellow"
        label = "⚠️ 注意觀察"
    else:
        status = "green"
        label = "✅ 系統正常"

    streak = _recent_status_streak(status, as_of)
    if status == "red" and streak >= 10:
        suggestion = "🔴 持續 ≥ 2 週：維持現行 pin，等待 regime 改變或 OOS 成熟樣本，不開新修復票。"
    elif status == "red":
        suggestion = "🔴 單週/短期警示：先維持現行 pin，持續觀察，不調整。"
    elif status == "yellow":
        suggestion = "⚠️ 單週：持續觀察，不調整。"
    elif streak >= 10:
        suggestion = "✅ 連續 2 週 ≥ 35%：系統恢復，可考慮重新評估模型 retrain。"
    else:
        suggestion = "✅ 系統正常：維持現行設定。"
    return status, label, suggestion


def _rows_to_text(rows: list[tuple[str, str]]) -> str:
    width = max((len(left) for left, _ in rows), default=0)
    return "\n".join(f"{left:<{width}}  {right}" for left, right in rows)


def _html_table(rows: list[tuple[str, str]]) -> str:
    body = "\n".join(
        f"<tr><th>{html.escape(left)}</th><td>{html.escape(right)}</td></tr>"
        for left, right in rows
    )
    return f"<table>{body}</table>"


def _render_report(
    *,
    as_of: date,
    portfolio_as_of: date,
    market_info: dict[str, dict[str, Any]],
    regime: dict[str, Any],
    run_stats: dict[str, Any],
    exit_counts: dict[str, int],
    portfolio: dict[str, Any],
    signal: dict[str, Any],
    rolling: dict[str, Any],
    weekly: dict[str, Any],
    status_label: str,
    suggestion: str,
    signal_path: Path | None,
) -> tuple[str, str, dict[str, Any]]:
    exits = ", ".join(f"{reason}: {count}" for reason, count in exit_counts.items()) or "0"
    max_loss = portfolio.get("max_loss") or {}
    max_loss_text = (
        f"{max_loss.get('ticker')} ({_pct(max_loss.get('return_pct'))}, mark {max_loss.get('mark_date')})"
        if max_loss
        else "n/a"
    )
    top_ratio = f"{signal['rank_top10_count']} / {signal['rank_11_30_count']} (Top10 / 11-30)"
    source_score = signal.get("source_score_column") or "n/a"
    signal_source = signal_path.name if signal_path else "n/a"
    twii_info = market_info["TWII"]

    def _market_line(label: str) -> str:
        item = market_info[label]
        return (
            f"{_num(item['close'])} ({_pct(item['daily_return'])})｜"
            f"{item['description']}｜資料日 {item['date']}"
        )

    blocks: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "Block 1｜市場面",
            [
                ("TWII 收盤", _market_line("TWII")),
                ("S&P500 收盤", _market_line("S&P500")),
                ("SOX 收盤", _market_line("SOX")),
                ("VIX 收盤", _market_line("VIX")),
                ("目前 regime", f"{regime.get('state', 'n/a')} / {regime.get('action', 'n/a')}"),
                ("TWII vs MA20", _pct(twii_info["vs_ma20"])),
                ("TWII vs MA60", _pct(twii_info["vs_ma60"])),
            ],
        ),
        (
            "Block 2｜Champion 組合面",
            [
                ("Champion 資料日", _date_str(portfolio_as_of)),
                ("新進場筆數（資料日）", str(run_stats.get("new_positions", 0))),
                ("出場筆數 + 原因（資料日）", exits),
                ("目前持倉數 / pending", f"{portfolio['open_count']} / {portfolio['pending_count']}"),
                ("整體 MTM %", _pct(portfolio.get("weighted_mtm"))),
                ("最大單筆虧損 ticker（資料日）", max_loss_text),
            ],
        ),
        (
            "Block 3｜訊號品質面",
            [
                ("Stage1 score 中位數（訊號資料日）", f"{_num(signal['stage1_median'], 4)} ({source_score})"),
                ("price_vs_ma5 分布中位數（訊號資料日）", _pct(signal["price_vs_ma5_median"])),
                ("Top10 vs 11–30 佔比（訊號資料日）", top_ratio),
                ("signals source", signal_source),
            ],
        ),
        (
            "Block 4｜Rolling 績效追蹤",
            [
                ("近 20 筆已平倉勝率", _pct(rolling["win_rate"])),
                ("近 20 筆平均報酬", _pct(rolling["avg_return"])),
                ("本週累積 alpha（vs TWII）", _pct(weekly["alpha"])),
                ("本週區間（Champion 資料日）", f"{weekly['week_start']} → {_date_str(portfolio_as_of)}"),
            ],
        ),
        (
            "Block 5｜今日結論",
            [("狀態", status_label)],
        ),
        (
            "Block 6｜條件 / 模型調整建議",
            [("建議", suggestion)],
        ),
    ]

    text_parts = [f"每日健康報告 — {_date_str(as_of)}"]
    for title, rows in blocks:
        text_parts.append("")
        text_parts.append(title)
        text_parts.append(_rows_to_text(rows))
    text = "\n".join(text_parts)

    html_sections = "\n".join(
        f"<section><h2>{html.escape(title)}</h2>{_html_table(rows)}</section>"
        for title, rows in blocks
    )
    html_body = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: "Microsoft JhengHei", Arial, sans-serif; color: #17202a; line-height: 1.45; }}
    h1 {{ font-size: 22px; margin: 0 0 14px; }}
    h2 {{ font-size: 17px; margin: 18px 0 8px; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 860px; }}
    th, td {{ border: 1px solid #d7dee8; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ width: 230px; background: #f5f7fa; font-weight: 700; }}
    .status {{ display: inline-block; padding: 6px 10px; border-radius: 6px; background: #fff3cd; }}
    footer {{ margin-top: 18px; color: #64748b; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>每日健康報告 — {html.escape(_date_str(as_of))}</h1>
  <p class="status">{html.escape(status_label)}</p>
  {html_sections}
  <footer>此報告只讀取既有 artifacts，不重跑模型、不改 production 資料。</footer>
</body>
</html>"""

    data = {
        "as_of_date": _date_str(as_of),
        "portfolio_as_of_date": _date_str(portfolio_as_of),
        "market": market_info,
        "twii": twii_info,
        "regime": regime,
        "run_stats": run_stats,
        "exit_counts": exit_counts,
        "portfolio": portfolio,
        "signal_quality": signal,
        "rolling": rolling,
        "weekly": weekly,
        "status_label": status_label,
        "suggestion": suggestion,
        "signal_path": str(signal_path) if signal_path else None,
    }
    return text, html_body, data


def build_report(as_of: date, db_path: Path, portfolio_as_of: date | None = None) -> HealthReport:
    portfolio_day = portfolio_as_of or as_of
    twii = _load_twii()
    market_info = _market_snapshots(as_of)
    regime = evaluate_market_regime(_date_str(as_of))

    signal_path, signal_df = _load_signal_df(portfolio_day)
    signal = _signal_quality(signal_df)

    with _open_db(db_path) as conn:
        run_stats = _run_stats(conn, portfolio_day)
        exit_counts = _exit_reason_counts(conn, portfolio_day)
        portfolio = _portfolio_stats(conn, portfolio_day)
        closed = _last_closed_positions(conn, limit=20)
        win_rate = None
        avg_return = None
        if not closed.empty:
            returns = closed["realized_return_pct"].dropna()
            if not returns.empty:
                win_rate = float((returns > 0).mean())
                avg_return = float(returns.mean())
        rolling = {
            "sample_count": int(len(closed)),
            "win_rate": win_rate,
            "avg_return": avg_return,
        }
        weekly = _weekly_alpha(conn, twii, portfolio_day)

    status, status_label, suggestion = _classify_status(
        rolling["win_rate"], signal["stage1_median"], as_of
    )
    text, html_body, data = _render_report(
        as_of=as_of,
        portfolio_as_of=portfolio_day,
        market_info=market_info,
        regime=regime,
        run_stats=run_stats,
        exit_counts=exit_counts,
        portfolio=portfolio,
        signal=signal,
        rolling=rolling,
        weekly=weekly,
        status_label=status_label,
        suggestion=suggestion,
        signal_path=signal_path,
    )
    data["status"] = status
    return HealthReport(
        as_of_date=_date_str(as_of),
        status=status,
        status_label=status_label,
        suggestion=suggestion,
        text=text,
        html=html_body,
        data=data,
    )


def _write_outputs(report: HealthReport) -> tuple[Path, Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of_date.replace("-", "")
    log_path = LOG_DIR / f"daily_health_{stamp}.log"
    json_path = LOG_DIR / f"daily_health_{stamp}.json"
    html_path = LOG_DIR / f"daily_health_{stamp}.html"
    log_path.write_text(report.text + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(report.data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(report.html, encoding="utf-8")
    return log_path, json_path, html_path


def _write_skip_log(as_of: date, reason: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _date_str(as_of).replace("-", "")
    path = LOG_DIR / f"daily_health_{stamp}.log"
    message = f"[daily-health] skipped: {reason} ({_date_str(as_of)})"
    path.write_text(message + "\n", encoding="utf-8")
    return path


def _send_report(report: HealthReport, to_email: str) -> bool:
    settings = load_email_settings()
    if settings is None:
        print("[daily-health] email skipped: SMTP settings missing")
        return False
    settings.to_emails = [to_email]
    subject = f"{settings.subject_prefix} Daily Health {report.as_of_date} {report.status_label}"
    send_email(settings, subject, report.html)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send Champion daily health report.")
    parser.add_argument("--as-of-date", help="Report date, defaults to latest TWII date <= today.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Champion paper DB path.")
    parser.add_argument("--to", default=DEFAULT_TO_EMAIL, help="Recipient email address.")
    parser.add_argument("--force", action="store_true", help="Bypass non-trading / stale DB skip checks.")
    parser.add_argument("--dry-run", action="store_true", help="Write logs and print report, but do not send email.")
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
        print(f"[daily-health] skipped: non-trading day log={path}")
        return 0

    twii = _load_twii()
    as_of = _resolve_as_of_date(twii, requested_day)
    if not args.force and requested_day is None and as_of < run_day:
        path = _write_skip_log(run_day, f"market data not updated; latest_twii={as_of}")
        print(f"[daily-health] skipped: market_data_not_updated latest_twii={as_of} log={path}")
        return 0

    db_path = Path(args.db_path)
    with _open_db(db_path) as conn:
        latest_db_date = _latest_db_activity_date(conn)
    if latest_db_date is None:
        path = _write_skip_log(as_of, "paper portfolio DB has no activity date")
        print(f"[daily-health] skipped: db_empty log={path}")
        return 0
    portfolio_as_of = min(latest_db_date, as_of)
    if not args.force:
        required_db_date = previous_taiwan_trading_day(as_of)
        if latest_db_date < required_db_date:
            path = _write_skip_log(
                as_of,
                f"paper portfolio DB too stale; latest={latest_db_date}, required>={required_db_date}",
            )
            print(
                "[daily-health] skipped: db_too_stale "
                f"latest={latest_db_date} required>={required_db_date} log={path}"
            )
            return 0

    report = build_report(as_of, db_path, portfolio_as_of=portfolio_as_of)
    log_path, json_path, html_path = _write_outputs(report)
    print(report.text)
    print(f"\n[daily-health] wrote log={log_path}")
    print(f"[daily-health] wrote json={json_path}")
    print(f"[daily-health] wrote html={html_path}")

    if args.dry_run:
        print("[daily-health] dry-run: email not sent")
        return 0

    sent = _send_report(report, args.to)
    print(f"[daily-health] email_sent={sent} to={args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
