"""Send the daily ML leaderboard and paper portfolio summary by email."""

from __future__ import annotations

import argparse
import glob
import html
import os
import re
import smtplib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

import pandas as pd

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR
from ml.predict import apply_sector_cap, _sort_prediction_df
from scripts.update_paper_portfolio import (
    DEFAULT_DB_PATH,
    connect_db,
    summarize_ledger,
)

DEFAULT_EMAIL_TOP_N = 30
DEFAULT_PORTFOLIO_LIMIT = 12
DEFAULT_SUBJECT_PREFIX = "[Stock ML]"
DEFAULT_PREVIEW_PATH = os.path.join(BASE_DIR, "logs", "daily_email_preview.html")


@dataclass
class EmailSettings:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    to_emails: list[str]
    use_ssl: bool
    from_name: str = "Stock ML Bot"
    subject_prefix: str = DEFAULT_SUBJECT_PREFIX
    top_n: int = DEFAULT_EMAIL_TOP_N
    portfolio_limit: int = DEFAULT_PORTFOLIO_LIMIT


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            values[key] = value
    return values


def _env_value(key: str, env_map: dict[str, str], default: str = "") -> str:
    value = os.environ.get(key)
    if value is not None and value != "":
        return value
    return env_map.get(key, default)


def _as_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_email_settings() -> EmailSettings | None:
    env_map: dict[str, str] = {}
    for path in (os.path.join(BASE_DIR, ".env.email"), os.path.join(BASE_DIR, ".env")):
        env_map.update(_parse_env_file(path))

    from_email = _env_value("SMTP_FROM", env_map)
    to_value = _env_value("SMTP_TO", env_map)
    user = _env_value("SMTP_USER", env_map, from_email)
    password = _env_value("SMTP_PASSWORD", env_map)

    if not (from_email and to_value and user and password):
        return None

    host = _env_value("SMTP_HOST", env_map, "smtp.gmail.com")
    port = int(_env_value("SMTP_PORT", env_map, "465"))
    use_ssl = _as_bool(_env_value("SMTP_USE_SSL", env_map, "true"), default=True)
    from_name = _env_value("SMTP_FROM_NAME", env_map, "Stock ML Bot")
    subject_prefix = _env_value("EMAIL_SUBJECT_PREFIX", env_map, DEFAULT_SUBJECT_PREFIX)
    top_n = max(DEFAULT_EMAIL_TOP_N, int(_env_value("EMAIL_TOP_N", env_map, str(DEFAULT_EMAIL_TOP_N))))
    portfolio_limit = int(
        _env_value("EMAIL_PORTFOLIO_LIMIT", env_map, str(DEFAULT_PORTFOLIO_LIMIT))
    )

    to_emails = [item.strip() for item in to_value.split(",") if item.strip()]
    if not to_emails:
        return None

    return EmailSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        from_email=from_email,
        to_emails=to_emails,
        use_ssl=use_ssl,
        from_name=from_name,
        subject_prefix=subject_prefix,
        top_n=top_n,
        portfolio_limit=portfolio_limit,
    )


def _latest_prediction_path(prediction_file: str | None = None) -> str:
    if prediction_file:
        if os.path.isabs(prediction_file):
            return prediction_file
        return os.path.join(BASE_DIR, prediction_file)

    candidates = sorted(
        f for f in glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv"))
        if "predictions_t1_" not in os.path.basename(f)
    )
    if not candidates:
        raise FileNotFoundError("No predictions_YYYY-MM-DD.csv were found.")
    return candidates[-1]


def _latest_t1_prediction_path() -> str | None:
    candidates = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    return candidates[-1] if candidates else None


def _load_t1_leaderboard(top_n: int = 20) -> tuple[str, pd.DataFrame]:
    """載入最新 T+1 預測 leaderboard，回傳 (prediction_date, df)."""
    path = _latest_t1_prediction_path()
    if path is None:
        return "", pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return "", pd.DataFrame()
    prediction_date = str(df["date"].iloc[0]) if "date" in df.columns else ""
    selected = df[df.get("selected_for_trade", pd.Series(dtype=bool)) == True].copy()
    if selected.empty:
        selected = df.head(top_n).copy()
    else:
        selected = selected.head(top_n)
    return prediction_date, selected.reset_index(drop=True)


def _load_leaderboard(prediction_path: str, top_n: int) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    pred_df = pd.read_csv(prediction_path)
    pred_df = _sort_prediction_df(pred_df)

    if {"pred_return_20d", "recommendation"} <= set(pred_df.columns):
        leaderboard = apply_sector_cap(pred_df, top_n=top_n).reset_index(drop=True)
    else:
        leaderboard = pred_df.head(top_n).copy().reset_index(drop=True)

    leaderboard["selection_rank"] = range(1, len(leaderboard) + 1)
    prediction_date = str(pred_df["date"].iloc[0]) if "date" in pred_df.columns else ""
    return prediction_date, pred_df, leaderboard


def _load_portfolio_snapshot(
    db_path: str = DEFAULT_DB_PATH,
    limit: int | None = None,
) -> tuple[dict[str, object], pd.DataFrame]:
    if not os.path.exists(db_path):
        return {}, pd.DataFrame()

    with connect_db(db_path) as conn:
        summary = summarize_ledger(conn)
        query = """
            WITH latest_marks AS (
                SELECT position_id, mark_date, close, close_return_pct
                FROM (
                    SELECT
                        position_id,
                        mark_date,
                        close,
                        close_return_pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY position_id
                            ORDER BY mark_date DESC
                        ) AS rn
                    FROM portfolio_marks
                ) ranked
                WHERE rn = 1
            )
            SELECT
                p.prediction_date,
                p.selection_rank,
                p.ticker,
                p.recommendation,
                p.status,
                ROUND(p.entry_open, 2) AS entry_open,
                ROUND(lm.close, 2) AS latest_close,
                ROUND(lm.close_return_pct * 100, 2) AS unrealized_pct,
                ROUND(p.max_drawdown_pct * 100, 2) AS max_drawdown_pct,
                lm.mark_date AS latest_mark_date
            FROM portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.prediction_date >= ?
            ORDER BY
                CASE p.status
                    WHEN 'open' THEN 0
                    WHEN 'pending' THEN 1
                    ELSE 2
                END,
                COALESCE(lm.close_return_pct, -999) DESC,
                p.prediction_date DESC,
                p.selection_rank ASC
            """
        params: tuple[object, ...] = ("2026-03-18",)
        if limit is not None and int(limit) > 0:
            query += "\n            LIMIT ?"
            params = ("2026-03-18", int(limit))

        detail_df = pd.read_sql_query(query, conn, params=params)
    return summary, detail_df


def _fmt_pct(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.{digits}f}%"


def _fmt_num(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _html_cell(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return html.escape(str(value))


def _format_risk_html(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"

    parts = [part.strip() for part in re.split(r"\s+(?=[⚠💡])", text) if part.strip()]
    if not parts:
        return html.escape(text)
    return "<br>".join(html.escape(part) for part in parts)


def _signed_value_class(value: object) -> str:
    if value is None or pd.isna(value):
        return "value-flat"
    numeric = float(value)
    if numeric > 0:
        return "value-up"
    if numeric < 0:
        return "value-down"
    return "value-flat"


def _badge_class(value: object, *, kind: str) -> str:
    text = str(value or "")
    if kind == "recommendation":
        if text == "強力買進":
            return "badge badge-strong-buy"
        if text == "建議買進":
            return "badge badge-buy"
        if text.startswith("觀望"):
            return "badge badge-watch"
        if "賣出" in text:
            return "badge badge-sell"
        return "badge badge-neutral"

    if kind == "status":
        if text == "open":
            return "badge badge-open"
        if text == "pending":
            return "badge badge-pending"
        if text == "closed":
            return "badge badge-closed"
    return "badge badge-neutral"


def _render_leaderboard_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        rank = int(row["selection_rank"])
        ticker = _html_cell(row.get("ticker"))
        sector = _html_cell(row.get("sector") or "-")
        close = _fmt_num(row.get("close"), 1)
        pred_return = _fmt_pct(
            float(row.get("pred_return_20d")) * 100 if pd.notna(row.get("pred_return_20d")) else None
        )
        recommendation = _html_cell(row.get("recommendation"))
        risk_html = _format_risk_html(row.get("risk_tags"))
        rows.append(
            """
            <tr class="main-row">
              <td class="col-rank">#{rank}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}</div>
                <div class="cell-sub">{sector} / 收盤 {close}</div>
                <div class="cell-badges">
                  <span class="{rec_class}">{recommendation}</span>
                </div>
              </td>
              <td class="col-return">{pred_return}</td>
            </tr>
            <tr class="detail-row">
              <td class="detail-spacer"></td>
              <td colspan="2" class="detail-cell">
                <span class="detail-label">風險標籤</span>
                <div class="risk-text">{risk_html}</div>
              </td>
            </tr>
            """.format(
                rank=rank,
                ticker=ticker,
                sector=sector,
                close=close,
                pred_return=pred_return,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                risk_html=risk_html,
            )
        )
    return "\n".join(rows)


def _render_t1_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        rank = int(row.get("selection_rank", 0)) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        sector = _html_cell(row.get("sector") or "-")
        close = _fmt_num(row.get("close"), 1)
        hit_prob = _fmt_pct(float(row.get("hit_prob_3pct", 0)) * 100) if pd.notna(row.get("hit_prob_3pct")) else "-"
        t1_score = f"{float(row.get('t1_score', 0)):.3f}" if pd.notna(row.get("t1_score")) else "-"
        recommendation = _html_cell(row.get("recommendation"))
        setup = _html_cell(row.get("setup_tags") or "-")
        risk_html = _format_risk_html(row.get("risk_tags"))
        tp = _fmt_pct(float(row.get("take_profit", 0)) * 100) if pd.notna(row.get("take_profit")) else "-"
        sl = _fmt_pct(float(row.get("stop_loss", 0)) * -100) if pd.notna(row.get("stop_loss")) else "-"
        rows.append(
            """
            <tr>
              <td class="col-rank">#{rank}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}</div>
                <div class="cell-sub">{sector} / 收盤 {close}</div>
                <div class="cell-badges">
                  <span class="{rec_class}">{recommendation}</span>
                </div>
                <div class="cell-sub" style="margin-top:4px;">型態：{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title">{hit_prob}</div>
                <div class="cell-sub">T1分數 {t1_score}</div>
                <div class="cell-sub">停利 {tp} / 停損 {sl}</div>
              </td>
            </tr>
            """.format(
                rank=rank,
                ticker=ticker,
                sector=sector,
                close=close,
                hit_prob=hit_prob,
                t1_score=t1_score,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                setup=setup,
                risk_html=risk_html,
                tp=tp,
                sl=sl,
            )
        )
    return "\n".join(rows)


def _render_portfolio_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        prediction_date = _html_cell(row.get("prediction_date"))
        rank = int(row.get("selection_rank")) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        status = _html_cell(row.get("status"))
        recommendation = _html_cell(row.get("recommendation"))
        entry_open = _fmt_num(row.get("entry_open"), 2)
        latest_close = _fmt_num(row.get("latest_close"), 2)
        unrealized_pct = _fmt_pct(row.get("unrealized_pct"))
        max_drawdown_pct = _fmt_pct(row.get("max_drawdown_pct"))
        unrealized_class = _signed_value_class(row.get("unrealized_pct"))
        rows.append(
            """
            <tr>
              <td class="col-date">
                <div class="cell-title">{prediction_date}</div>
                <div class="cell-sub">#{rank} / {ticker}</div>
              </td>
              <td class="col-target">
                <div class="cell-badges">
                  <span class="{status_class}">{status}</span>
                  <span class="{rec_class} portfolio-rec">{recommendation}</span>
                </div>
                <div class="cell-sub">成本 {entry_open} → 最新 {latest_close}</div>
              </td>
              <td class="col-return">
                <div class="cell-title {unrealized_class}">{unrealized_pct}</div>
                <div class="cell-sub">回撤 {max_drawdown_pct}</div>
              </td>
            </tr>
            """.format(
                prediction_date=prediction_date,
                rank=rank,
                status_class=_badge_class(row.get("status"), kind="status"),
                status=status,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                entry_open=entry_open,
                latest_close=latest_close,
                unrealized_pct=unrealized_pct,
                max_drawdown_pct=max_drawdown_pct,
                unrealized_class=unrealized_class,
                ticker=ticker,
            )
        )
    return "\n".join(rows)


def build_email_html(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    leaderboard_df: pd.DataFrame,
    portfolio_summary: dict[str, object],
    portfolio_df: pd.DataFrame,
    t1_date: str = "",
    t1_df: pd.DataFrame | None = None,
) -> str:
    sentiment = _html_cell(all_pred_df["market_sentiment"].iloc[0]) if "market_sentiment" in all_pred_df.columns else "-"
    q25 = (
        _fmt_pct(float(all_pred_df["market_return_q25"].iloc[0]) * 100)
        if "market_return_q25" in all_pred_df.columns else "-"
    )
    median = (
        _fmt_pct(float(all_pred_df["market_return_median"].iloc[0]) * 100)
        if "market_return_median" in all_pred_df.columns else "-"
    )
    q75 = (
        _fmt_pct(float(all_pred_df["market_return_q75"].iloc[0]) * 100)
        if "market_return_q75" in all_pred_df.columns else "-"
    )

    top_count = len(leaderboard_df)
    summary_cards = [
        ("總批次", portfolio_summary.get("total_runs")),
        ("持倉中", portfolio_summary.get("open_positions")),
        ("待進場", portfolio_summary.get("pending_positions")),
        ("已平倉", portfolio_summary.get("closed_positions")),
        ("10% 回撤警示", portfolio_summary.get("breach_10pct_count")),
        ("已平倉平均報酬", _fmt_pct(
            float(portfolio_summary["avg_realized_return_pct"]) * 100
            if portfolio_summary.get("avg_realized_return_pct") is not None else None
        )),
    ]

    cards_html = "\n".join(
        f"""
        <div class="summary-card">
          <div class="label">{html.escape(str(label))}</div>
          <div class="value">{html.escape(str(value if value not in (None, '') else '-'))}</div>
        </div>
        """
        for label, value in summary_cards
    )

    leaderboard_section = (
        f"""
        <table class="data-table leaderboard-table">
          <thead>
            <tr>
              <th class="col-rank">名次</th>
              <th class="col-target">標的 / 推薦</th>
              <th class="col-return">預估 20 日報酬</th>
            </tr>
          </thead>
          <tbody>
            {_render_leaderboard_rows(leaderboard_df)}
          </tbody>
        </table>
        """
        if not leaderboard_df.empty
        else "<p class='muted'>目前沒有可顯示的 ML 排行資料。</p>"
    )

    portfolio_section = (
        f"""
        <table class="data-table portfolio-table">
          <thead>
            <tr>
              <th class="col-date">入選日 / 名次 / 代號</th>
              <th class="col-target">狀態 / 推薦 / 價格</th>
              <th class="col-return">帳面報酬 / 回撤</th>
            </tr>
          </thead>
          <tbody>
            {_render_portfolio_rows(portfolio_df)}
          </tbody>
        </table>
        """
        if not portfolio_df.empty
        else "<p class='muted'>目前沒有可顯示的帳本部位資料。</p>"
    )

    _t1_df = t1_df if t1_df is not None else pd.DataFrame()
    t1_section = ""
    if not _t1_df.empty:
        t1_count = len(_t1_df)
        t1_section = f"""
    <div class="section">
      <h2>T+1 次日動能排行（{html.escape(t1_date)}）</h2>
      <div class="muted">短線隔日沖 Top {t1_count}。命中率 = 隔日漲幅 &ge; 3% 的機率。</div>
      <table class="data-table leaderboard-table">
        <thead>
          <tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / 推薦 / 型態</th>
            <th class="col-return">命中率 / 停利停損</th>
          </tr>
        </thead>
        <tbody>
          {_render_t1_rows(_t1_df)}
        </tbody>
      </table>
    </div>
"""
    else:
        t1_section = ""

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(prediction_date)} 每日 ML 預測</title>
  <style>
    body {{
      margin: 0;
      background: #eef2f7;
      color: #0f172a;
      font-family: 'Segoe UI', 'Microsoft JhengHei', sans-serif;
      -webkit-text-size-adjust: 100%;
      text-size-adjust: 100%;
    }}
    .wrap {{
      max-width: 760px;
      margin: 0 auto;
      padding: 18px 12px 26px;
    }}
    .hero {{
      background: #ffffff;
      color: #0f172a;
      border-radius: 16px;
      padding: 18px 18px 16px;
      border: 1px solid #d8e0ea;
      border-top: 6px solid #2f6fed;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }}
    h1, h2 {{
      margin: 0 0 10px;
      color: inherit;
    }}
    .hero-kicker {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
      color: #2f6fed;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .hero-title {{
      margin: 0 0 12px;
      font-size: 30px;
      line-height: 1.22;
      font-weight: 800;
      color: #183b63 !important;
      -webkit-text-fill-color: #183b63;
    }}
    .hero-meta {{
      margin-top: 8px;
      font-size: 15px;
      line-height: 1.55;
      color: #395170 !important;
      -webkit-text-fill-color: #395170;
    }}
    .section {{
      margin-top: 16px;
      background: #ffffff;
      border-radius: 16px;
      padding: 16px 16px 14px;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
      border: 1px solid #d8e0ea;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .summary-card {{
      background: #f8fafc;
      border: 1px solid #d8e0ea;
      border-radius: 14px;
      padding: 14px 14px 12px;
    }}
    .label {{
      font-size: 12px;
      color: #475569;
      margin-bottom: 6px;
      font-weight: 600;
      line-height: 1.45;
    }}
    .value {{
      font-size: 24px;
      font-weight: 700;
      color: #0f172a;
    }}
    .muted {{
      color: #475569;
      font-size: 14px;
      line-height: 1.6;
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      margin-top: 14px;
      background: #ffffff;
      border: 1px solid #d8e0ea;
      border-radius: 14px;
      overflow: hidden;
    }}
    .data-table th,
    .data-table td {{
      padding: 11px 10px;
      border-bottom: 1px solid #e5ebf2;
      vertical-align: top;
      text-align: left;
      color: #0f172a;
      font-size: 13px;
      line-height: 1.5;
      word-break: break-word;
    }}
    .data-table th {{
      background: #f4f7fb;
      color: #334155;
      font-weight: 700;
      font-size: 12px;
    }}
    .data-table tr:last-child td {{
      border-bottom: none;
    }}
    .detail-row td {{
      padding-top: 6px;
      padding-bottom: 10px;
      background: #fafcff;
    }}
    .detail-spacer {{
      border-bottom: 1px solid #e5ebf2;
    }}
    .detail-cell {{
      border-bottom: 1px solid #e5ebf2;
    }}
    .col-rank {{
      width: 54px;
      text-align: center;
      white-space: nowrap;
      font-weight: 700;
    }}
    .leaderboard-table .col-target {{
      width: 52%;
    }}
    .leaderboard-table .col-return {{
      width: 24%;
      white-space: nowrap;
      font-weight: 700;
    }}
    .portfolio-table .col-date {{
      width: 28%;
    }}
    .portfolio-table .col-target {{
      width: 42%;
    }}
    .portfolio-table .col-return {{
      width: 30%;
      white-space: nowrap;
      font-weight: 700;
    }}
    .cell-title {{
      font-size: 15px;
      font-weight: 800;
      color: #0f172a;
      line-height: 1.35;
    }}
    .cell-sub {{
      margin-top: 2px;
      font-size: 12px;
      color: #64748b;
      line-height: 1.45;
    }}
    .value-up {{
      color: #c62828;
    }}
    .value-down {{
      color: #0f8a3b;
    }}
    .value-flat {{
      color: #0f172a;
    }}
    .cell-badges {{
      margin-top: 6px;
    }}
    .cell-badges .badge {{
      margin-right: 6px;
      margin-bottom: 4px;
    }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      line-height: 1.4;
      white-space: nowrap;
    }}
    .badge-strong-buy {{
      background: #e7f8ee;
      color: #166534;
      border: 1px solid #9dd8b4;
    }}
    .badge-buy {{
      background: #eaf4ff;
      color: #1d4ed8;
      border: 1px solid #b6d3ff;
    }}
    .badge-watch {{
      background: #fff7df;
      color: #a16207;
      border: 1px solid #f2d08a;
    }}
    .badge-sell {{
      background: #fdebed;
      color: #be123c;
      border: 1px solid #f2b7c0;
    }}
    .badge-open {{
      background: #eaf4ff;
      color: #1d4ed8;
      border: 1px solid #b6d3ff;
    }}
    .badge-pending {{
      background: #fff7df;
      color: #a16207;
      border: 1px solid #f2d08a;
    }}
    .badge-closed {{
      background: #f1ecff;
      color: #6d28d9;
      border: 1px solid #d5c4ff;
    }}
    .badge-neutral {{
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #d7dee7;
    }}
    .risk-text {{
      color: #7f1d1d;
      font-size: 13px;
      font-weight: 600;
      line-height: 1.55;
    }}
    .detail-label {{
      display: inline-block;
      margin-bottom: 4px;
      color: #9a3412;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
    }}
    .portfolio-rec {{
      margin-top: 0;
    }}
    .foot {{
      margin-top: 18px;
      color: #64748b;
      font-size: 12px;
      line-height: 1.55;
    }}
    @media screen and (max-width: 540px) {{
      .wrap {{
        padding: 14px 8px 22px;
      }}
      .hero {{
        padding: 16px 14px 14px;
      }}
      .hero-title {{
        font-size: 24px;
      }}
      .section {{
        padding: 14px 12px 12px;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .data-table th,
      .data-table td {{
        padding: 8px 6px;
        font-size: 11px;
      }}
      .cell-title {{
        font-size: 13px;
      }}
      .cell-sub {{
        font-size: 11px;
      }}
      .badge {{
        padding: 3px 7px;
        font-size: 10px;
      }}
      .leaderboard-table .col-target {{
        width: 50%;
      }}
      .leaderboard-table .col-return {{
        width: 26%;
      }}
      .portfolio-table .col-date {{
        width: 26%;
      }}
      .portfolio-table .col-target {{
        width: 44%;
      }}
      .portfolio-table .col-return {{
        width: 30%;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="hero-kicker">Stock ML Bot</div>
      <h1 class="hero-title" style="margin:0 0 12px;color:#183b63 !important;-webkit-text-fill-color:#183b63;">{html.escape(prediction_date)} 每日 ML 預測與帳本觀察</h1>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">
        市場氣氛：{sentiment} / 25% 分位：{q25} / 中位數：{median} / 75% 分位：{q75}
      </div>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">產出時間：{generated_at}</div>
    </div>

    <div class="section">
      <h2>今日 ML 排行（20 日）</h2>
      <div class="muted">本封信顯示 Top {top_count}。風險標籤改為每檔下一行整列顯示，避免手機版擠壓跑版。</div>
      {leaderboard_section}
    </div>

    {t1_section}

    <div class="section">
      <h2>實戰帳本摘要</h2>
      <div class="grid">
        {cards_html}
      </div>
      {portfolio_section}
    </div>

    <div class="foot">
      此信件由 F:\\stock 每日 pipeline 自動產生。
    </div>
  </div>
</body>
</html>
"""


def send_email(settings: EmailSettings, subject: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.from_name} <{settings.from_email}>"
    message["To"] = ", ".join(settings.to_emails)
    message.set_content("請使用支援 HTML 的郵件客戶端查看每日 ML 預測與帳本觀察。")
    message.add_alternative(html_body, subtype="html")

    if settings.use_ssl:
        with smtplib.SMTP_SSL(settings.host, settings.port, timeout=30) as server:
            server.login(settings.user, settings.password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as server:
            server.starttls()
            server.login(settings.user, settings.password)
            server.send_message(message)


def send_latest_email(
    prediction_file: str | None = None,
    dry_run: bool = False,
    preview_path: str | None = None,
) -> dict[str, object]:
    settings = load_email_settings()
    if settings is None:
        print("[email] SMTP 未設定，跳過寄信。")
        return {"status": "skipped", "reason": "missing_smtp_config"}

    prediction_path = _latest_prediction_path(prediction_file)
    prediction_date, all_pred_df, leaderboard_df = _load_leaderboard(
        prediction_path, settings.top_n
    )
    portfolio_summary, portfolio_df = _load_portfolio_snapshot(
        db_path=DEFAULT_DB_PATH,
        limit=0,
    )
    t1_date, t1_df = _load_t1_leaderboard(top_n=20)

    html_body = build_email_html(
        prediction_date=prediction_date,
        all_pred_df=all_pred_df,
        leaderboard_df=leaderboard_df,
        portfolio_summary=portfolio_summary,
        portfolio_df=portfolio_df,
        t1_date=t1_date,
        t1_df=t1_df,
    )
    preview_target = preview_path or DEFAULT_PREVIEW_PATH
    os.makedirs(os.path.dirname(preview_target), exist_ok=True)
    with open(preview_target, "w", encoding="utf-8") as f:
        f.write(html_body)

    subject = f"{settings.subject_prefix} {prediction_date} 每日 ML 預測與帳本觀察"
    if dry_run:
        print(f"[email] Dry run 完成：{preview_target}")
        return {
            "status": "preview",
            "preview_path": preview_target,
            "subject": subject,
            "prediction_path": prediction_path,
        }

    send_email(settings, subject, html_body)
    print(f"[email] 已寄出至: {', '.join(settings.to_emails)}")
    return {
        "status": "sent",
        "preview_path": preview_target,
        "subject": subject,
        "prediction_path": prediction_path,
        "to": settings.to_emails,
    }


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Send the daily ML email report.")
    parser.add_argument("--prediction-file", help="Specific predictions_YYYY-MM-DD.csv path.")
    parser.add_argument("--dry-run", action="store_true", help="Render HTML preview without sending.")
    parser.add_argument(
        "--preview-path",
        default=DEFAULT_PREVIEW_PATH,
        help="Where to write the HTML preview. Default: %(default)s",
    )
    args = parser.parse_args(argv)

    return send_latest_email(
        prediction_file=args.prediction_file,
        dry_run=args.dry_run,
        preview_path=args.preview_path,
    )


if __name__ == "__main__":
    main()
