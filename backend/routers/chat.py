from __future__ import annotations

import glob
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.db.engine import query_df
from backend.routers import unified
from backend.services.warroom_service import (
    build_dashboard_summary,
    build_portfolio_champion_vs_shadow,
    build_unified_signals_latest_feed,
)
from backend.services.tw_news_intel_service import build_ticker_news_intel
from ml.config import MODEL_DIR, REPORT_DIR

router = APIRouter(prefix="/api/chat", tags=["chat"])

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "gemma4")
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_CHAT_TIMEOUT_SECONDS", "120"))
MAX_TOOL_ROWS = 12
MAX_TOOL_ROUNDS = 6
MAX_HISTORY_MESSAGES = 8

PRED20_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
PRED_T1_RE = re.compile(r"^predictions_t1_(\d{4}-\d{2}-\d{2})\.csv$")
MONITOR_T1_REPORT = os.path.join(REPORT_DIR, "monitor_t1_latest.json")
BACKTEST_20D_REPORT = os.path.join(REPORT_DIR, "backtest_latest.json")
BACKTEST_T1_REPORT = os.path.join(REPORT_DIR, "t1_backtest_latest.json")
PREDICTION_TRACKING_REPORT = os.path.join(REPORT_DIR, "prediction_tracking.json")

SYSTEM_PROMPT = """你是 Unified Portfolio 的研究助理。

你的工作是根據工具回傳的真實資料，用繁體中文整理答案。

規則:
1. 只根據工具資料回答，不要編造不存在的數字或檔案。
2. 使用者問到系統現況、部位、信號、股票、回測、監控時，先呼叫工具再回答。
3. 如果股票代號或名稱不明確，先用 search_stock。
4. 回答要精簡、直接，優先給結論，再補 2-4 個關鍵點。
5. 如果資料不足，明確說缺什麼。
6. 回答最後加一行「來源：...」，列出你實際用到的資料來源名稱。

可用工具:
- search_stock: 搜尋股票代號/名稱
- get_stock_snapshot: 個股快照（預測+訊號+持倉）
- get_latest_unified_signals: 最新統一訊號
- get_unified_portfolio: 統一帳本持倉
- get_monitor_summary: T+1 監控摘要
- get_backtest_summary: 回測結果
- get_stock_revenue: 個股月營收（營收金額、YoY）
- get_stock_financials: 個股季報財務（毛利率、營業利益率、淨利率）
- get_stock_price: 個股近期日K（價量、法人買賣超、融資券餘額、技術指標）
- get_stock_tdcc: 個股集保股權分散（散戶%、大戶%、持有人數趨勢）
- get_market_indices: 大盤/國際指數（TWII、VIX、費半SOX、S&P500、美元台幣）
- get_stock_news: 個股 MOPS 重大訊息公告
- get_stock_valuation: 個股最新估值（PE、PB、殖利率）

使用者問營收、財報、利潤率、估值、法人買賣超、融資券、集保、大盤、新聞等問題時，請用對應工具查詢。
使用者問個股分析時，建議組合多個工具（snapshot + revenue + financials + valuation + price）給出全面回答。
"""


COPILOT_SYSTEM_PROMPT = """
You are Two-Stage Champion Copilot, a local assistant for a Taiwan equities quant trading war room.

Operating rules:
1. Read the injected war room context before answering.
2. If the injected context is not enough, use available tools to inspect more data.
3. Stay faithful to the data. If something is missing, say so clearly.
4. Focus on actionable analysis: attribution, entry gates, exit risks, holdings, and ticker diagnostics.
5. Prefer the active contract:
   - Entry: Penalty Overlay V1
   - Champion Ranking: Two-Stage N=75 LambdaRank
   - Champion Exit: asymmetric_v2
   - Shadow Exit: baseline_with_ma20
6. Quote concrete numbers, tickers, penalty reasons, and exit reasons when available.
7. Use get_stock_news_intel when the user asks about news, catalysts, risk events, or whether recent headlines affect entry timing.
8. Answer in the user's language when possible.
"""

COPILOT_SUGGESTED_QUESTIONS = [
    "總結今天 Champion 跟 Shadow 的績效差異。",
    "幫我檢查明天前五大建倉名單，有沒有產業過度集中或扣分過重。",
    "有哪些持倉已經逼近 MA5 或 HWM 防線？",
    "為什麼某檔股票今天被 Hard Block 擋掉？",
]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatQueryRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=200)


@dataclass
class ToolResult:
    payload: dict[str, Any]
    sources: list[str] = field(default_factory=list)


def _latest_matching_file(pattern: re.Pattern[str]) -> str | None:
    candidates = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "*.csv")))
        if pattern.match(os.path.basename(path))
    ]
    return candidates[-1] if candidates else None


def _safe_float(value: Any, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _safe_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _trim_records(df: pd.DataFrame, columns: list[str], limit: int) -> list[dict[str, Any]]:
    if df.empty:
        return []
    limited = df.loc[:, [column for column in columns if column in df.columns]].head(limit).copy()
    return json.loads(limited.to_json(orient="records", force_ascii=False))


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_limit(limit: int | None, default: int = MAX_TOOL_ROWS) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_TOOL_ROWS))


def _load_warroom_bundle() -> dict[str, Any]:
    try:
        dashboard = build_dashboard_summary()
    except Exception as exc:
        dashboard = {"status": "error", "error": str(exc)}
    try:
        portfolio = build_portfolio_champion_vs_shadow()
    except Exception as exc:
        portfolio = {"status": "error", "error": str(exc)}
    try:
        signals = build_unified_signals_latest_feed()
    except Exception as exc:
        signals = {"status": "error", "error": str(exc)}
    return {
        "dashboard": dashboard,
        "portfolio": portfolio,
        "signals": signals,
    }


def _pct_text(value: Any, digits: int = 2) -> str:
    numeric = _safe_float(value, digits)
    if numeric is None:
        return "-"
    return f"{numeric:+.{digits}f}%"


def _value_text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "-"


def _top_rows(rows: list[dict[str, Any]] | None, limit: int = 5) -> list[dict[str, Any]]:
    if not rows:
        return []
    return rows[: max(1, limit)]


def _format_position_lines(rows: list[dict[str, Any]] | None, limit: int = 5) -> str:
    items = []
    for row in _top_rows(rows, limit=limit):
        items.append(
            "- {ticker} {name} | pnl {pnl} | ma5 {ma5} | hwm {hwm} | risk {risk}".format(
                ticker=_value_text(row.get("ticker")),
                name=_value_text(row.get("name")),
                pnl=_pct_text(row.get("unrealized_pnl_pct")),
                ma5=_pct_text(row.get("ma5_distance_pct")),
                hwm=_pct_text(row.get("drawdown_from_high_pct")),
                risk=_value_text(row.get("risk_state")),
            )
        )
    return "\n".join(items) if items else "- none"


def _format_exit_lines(rows: list[dict[str, Any]] | None, limit: int = 5) -> str:
    items = []
    for row in _top_rows(rows, limit=limit):
        items.append(
            "- {date} | {ticker} {name} | {reason} | realized {ret}".format(
                date=_value_text(row.get("exit_date")),
                ticker=_value_text(row.get("ticker")),
                name=_value_text(row.get("name")),
                reason=_value_text(row.get("exit_reason")),
                ret=_pct_text(row.get("realized_return_pct")),
            )
        )
    return "\n".join(items) if items else "- none"


def _format_signal_lines(rows: list[dict[str, Any]] | None, limit: int = 5) -> str:
    items = []
    for row in _top_rows(rows, limit=limit):
        foreign_penalty = -(_safe_float(row.get("penalty_foreign_selling"), 2) or 0)
        gap_penalty = -(_safe_float(row.get("penalty_gap_risk"), 2) or 0)
        beta_penalty = -(_safe_float(row.get("penalty_beta"), 2) or 0)
        items.append(
            "- {ticker} {name} | sector {sector} | base {base} | net {net} | foreign {foreign} | gap {gap} | beta {beta}".format(
                ticker=_value_text(row.get("ticker")),
                name=_value_text(row.get("name")),
                sector=_value_text(row.get("sector")),
                base=_pct_text(row.get("base_score")),
                net=_pct_text(row.get("net_score")),
                foreign=_pct_text(foreign_penalty),
                gap=_pct_text(gap_penalty),
                beta=_pct_text(beta_penalty),
            )
        )
    return "\n".join(items) if items else "- none"


def _format_hard_block_lines(rows: list[dict[str, Any]] | None, limit: int = 5) -> str:
    items = []
    for row in _top_rows(rows, limit=limit):
        items.append(
            "- {ticker} {name} | base {base} | net {net} | reason {reason}".format(
                ticker=_value_text(row.get("ticker")),
                name=_value_text(row.get("name")),
                base=_pct_text(row.get("base_score")),
                net=_pct_text(row.get("net_score")),
                reason=_value_text(row.get("penalty_overlay_reason") or row.get("risk_tags")),
            )
        )
    return "\n".join(items) if items else "- none"


def _build_proactive_alerts(bundle: dict[str, Any]) -> list[dict[str, str]]:
    dashboard = bundle.get("dashboard") or {}
    portfolio = bundle.get("portfolio") or {}
    signals = bundle.get("signals") or {}
    champion_summary = ((portfolio.get("champion") or {}).get("summary") or {})
    shadow_summary = ((portfolio.get("shadow") or {}).get("summary") or {})
    champion_active = ((portfolio.get("champion") or {}).get("active_positions") or [])
    selected = signals.get("selected") or []

    alerts: list[dict[str, str]] = []
    red_rows = [row for row in champion_active if str(row.get("risk_state") or "").lower() == "red"]
    yellow_rows = [row for row in champion_active if str(row.get("risk_state") or "").lower() == "yellow"]
    if red_rows:
        tickers = ", ".join(str(row.get("ticker")) for row in red_rows[:5])
        alerts.append(
            {
                "level": "red",
                "title": "防線已觸發",
                "message": f"目前有 {len(red_rows)} 檔 Champion 持倉已達紅燈區，包含 {tickers}。",
            }
        )
    elif len(yellow_rows) >= 3:
        tickers = ", ".join(str(row.get("ticker")) for row in yellow_rows[:5])
        alerts.append(
            {
                "level": "yellow",
                "title": "逼近防線",
                "message": f"目前有 {len(yellow_rows)} 檔 Champion 持倉逼近 MA5 或 HWM，包含 {tickers}。",
            }
        )

    if selected:
        sectors: dict[str, int] = {}
        for row in selected[:5]:
            sector = _value_text(row.get("sector"))
            sectors[sector] = sectors.get(sector, 0) + 1
        crowded = [f"{sector} x{count}" for sector, count in sectors.items() if sector != "-" and count >= 2]
        if crowded:
            alerts.append(
                {
                    "level": "yellow",
                    "title": "候選名單集中",
                    "message": f"前五大候選名單有產業集中跡象：{', '.join(crowded)}。",
                }
            )

    champion_alpha = _safe_float(champion_summary.get("mtm_alpha_vs_twii_pct"), 2)
    shadow_alpha = _safe_float(shadow_summary.get("mtm_alpha_vs_twii_pct"), 2)
    if champion_alpha is not None and shadow_alpha is not None and champion_alpha - shadow_alpha >= 2.0:
        alerts.append(
            {
                "level": "green",
                "title": "Champion 領先 Shadow",
                "message": (
                    f"目前 Champion alpha {champion_alpha:+.2f}% 明顯優於 Shadow {shadow_alpha:+.2f}% 。"
                ),
            }
        )

    if not alerts:
        alerts.append(
            {
                "level": "blue",
                "title": "系統狀態正常",
                "message": "目前沒有明顯異常，Copilot 已準備好回答持倉、名單與歸因問題。",
            }
        )
    return alerts


def _build_copilot_context(bundle: dict[str, Any]) -> str:
    dashboard = bundle.get("dashboard") or {}
    portfolio = bundle.get("portfolio") or {}
    signals = bundle.get("signals") or {}
    champion_summary = ((portfolio.get("champion") or {}).get("summary") or {})
    shadow_summary = ((portfolio.get("shadow") or {}).get("summary") or {})
    market = dashboard.get("market") or {}
    regime = dashboard.get("market_regime") or {}
    champion_active = ((portfolio.get("champion") or {}).get("active_positions") or [])
    champion_exits = ((portfolio.get("champion") or {}).get("recent_exits") or [])
    shadow_active = ((portfolio.get("shadow") or {}).get("active_positions") or [])
    opportunity_cost = portfolio.get("opportunity_cost") or []
    selected = signals.get("selected") or []
    hard_blocks = signals.get("hard_blocks") or []

    return (
        "Current Two-Stage Champion war room context\n"
        f"- as_of_date: {_value_text(dashboard.get('as_of_date'))}\n"
        f"- prediction_date: {_value_text(signals.get('prediction_date'))}\n"
        f"- market regime: {_value_text(regime.get('state'))} / {_value_text(regime.get('action'))}\n"
        f"- TWII return: {_pct_text(market.get('twii_return_pct'))}\n"
        f"- Champion MTM return: {_pct_text(champion_summary.get('mtm_return_pct'))}\n"
        f"- Champion alpha: {_pct_text(champion_summary.get('mtm_alpha_vs_twii_pct'))}\n"
        f"- Champion win rate: {_pct_text(champion_summary.get('realized_win_rate_pct'))}\n"
        f"- Champion max drawdown: {_pct_text(champion_summary.get('max_drawdown_pct'))}\n"
        f"- Champion turnover: {_value_text(champion_summary.get('capital_turnover_20d_multiple'))}x\n"
        f"- Shadow MTM return: {_pct_text(shadow_summary.get('mtm_return_pct'))}\n"
        f"- Shadow alpha: {_pct_text(shadow_summary.get('mtm_alpha_vs_twii_pct'))}\n"
        f"- Shadow win rate: {_pct_text(shadow_summary.get('realized_win_rate_pct'))}\n"
        f"- Shadow max drawdown: {_pct_text(shadow_summary.get('max_drawdown_pct'))}\n"
        "\nChampion active positions (top 5)\n"
        f"{_format_position_lines(champion_active, limit=5)}\n"
        "\nChampion recent exits (top 5)\n"
        f"{_format_exit_lines(champion_exits, limit=5)}\n"
        "\nShadow active positions (top 5)\n"
        f"{_format_position_lines(shadow_active, limit=5)}\n"
        "\nEntry candidates (top 5)\n"
        f"{_format_signal_lines(selected, limit=5)}\n"
        "\nHard blocks (top 5)\n"
        f"{_format_hard_block_lines(hard_blocks, limit=5)}\n"
        "\nOpportunity cost cases (top 5)\n"
        f"{_format_exit_lines(opportunity_cost, limit=5)}\n"
    )


def _build_chat_briefing() -> dict[str, Any]:
    bundle = _load_warroom_bundle()
    dashboard = bundle.get("dashboard") or {}
    portfolio = bundle.get("portfolio") or {}
    signals = bundle.get("signals") or {}
    champion_summary = ((portfolio.get("champion") or {}).get("summary") or {})
    shadow_summary = ((portfolio.get("shadow") or {}).get("summary") or {})
    return {
        "status": "success",
        "mode": "v2_champion_copilot",
        "as_of_date": dashboard.get("as_of_date"),
        "prediction_date": signals.get("prediction_date"),
        "summary": {
            "champion_alpha_pct": champion_summary.get("mtm_alpha_vs_twii_pct"),
            "shadow_alpha_pct": shadow_summary.get("mtm_alpha_vs_twii_pct"),
            "champion_win_rate_pct": champion_summary.get("realized_win_rate_pct"),
            "shadow_win_rate_pct": shadow_summary.get("realized_win_rate_pct"),
            "champion_turnover_multiple": champion_summary.get("capital_turnover_20d_multiple"),
            "shadow_turnover_multiple": shadow_summary.get("capital_turnover_20d_multiple"),
        },
        "alerts": _build_proactive_alerts(bundle),
        "suggested_questions": COPILOT_SUGGESTED_QUESTIONS,
    }


def _tool_search_stock(keyword: str, limit: int = 8) -> ToolResult:
    keyword = str(keyword or "").strip()
    if not keyword:
        return ToolResult({"keyword": "", "count": 0, "rows": []}, sources=["stock_list"])

    limit_value = _normalize_limit(limit)
    like_keyword = f"%{keyword}%"
    df = query_df(
        f"""
        SELECT
            Ticker,
            Name,
            Last_Close,
            Last_Change_Pct,
            Last_Volume,
            Last_Date
        FROM stock_list
        WHERE Ticker LIKE ? OR Name LIKE ?
        ORDER BY
            CASE WHEN Ticker = ? THEN 0 ELSE 1 END,
            Ticker
        LIMIT {limit_value}
        """,
        [like_keyword, like_keyword, keyword],
    )
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "ticker": str(row.get("Ticker", "")),
                "name": str(row.get("Name", "")),
                "last_close": _safe_float(row.get("Last_Close"), 2),
                "last_change_pct": _safe_float(row.get("Last_Change_Pct"), 2),
                "last_volume": _safe_int(row.get("Last_Volume")),
                "last_date": str(row.get("Last_Date", "")) or None,
            }
        )
    return ToolResult(
        payload={"keyword": keyword, "count": len(rows), "rows": rows},
        sources=["stock_list"],
    )


def _tool_get_latest_unified_signals(
    signal_type: str | None = None,
    ticker: str | None = None,
    limit: int = 10,
) -> ToolResult:
    normalized_signal_type = unified._normalize_signal_type(signal_type)
    df, signal_path = unified._load_latest_unified_signals_df()
    working = df.copy()
    if normalized_signal_type:
        working = working[working["signal_type"] == normalized_signal_type].copy()
    if ticker:
        working = working[working["ticker"].astype(str) == str(ticker).strip()].copy()

    rows = _trim_records(
        working,
        [
            "prediction_date",
            "ticker",
            "name",
            "sector",
            "signal_type",
            "target_units",
            "target_weight_pct",
            "rank_20d",
            "rank_t1",
            "hit_prob_3pct",
            "pred_return_20d",
            "recommendation_20d",
            "recommendation_t1",
            "setup_tags_t1",
            "close_ref",
        ],
        _normalize_limit(limit),
    )
    return ToolResult(
        payload={
            "prediction_date": (
                str(df["prediction_date"].iloc[0])
                if not df.empty and "prediction_date" in df.columns
                else None
            ),
            "source_file": os.path.basename(signal_path),
            "total": int(len(df)),
            "matched_count": int(len(working)),
            "signal_counts": unified._signal_type_counts(df["signal_type"] if "signal_type" in df.columns else None),
            "filters": {"signal_type": normalized_signal_type, "ticker": ticker or None},
            "rows": rows,
        },
        sources=[os.path.basename(signal_path)],
    )


def _tool_get_unified_portfolio(
    status: str | None = None,
    signal_type: str | None = None,
    ticker: str | None = None,
    limit: int = 10,
) -> ToolResult:
    normalized_status = unified._parse_status_filter(status)
    normalized_signal_type = unified._normalize_signal_type(signal_type)
    df, summary = unified._load_unified_portfolio_df(
        status_filter=normalized_status,
        signal_type=normalized_signal_type,
    )
    working = df.copy()
    if ticker:
        working = working[working["ticker"].astype(str) == str(ticker).strip()].copy()

    rows = _trim_records(
        working,
        [
            "prediction_date",
            "last_signal_date",
            "ticker",
            "name",
            "sector",
            "signal_type",
            "entry_signal_type",
            "status",
            "target_units",
            "target_weight_pct",
            "planned_entry_ref_price",
            "entry_date",
            "entry_price",
            "latest_mark_date",
            "latest_price",
            "return_pct",
            "max_drawdown_pct",
            "exit_policy",
            "hold_days_target",
            "days_observed",
            "upgrade_count",
        ],
        _normalize_limit(limit),
    )
    return ToolResult(
        payload={
            "summary": summary,
            "matched_count": int(len(working)),
            "filters": {
                "status": normalized_status or [],
                "signal_type": normalized_signal_type,
                "ticker": ticker or None,
            },
            "rows": rows,
        },
        sources=[os.path.basename(unified.UNIFIED_PORTFOLIO_DB_PATH)],
    )


def _tool_get_stock_snapshot(ticker: str) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")

    stock_df = query_df(
        """
        SELECT
            Ticker,
            Name,
            Last_Close,
            Last_Change_Pct,
            Last_Volume,
            Last_Date
        FROM stock_list
        WHERE Ticker = ?
        LIMIT 1
        """,
        [ticker],
    )

    latest_20d_path = _latest_matching_file(PRED20_RE)
    latest_t1_path = _latest_matching_file(PRED_T1_RE)
    latest_signal_path = unified._latest_unified_signal_path()

    latest_20d_row: dict[str, Any] | None = None
    if latest_20d_path:
        pred20_df = pd.read_csv(latest_20d_path, encoding="utf-8-sig", dtype={"ticker": str})
        match = pred20_df[pred20_df["ticker"].astype(str) == ticker]
        if not match.empty:
            row = match.iloc[0]
            latest_20d_row = {
                "prediction_date": str(row.get("date", "")) or None,
                "signal": str(row.get("signal", "")) or None,
                "pred_return_20d": _safe_float(row.get("pred_return_20d")),
                "recommendation": str(row.get("recommendation", "")) or None,
                "prob_edge": _safe_float(row.get("prob_edge")),
                "up_prob": _safe_float(row.get("up_prob")),
                "flat_prob": _safe_float(row.get("flat_prob")),
                "down_prob": _safe_float(row.get("down_prob")),
                "close": _safe_float(row.get("close"), 2),
            }

    latest_t1_row: dict[str, Any] | None = None
    if latest_t1_path:
        pred_t1_df = pd.read_csv(latest_t1_path, encoding="utf-8-sig", dtype={"ticker": str})
        match = pred_t1_df[pred_t1_df["ticker"].astype(str) == ticker]
        if not match.empty:
            row = match.iloc[0]
            latest_t1_row = {
                "prediction_date": str(row.get("date", "")) or None,
                "recommendation": str(row.get("recommendation", "")) or None,
                "selected_for_trade": bool(row.get("selected_for_trade", False)),
                "selection_rank": _safe_int(row.get("selection_rank")),
                "hit_prob_3pct": _safe_float(row.get("hit_prob_3pct")),
                "close": _safe_float(row.get("close"), 2),
                "setup_tags": str(row.get("setup_tags", "")) or None,
                "risk_tags": str(row.get("risk_tags", "")) or None,
            }

    latest_unified_row: dict[str, Any] | None = None
    if latest_signal_path:
        signal_df = pd.read_csv(latest_signal_path, encoding="utf-8-sig", dtype={"ticker": str})
        match = signal_df[signal_df["ticker"].astype(str) == ticker]
        if not match.empty:
            row = match.iloc[0]
            latest_unified_row = {
                "prediction_date": str(row.get("prediction_date", "")) or None,
                "signal_type": str(row.get("signal_type", "")) or None,
                "target_units": _safe_int(row.get("target_units")),
                "target_weight_ratio": _safe_float(row.get("target_weight_ratio")),
                "rank_20d": _safe_int(row.get("rank_20d")),
                "rank_t1": _safe_int(row.get("rank_t1")),
                "close_ref": _safe_float(row.get("close_ref"), 2),
            }

    portfolio_df, _ = unified._load_unified_portfolio_df(status_filter=None, signal_type=None)
    portfolio_match = portfolio_df[portfolio_df["ticker"].astype(str) == ticker].head(5).copy()
    portfolio_rows = _trim_records(
        portfolio_match,
        [
            "prediction_date",
            "ticker",
            "signal_type",
            "status",
            "target_units",
            "target_weight_pct",
            "entry_date",
            "entry_price",
            "latest_mark_date",
            "latest_price",
            "return_pct",
            "max_drawdown_pct",
            "upgrade_count",
        ],
        5,
    )

    stock_info = None
    if not stock_df.empty:
        row = stock_df.iloc[0]
        stock_info = {
            "ticker": str(row.get("Ticker", "")),
            "name": str(row.get("Name", "")),
            "last_close": _safe_float(row.get("Last_Close"), 2),
            "last_change_pct": _safe_float(row.get("Last_Change_Pct"), 2),
            "last_volume": _safe_int(row.get("Last_Volume")),
            "last_date": str(row.get("Last_Date", "")) or None,
        }

    sources = ["stock_list", os.path.basename(unified.UNIFIED_PORTFOLIO_DB_PATH)]
    if latest_20d_path:
        sources.append(os.path.basename(latest_20d_path))
    if latest_t1_path:
        sources.append(os.path.basename(latest_t1_path))
    if latest_signal_path:
        sources.append(os.path.basename(latest_signal_path))

    return ToolResult(
        payload={
            "ticker": ticker,
            "stock": stock_info,
            "latest_20d": latest_20d_row,
            "latest_t1": latest_t1_row,
            "latest_unified": latest_unified_row,
            "portfolio_rows": portfolio_rows,
        },
        sources=sources,
    )


def _tool_get_monitor_summary() -> ToolResult:
    if not os.path.exists(MONITOR_T1_REPORT):
        return ToolResult(
            payload={"status": "missing", "message": "monitor_t1_latest.json not found"},
            sources=[os.path.basename(MONITOR_T1_REPORT)],
        )
    report = _load_json(MONITOR_T1_REPORT)
    anomalies = report.get("anomalies", []) or []
    summary = report.get("summary", {}) or {}
    portfolio = report.get("portfolio", {}) or {}
    execution_gap = portfolio.get("execution_gap", {}) or {}
    return ToolResult(
        payload={
            "status": "ready",
            "generated_at": report.get("generated_at"),
            "summary": summary,
            "portfolio": {
                "overall_win_rate": portfolio.get("overall_win_rate"),
                "mdd": portfolio.get("mdd"),
                "equity_latest": portfolio.get("equity_latest"),
                "total_positions": portfolio.get("total_positions"),
                "execution_gap": execution_gap,
            },
            "anomaly_count": len(anomalies),
            "top_anomalies": anomalies[:5],
        },
        sources=[os.path.basename(MONITOR_T1_REPORT)],
    )


def _tool_get_backtest_summary(kind: str = "all") -> ToolResult:
    selected = str(kind or "all").strip().lower()
    payload: dict[str, Any] = {"kind": selected}
    sources: list[str] = []

    if selected in {"all", "20d"} and os.path.exists(BACKTEST_20D_REPORT):
        report = _load_json(BACKTEST_20D_REPORT)
        payload["backtest_20d"] = report.get("overall", {})
        sources.append(os.path.basename(BACKTEST_20D_REPORT))

    if selected in {"all", "t1"} and os.path.exists(BACKTEST_T1_REPORT):
        report = _load_json(BACKTEST_T1_REPORT)
        payload["backtest_t1"] = report.get("overall", {})
        sources.append(os.path.basename(BACKTEST_T1_REPORT))

    if selected in {"all", "tracking"} and os.path.exists(PREDICTION_TRACKING_REPORT):
        report = _load_json(PREDICTION_TRACKING_REPORT)
        payload["prediction_tracking"] = report.get("summary", {})
        sources.append(os.path.basename(PREDICTION_TRACKING_REPORT))

    if len(payload) == 1:
        payload["message"] = "No backtest report matched the request."

    return ToolResult(payload=payload, sources=sources)


def _tool_get_stock_revenue(ticker: str, limit: int = 12) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    limit_value = _normalize_limit(limit, default=12)
    df = query_df(
        """
        SELECT Ticker, Date, Name, Monthly_Revenue, YoY_pct_change,
               Cumulative_Revenue, Cumulative_YoY_pct_change
        FROM revenue
        WHERE Ticker = ?
        ORDER BY Date DESC
        LIMIT ?
        """,
        [ticker, limit_value],
    )
    rows = json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []
    return ToolResult(
        payload={"ticker": ticker, "count": len(rows), "rows": rows},
        sources=["revenue"],
    )


def _tool_get_stock_financials(ticker: str, limit: int = 8) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    limit_value = _normalize_limit(limit, default=8)
    df = query_df(
        """
        SELECT Ticker, Name, Year, Season, Revenue_M,
               Gross_Margin_Pct, Operating_Margin_Pct,
               Pretax_Margin_Pct, Net_Margin_Pct
        FROM financials
        WHERE CAST(Ticker AS VARCHAR) = ?
        ORDER BY Year DESC, Season DESC
        LIMIT ?
        """,
        [ticker, limit_value],
    )
    rows = json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []
    return ToolResult(
        payload={"ticker": ticker, "count": len(rows), "rows": rows},
        sources=["financials"],
    )


def _tool_get_stock_price(ticker: str, limit: int = 10) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    limit_value = _normalize_limit(limit, default=10)
    df = query_df(
        """
        SELECT Ticker, Date, Open, High, Low, Close, Volume,
               Foreign_BuySell, Trust_BuySell, Dealer_BuySell,
               Margin_Balance, Short_Balance,
               MA_5, MA_20, MA_60, RSI_14
        FROM daily_k
        WHERE Ticker = ?
        ORDER BY Date DESC
        LIMIT ?
        """,
        [ticker, limit_value],
    )
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append({
            "date": str(row.get("Date", "")),
            "open": _safe_float(row.get("Open"), 2),
            "high": _safe_float(row.get("High"), 2),
            "low": _safe_float(row.get("Low"), 2),
            "close": _safe_float(row.get("Close"), 2),
            "volume": _safe_int(row.get("Volume")),
            "foreign_buysell": _safe_float(row.get("Foreign_BuySell"), 0),
            "trust_buysell": _safe_float(row.get("Trust_BuySell"), 0),
            "dealer_buysell": _safe_float(row.get("Dealer_BuySell"), 0),
            "margin_balance": _safe_float(row.get("Margin_Balance"), 0),
            "short_balance": _safe_float(row.get("Short_Balance"), 0),
            "ma5": _safe_float(row.get("MA_5"), 2),
            "ma20": _safe_float(row.get("MA_20"), 2),
            "ma60": _safe_float(row.get("MA_60"), 2),
            "rsi14": _safe_float(row.get("RSI_14"), 2),
        })
    return ToolResult(
        payload={"ticker": ticker, "count": len(rows), "rows": rows},
        sources=["daily_k"],
    )


def _tool_get_stock_tdcc(ticker: str, limit: int = 12) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    limit_value = _normalize_limit(limit, default=12)
    df = query_df(
        """
        SELECT Date, Ticker, Retail_Pct, Whale_Pct, Total_Holders
        FROM tdcc
        WHERE Ticker = ?
        ORDER BY Date DESC
        LIMIT ?
        """,
        [ticker, limit_value],
    )
    rows = json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []
    return ToolResult(
        payload={"ticker": ticker, "count": len(rows), "rows": rows},
        sources=["tdcc"],
    )


def _tool_get_market_indices(index_name: str | None = None, limit: int = 10) -> ToolResult:
    limit_value = _normalize_limit(limit, default=10)
    if index_name:
        name = str(index_name).strip().upper()
        df = query_df(
            """
            SELECT Index_Name, Date, Open, High, Low, Close, Volume
            FROM indices
            WHERE Index_Name = ?
            ORDER BY Date DESC
            LIMIT ?
            """,
            [name, limit_value],
        )
    else:
        df = query_df(
            """
            SELECT Index_Name, Date, Close
            FROM indices
            WHERE Date = (SELECT MAX(Date) FROM indices)
            ORDER BY Index_Name
            """,
        )
    rows = json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []
    return ToolResult(
        payload={
            "index_name": index_name,
            "count": len(rows),
            "available_indices": ["TWII", "VIX", "SOX", "GSPC", "USDTWDX"],
            "rows": rows,
        },
        sources=["indices"],
    )


NEWS_DIR = os.path.join(REPO_ROOT, "新聞資料")
VALUATION_DIR = os.path.join(REPO_ROOT, "估值資料")


def _tool_get_stock_news(ticker: str, limit: int = 10) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    limit_value = _normalize_limit(limit, default=10)
    csv_files = sorted(glob.glob(os.path.join(NEWS_DIR, "announcements_*.csv")), reverse=True)
    all_rows: list[dict[str, Any]] = []
    for csv_path in csv_files:
        if len(all_rows) >= limit_value:
            break
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"Ticker": str})
            matched = df[df["Ticker"].astype(str) == ticker].copy()
            if matched.empty:
                continue
            matched = matched.sort_values("Date", ascending=False).head(limit_value - len(all_rows))
            for _, row in matched.iterrows():
                all_rows.append({
                    "date": str(row.get("Date", "")),
                    "time": str(row.get("Time", "")),
                    "title": str(row.get("Title", "")),
                    "market": str(row.get("Market", "")),
                })
        except Exception:
            continue
    return ToolResult(
        payload={"ticker": ticker, "count": len(all_rows), "rows": all_rows[:limit_value]},
        sources=["新聞資料"],
    )


def _tool_get_stock_news_intel(ticker: str, name: str | None = None, limit: int = 6, days: int = 7) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    payload = build_ticker_news_intel(
        ticker=ticker,
        name=name,
        limit=_normalize_limit(limit, default=6),
        days=max(1, min(int(days or 7), 30)),
        include_web=True,
    )
    report = payload.get("report") or {}
    compact_items = []
    for item in report.get("items") or []:
        compact_items.append(
            {
                "risk_level": item.get("risk_level"),
                "dimension": item.get("dimension_label") or item.get("dimension"),
                "title": item.get("title"),
                "source": item.get("source") or item.get("provider"),
                "published_date": item.get("published_date"),
                "relevance_score": item.get("relevance_score"),
                "sentiment": item.get("sentiment"),
                "url": item.get("url"),
            }
        )
    return ToolResult(
        payload={
            "ticker": report.get("ticker"),
            "name": report.get("name"),
            "verdict": report.get("verdict"),
            "risk_counts": report.get("risk_counts"),
            "web_search_status": report.get("web_search_status"),
            "items": compact_items,
        },
        sources=["MOPS", "tw_news_intel"],
    )


def _tool_get_stock_valuation(ticker: str) -> ToolResult:
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    csv_files = sorted(glob.glob(os.path.join(VALUATION_DIR, "valuation_*.csv")), reverse=True)
    for csv_path in csv_files[:5]:
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"Ticker": str})
            matched = df[df["Ticker"].astype(str) == ticker]
            if matched.empty:
                continue
            row = matched.iloc[0]
            valuation_date = os.path.basename(csv_path).replace("valuation_", "").replace(".csv", "")
            return ToolResult(
                payload={
                    "ticker": ticker,
                    "date": f"{valuation_date[:4]}-{valuation_date[4:6]}-{valuation_date[6:]}",
                    "pe_ratio": _safe_float(row.get("PE_Ratio"), 2),
                    "pb_ratio": _safe_float(row.get("PB_Ratio"), 2),
                    "dividend_yield": _safe_float(row.get("Dividend_Yield"), 2),
                },
                sources=["估值資料"],
            )
        except Exception:
            continue
    return ToolResult(
        payload={"ticker": ticker, "message": "No valuation data found."},
        sources=["估值資料"],
    )


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "search_stock",
            "description": "Search stock tickers or names in the current stock universe.",
            "parameters": {
                "type": "object",
                "required": ["keyword"],
                "properties": {
                    "keyword": {"type": "string", "description": "Ticker or stock name keyword."},
                    "limit": {"type": "integer", "description": "Maximum rows to return."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_unified_signals",
            "description": "Read the latest unified signal artifact and return matching rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_type": {
                        "type": "string",
                        "description": "Optional filter: Dual, 20D_only, or T1_only.",
                    },
                    "ticker": {"type": "string", "description": "Optional exact ticker filter."},
                    "limit": {"type": "integer", "description": "Maximum rows to return."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_unified_portfolio",
            "description": "Read the unified portfolio ledger and return matching positions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional status filter such as ACTIVE, OPEN, CLOSED, pending, open, closed, stopped_out.",
                    },
                    "signal_type": {
                        "type": "string",
                        "description": "Optional signal type filter: Dual, 20D_only, or T1_only.",
                    },
                    "ticker": {"type": "string", "description": "Optional exact ticker filter."},
                    "limit": {"type": "integer", "description": "Maximum rows to return."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_snapshot",
            "description": "Get the latest stock snapshot across stock list, 20D prediction, T+1 prediction, unified signals, and unified portfolio.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, for example 2330."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_monitor_summary",
            "description": "Read the latest T+1 monitor summary and top anomalies.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_backtest_summary",
            "description": "Read the latest 20D and T+1 backtest summaries plus prediction tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Optional filter: all, 20d, t1, or tracking.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_revenue",
            "description": "Query monthly revenue data for a stock from DuckDB. Returns recent months of revenue with YoY growth.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "limit": {"type": "integer", "description": "Number of recent months to return (default 12)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_financials",
            "description": "Query quarterly financial report data for a stock from DuckDB. Returns gross margin, operating margin, net margin by quarter.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "limit": {"type": "integer", "description": "Number of recent quarters to return (default 8)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Query recent daily price, volume, institutional flows, margin data, and key technical indicators for a stock from DuckDB.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "limit": {"type": "integer", "description": "Number of recent trading days to return (default 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_tdcc",
            "description": "Query TDCC shareholder distribution data for a stock. Shows retail vs whale holding percentages and total holder counts over recent weeks.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "limit": {"type": "integer", "description": "Number of recent weeks to return (default 12)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_indices",
            "description": "Query market indices data (TWII, VIX, SOX, GSPC, USDTWDX). Without index_name returns the latest snapshot of all indices; with index_name returns recent daily history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index_name": {"type": "string", "description": "Optional: TWII, VIX, SOX, GSPC, or USDTWDX."},
                    "limit": {"type": "integer", "description": "Number of recent days to return (default 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_news",
            "description": "Query recent MOPS major announcements for a specific stock. Data comes from daily-crawled MOPS bulletin.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "limit": {"type": "integer", "description": "Number of recent announcements to return (default 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_news_intel",
            "description": "Query Taiwan-stock news intelligence with MOPS plus optional web-search fallback, freshness filtering, relevance scoring, and risk flags. This is informational only and does not alter trading gates.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                    "name": {"type": "string", "description": "Optional stock name."},
                    "limit": {"type": "integer", "description": "Maximum intelligence items to return."},
                    "days": {"type": "integer", "description": "Freshness window in days, default 7."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_valuation",
            "description": "Get the latest PE ratio, PB ratio, and dividend yield for a stock from valuation CSV files.",
            "parameters": {
                "type": "object",
                "required": ["ticker"],
                "properties": {
                    "ticker": {"type": "string", "description": "Exact stock ticker, e.g. 2330."},
                },
            },
        },
    },
]


TOOL_HANDLERS = {
    "search_stock": _tool_search_stock,
    "get_latest_unified_signals": _tool_get_latest_unified_signals,
    "get_unified_portfolio": _tool_get_unified_portfolio,
    "get_stock_snapshot": _tool_get_stock_snapshot,
    "get_monitor_summary": _tool_get_monitor_summary,
    "get_backtest_summary": _tool_get_backtest_summary,
    "get_stock_revenue": _tool_get_stock_revenue,
    "get_stock_financials": _tool_get_stock_financials,
    "get_stock_price": _tool_get_stock_price,
    "get_stock_tdcc": _tool_get_stock_tdcc,
    "get_market_indices": _tool_get_market_indices,
    "get_stock_news": _tool_get_stock_news,
    "get_stock_news_intel": _tool_get_stock_news_intel,
    "get_stock_valuation": _tool_get_stock_valuation,
}


def _sanitize_history(history: list[ChatMessage]) -> list[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for item in history[-MAX_HISTORY_MESSAGES:]:
        content = str(item.content or "").strip()
        if not content:
            continue
        sanitized.append({"role": item.role, "content": content})
    return sanitized


def _parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        text = raw_args.strip()
        if not text:
            return {}
        return json.loads(text)
    return dict(raw_args)


def _ollama_chat_once(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "tools": TOOL_DEFS,
        "options": {
            "temperature": 0.2,
        },
    }
    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_chat_agent(request_body: ChatQueryRequest) -> dict[str, Any]:
    model = (request_body.model or OLLAMA_DEFAULT_MODEL).strip() or OLLAMA_DEFAULT_MODEL
    context_bundle = _load_warroom_bundle()
    context_prompt = _build_copilot_context(context_bundle)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": COPILOT_SYSTEM_PROMPT},
        {"role": "system", "content": context_prompt},
    ]
    messages.extend(_sanitize_history(request_body.history))
    messages.append({"role": "user", "content": request_body.message.strip()})

    used_sources: list[str] = []
    tool_trace: list[dict[str, Any]] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = _ollama_chat_once(messages, model=model)
        assistant_message = response.get("message") or {}
        assistant_content = str(assistant_message.get("content") or "").strip()
        tool_calls = assistant_message.get("tool_calls") or []

        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            }
        )

        if not tool_calls:
            unique_sources = []
            for source in used_sources:
                if source not in unique_sources:
                    unique_sources.append(source)
            if not assistant_content:
                assistant_content = "目前沒有可整理的回答。"
            if unique_sources and "來源：" not in assistant_content:
                assistant_content = f"{assistant_content.rstrip()}\n來源：{'、'.join(unique_sources)}"
            return {
                "status": "success",
                "answer": assistant_content,
                "sources": unique_sources,
                "model": model,
                "tool_calls": tool_trace,
            }

        for call in tool_calls:
            fn_info = call.get("function") or {}
            fn_name = str(fn_info.get("name") or "").strip()
            handler = TOOL_HANDLERS.get(fn_name)
            if handler is None:
                result_payload = {"error": f"Unknown tool: {fn_name}"}
                tool_sources: list[str] = []
            else:
                args = _parse_tool_args(fn_info.get("arguments"))
                result = handler(**args)
                result_payload = result.payload
                tool_sources = result.sources
                used_sources.extend(tool_sources)

            tool_trace.append(
                {
                    "tool_name": fn_name,
                    "arguments": _parse_tool_args(fn_info.get("arguments")),
                    "sources": tool_sources,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_name": fn_name,
                    "content": json.dumps(result_payload, ensure_ascii=False),
                }
            )

    raise RuntimeError("Tool loop exceeded the maximum number of rounds.")


@router.get("/health")
def chat_health():
    return {
        "status": "success",
        "mode": "v2_champion_copilot",
        "ollama_base_url": OLLAMA_BASE_URL,
        "default_model": OLLAMA_DEFAULT_MODEL,
        "tools": list(TOOL_HANDLERS.keys()),
    }


@router.get("/briefing")
def chat_briefing():
    try:
        return JSONResponse(content=_build_chat_briefing())
    except Exception as exc:
        return JSONResponse(
            content={
                "status": "error",
                "mode": "v2_champion_copilot",
                "error": str(exc),
                "alerts": [],
                "suggested_questions": COPILOT_SUGGESTED_QUESTIONS,
            },
            status_code=500,
        )


@router.post("/query")
def chat_query(body: ChatQueryRequest):
    try:
        result = _run_chat_agent(body)
        return JSONResponse(content=result)
    except urllib.error.URLError as exc:
        return JSONResponse(
            content={
                "status": "error",
                "error": f"Cannot reach Ollama at {OLLAMA_BASE_URL}: {exc.reason}",
                "answer": "",
                "sources": [],
            },
            status_code=503,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return JSONResponse(
            content={
                "status": "error",
                "error": f"Ollama returned HTTP {exc.code}: {detail}",
                "answer": "",
                "sources": [],
            },
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse(
            content={
                "status": "error",
                "error": str(exc),
                "answer": "",
                "sources": [],
            },
            status_code=500,
        )
