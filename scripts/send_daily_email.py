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
from ml.cross_confirm import get_dual_confirmed_tickers
from ml.features.sector import SECTOR_MAPPING_PATH
from ml.predict import apply_sector_cap, _sort_prediction_df


def _load_name_lookup() -> dict[str, str]:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}
    _df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in _df.columns:
        return {}
    return dict(zip(_df["Ticker"], _df["Name"]))


_NAME_LOOKUP: dict[str, str] = _load_name_lookup()
from scripts.update_paper_portfolio import (
    DEFAULT_DB_PATH,
    connect_db,
    summarize_ledger,
)
from scripts.update_paper_portfolio_t1 import (
    DEFAULT_DB_PATH as T1_DB_PATH,
    connect_db as t1_connect_db,
    summarize_ledger as t1_summarize_ledger,
)

DEFAULT_EMAIL_TOP_N = 30
DEFAULT_PORTFOLIO_LIMIT = 12
DEFAULT_SUBJECT_PREFIX = "[Stock ML]"
DEFAULT_PREVIEW_PATH = os.path.join(BASE_DIR, "logs", "daily_email_preview.html")
PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")


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
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(f))
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


def _load_t1_portfolio_snapshot(
    db_path: str = T1_DB_PATH,
) -> tuple[dict[str, object], pd.DataFrame]:
    if not os.path.exists(db_path):
        return {}, pd.DataFrame()

    with t1_connect_db(db_path) as conn:
        summary = t1_summarize_ledger(conn)
        detail_df = pd.read_sql_query(
            """
            SELECT
                prediction_date,
                selection_rank,
                ticker,
                sector,
                recommendation,
                setup_tags,
                status,
                ROUND(selection_close, 2) AS selection_close,
                ROUND(entry_open, 2) AS entry_open,
                ROUND(exit_close, 2) AS exit_close,
                ROUND(realized_return_pct * 100, 2) AS return_pct,
                ROUND(max_intraday_gain_pct * 100, 2) AS max_gain_pct,
                ROUND(max_intraday_drawdown_pct * 100, 2) AS max_dd_pct,
                hit_3pct
            FROM t1_positions
            WHERE prediction_date >= ?
            ORDER BY prediction_date DESC, selection_rank ASC
            """,
            conn,
            params=("2026-03-23",),
        )
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
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
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
                <div class="cell-title">{ticker}{name_html}</div>
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
                name_html=name_html,
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
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
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
                <div class="cell-title">{ticker}{name_html}</div>
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
                name_html=name_html,
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
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
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
                <div class="cell-sub">#{rank} / {ticker}{name_suffix}</div>
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
                name_suffix=f" {html.escape(name)}" if name else "",
            )
        )
    return "\n".join(rows)


def _render_t1_portfolio_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        pred_date = _html_cell(row.get("prediction_date"))
        rank = int(row.get("selection_rank")) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        status = _html_cell(row.get("status"))
        setup = _html_cell(row.get("setup_tags") or "-")
        entry = _fmt_num(row.get("entry_open"), 2)
        exit_c = _fmt_num(row.get("exit_close"), 2)
        ret = _fmt_pct(row.get("return_pct"))
        max_gain = _fmt_pct(row.get("max_gain_pct"))
        max_dd = _fmt_pct(row.get("max_dd_pct"))
        hit = row.get("hit_3pct")
        hit_str = "O" if hit == 1 else ("X" if hit == 0 else "-")
        hit_class = "value-up" if hit == 1 else ("value-down" if hit == 0 else "value-flat")
        ret_class = _signed_value_class(row.get("return_pct"))
        rows.append(
            """
            <tr>
              <td class="col-date">
                <div class="cell-title">{pred_date}</div>
                <div class="cell-sub">#{rank} / {ticker}{name_suffix}</div>
              </td>
              <td class="col-target">
                <div class="cell-badges">
                  <span class="{status_class}">{status}</span>
                </div>
                <div class="cell-sub">開 {entry} → 收 {exit_c}</div>
                <div class="cell-sub" style="margin-top:2px;">{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title {ret_class}">{ret}</div>
                <div class="cell-sub">高 {max_gain} / 低 {max_dd}</div>
                <div class="cell-sub {hit_class}">命中 {hit_str}</div>
              </td>
            </tr>
            """.format(
                pred_date=pred_date,
                rank=rank,
                ticker=ticker,
                status_class=_badge_class(row.get("status"), kind="status"),
                status=status,
                entry=entry,
                exit_c=exit_c,
                ret=ret,
                max_gain=max_gain,
                max_dd=max_dd,
                hit_str=hit_str,
                hit_class=hit_class,
                ret_class=ret_class,
                setup=setup,
                name_suffix=f" {html.escape(name)}" if name else "",
            )
        )
    return "\n".join(rows)


def _render_cross_confirm_rows(items: list[dict]) -> str:
    rows = []
    for i, item in enumerate(items, 1):
        ticker = html.escape(str(item.get("ticker", "")))
        name = _NAME_LOOKUP.get(str(item.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
        t1_prob = f"{float(item.get('t1_prob', 0)) * 100:.1f}%" if item.get("t1_prob") else "-"
        t1_rank = f"#{item['t1_rank']}" if item.get("t1_rank") else "-"
        d20_ret = _fmt_pct(float(item.get("d20_pred_return", 0)) * 100) if item.get("d20_pred_return") else "-"
        d20_rec = html.escape(str(item.get("d20_recommendation", "")))
        cross_score = f"{float(item.get('cross_score', 0)):.4f}" if item.get("cross_score") else "-"
        setup = html.escape(str(item.get("t1_setup_tags", "") or "-"))
        rows.append(
            f"""
            <tr>
              <td class="col-rank">#{i}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}{name_html}</div>
                <div class="cell-sub">T+1 排名 {t1_rank} / 機率 {t1_prob}</div>
                <div class="cell-badges">
                  <span class="{_badge_class(d20_rec, kind='recommendation')}">{d20_rec}</span>
                </div>
                <div class="cell-sub" style="margin-top:4px;">型態：{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title">{d20_ret}</div>
                <div class="cell-sub">綜合分數 {cross_score}</div>
              </td>
            </tr>
            """
        )
    return "\n".join(rows)


def _wrap_email_html(title: str, subtitle: str, body_html: str, generated_at: str) -> str:
    """Wrap section HTML in the shared email shell (head/style/hero/foot)."""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
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
      <h1 class="hero-title" style="margin:0 0 12px;color:#183b63 !important;-webkit-text-fill-color:#183b63;">{html.escape(title)}</h1>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">{subtitle}</div>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">產出時間：{generated_at}</div>
    </div>

    {body_html}

    <div class="foot">
      此信件由 F:\\stock 每日 pipeline 自動產生。
    </div>
  </div>
</body>
</html>
"""


def build_email_html(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    leaderboard_df: pd.DataFrame,
    portfolio_summary: dict[str, object],
    portfolio_df: pd.DataFrame,
    t1_date: str = "",
    t1_df: pd.DataFrame | None = None,
    t1_portfolio_summary: dict[str, object] | None = None,
    t1_portfolio_df: pd.DataFrame | None = None,
    cross_confirmed: list[dict] | None = None,
) -> str:
    """Build single combined email (kept for backward compat / preview)."""
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
    <div class="section" id="sec-t1">
      <h2>T+1 次日動能排行（{html.escape(t1_date)}）<a href="#" class="back-top">&#8679; 頂部</a></h2>
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

    # T+1 portfolio section
    _t1_port_df = t1_portfolio_df if t1_portfolio_df is not None else pd.DataFrame()
    _t1_port_summary = t1_portfolio_summary or {}
    if not _t1_port_df.empty or _t1_port_summary:
        t1_total = _t1_port_summary.get("closed_positions", 0)
        t1_hit = _t1_port_summary.get("hit_count", 0)
        t1_hit_rate = _fmt_pct(float(_t1_port_summary["hit_rate"]) * 100, 1) if _t1_port_summary.get("hit_rate") is not None else "-"
        t1_avg_ret = _fmt_pct(float(_t1_port_summary["avg_realized_return_pct"]) * 100) if _t1_port_summary.get("avg_realized_return_pct") is not None else "-"
        t1_pending = _t1_port_summary.get("pending_positions", 0)

        t1_cards_html = f"""
        <div class="summary-card"><div class="label">已結算</div><div class="value">{t1_total}</div></div>
        <div class="summary-card"><div class="label">待結算</div><div class="value">{t1_pending}</div></div>
        <div class="summary-card"><div class="label">命中率（盤中 &ge;3%）</div><div class="value">{t1_hit}/{t1_total} = {t1_hit_rate}</div></div>
        <div class="summary-card"><div class="label">平均開→收報酬</div><div class="value">{t1_avg_ret}</div></div>
        """

        t1_port_table = (
            f"""
            <table class="data-table portfolio-table">
              <thead>
                <tr>
                  <th class="col-date">預測日 / 名次 / 代號</th>
                  <th class="col-target">狀態 / 型態 / 價格</th>
                  <th class="col-return">報酬 / 高低 / 命中</th>
                </tr>
              </thead>
              <tbody>
                {_render_t1_portfolio_rows(_t1_port_df)}
              </tbody>
            </table>
            """
            if not _t1_port_df.empty
            else "<p class='muted'>目前沒有 T+1 帳本資料。</p>"
        )

        t1_portfolio_section = f"""
    <div class="section" id="sec-t1port">
      <h2>T+1 實戰帳本<a href="#" class="back-top">&#8679; 頂部</a></h2>
      <div class="grid">
        {t1_cards_html}
      </div>
      {t1_port_table}
    </div>
"""
    else:
        t1_portfolio_section = ""

    # Cross-confirmation section
    _cross = cross_confirmed or []
    if _cross:
        cross_count = len(_cross)
        cross_section = f"""
    <div class="section" id="sec-cross">
      <h2>T+1 x 20D 雙重確認<a href="#" class="back-top">&#8679; 頂部</a></h2>
      <div class="muted">以下 {cross_count} 檔同時被 T+1（次日動能）和 20D（中期趨勢）模型看好，訊號一致性較高。</div>
      <table class="data-table leaderboard-table">
        <thead>
          <tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / T+1 排名 / 20D 推薦</th>
            <th class="col-return">20D 預估報酬 / 綜合分數</th>
          </tr>
        </thead>
        <tbody>
          {_render_cross_confirm_rows(_cross)}
        </tbody>
      </table>
    </div>
"""
    else:
        cross_section = ""

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    market_subtitle = f"市場氣氛：{sentiment} / 25%：{q25} / 中位：{median} / 75%：{q75}"
    body_sections = f"""
    <div class="section">
      <h2>今日 ML 排行（20 日）</h2>
      <div class="muted">Top {top_count}。</div>
      {leaderboard_section}
    </div>

    {t1_section}

    {cross_section}

    <div class="section">
      <h2>20 日實戰帳本</h2>
      <div class="grid">
        {cards_html}
      </div>
      {portfolio_section}
    </div>

    {t1_portfolio_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} 每日 ML 預測與帳本觀察",
        subtitle=market_subtitle,
        body_html=body_sections,
        generated_at=generated_at,
    )


# --- Individual email builders for split-send mode ---


def _build_market_subtitle(all_pred_df: pd.DataFrame) -> str:
    sentiment = _html_cell(all_pred_df["market_sentiment"].iloc[0]) if "market_sentiment" in all_pred_df.columns else "-"
    q25 = _fmt_pct(float(all_pred_df["market_return_q25"].iloc[0]) * 100) if "market_return_q25" in all_pred_df.columns else "-"
    median = _fmt_pct(float(all_pred_df["market_return_median"].iloc[0]) * 100) if "market_return_median" in all_pred_df.columns else "-"
    q75 = _fmt_pct(float(all_pred_df["market_return_q75"].iloc[0]) * 100) if "market_return_q75" in all_pred_df.columns else "-"
    return f"市場氣氛：{sentiment} / 25%：{q25} / 中位：{median} / 75%：{q75}"


def build_email_ml(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    leaderboard_df: pd.DataFrame,
    cross_confirmed: list[dict] | None = None,
) -> str:
    """信1: ML 排行 + 雙重確認"""
    top_count = len(leaderboard_df)

    leaderboard_section = (
        f"""
        <table class="data-table leaderboard-table">
          <thead><tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / 推薦</th>
            <th class="col-return">預估 20 日報酬</th>
          </tr></thead>
          <tbody>{_render_leaderboard_rows(leaderboard_df)}</tbody>
        </table>
        """
        if not leaderboard_df.empty
        else "<p class='muted'>目前沒有可顯示的 ML 排行資料。</p>"
    )

    _cross = cross_confirmed or []
    cross_section = ""
    if _cross:
        cross_count = len(_cross)
        cross_section = f"""
    <div class="section">
      <h2>T+1 x 20D 雙重確認</h2>
      <div class="muted">以下 {cross_count} 檔同時被 T+1 和 20D 模型看好。</div>
      <table class="data-table leaderboard-table">
        <thead><tr>
          <th class="col-rank">名次</th>
          <th class="col-target">標的 / T+1 排名 / 20D 推薦</th>
          <th class="col-return">20D 預估報酬 / 綜合分數</th>
        </tr></thead>
        <tbody>{_render_cross_confirm_rows(_cross)}</tbody>
      </table>
    </div>
"""

    body = f"""
    <div class="section">
      <h2>今日 ML 排行（20 日）</h2>
      <div class="muted">Top {top_count}。</div>
      {leaderboard_section}
    </div>
    {cross_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} ML 排行 + 雙重確認",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_email_t1(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    t1_date: str,
    t1_df: pd.DataFrame,
) -> str:
    """信2: T+1 動能"""
    t1_count = len(t1_df)
    body = f"""
    <div class="section">
      <h2>T+1 次日動能排行（{html.escape(t1_date)}）</h2>
      <div class="muted">短線隔日沖 Top {t1_count}。命中率 = 隔日漲幅 &ge; 3% 的機率。</div>
      <table class="data-table leaderboard-table">
        <thead><tr>
          <th class="col-rank">名次</th>
          <th class="col-target">標的 / 推薦 / 型態</th>
          <th class="col-return">命中率 / 停利停損</th>
        </tr></thead>
        <tbody>{_render_t1_rows(t1_df)}</tbody>
      </table>
    </div>
"""
    return _wrap_email_html(
        title=f"{prediction_date} T+1 動能預測",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_email_portfolio(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    portfolio_summary: dict[str, object],
    portfolio_df: pd.DataFrame,
    t1_portfolio_summary: dict[str, object] | None = None,
    t1_portfolio_df: pd.DataFrame | None = None,
) -> str:
    """信3: 帳本總覽（20D + T+1）"""
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
        f'<div class="summary-card"><div class="label">{html.escape(str(label))}</div>'
        f'<div class="value">{html.escape(str(value if value not in (None, "") else "-"))}</div></div>'
        for label, value in summary_cards
    )

    portfolio_section = (
        f"""
        <table class="data-table portfolio-table">
          <thead><tr>
            <th class="col-date">入選日 / 名次 / 代號</th>
            <th class="col-target">狀態 / 推薦 / 價格</th>
            <th class="col-return">帳面報酬 / 回撤</th>
          </tr></thead>
          <tbody>{_render_portfolio_rows(portfolio_df)}</tbody>
        </table>
        """
        if not portfolio_df.empty
        else "<p class='muted'>目前沒有可顯示的帳本部位資料。</p>"
    )

    # T+1 portfolio
    _t1_port_df = t1_portfolio_df if t1_portfolio_df is not None else pd.DataFrame()
    _t1_port_summary = t1_portfolio_summary or {}
    t1_portfolio_section = ""
    if not _t1_port_df.empty or _t1_port_summary:
        t1_total = _t1_port_summary.get("closed_positions", 0)
        t1_hit_rate = _fmt_pct(float(_t1_port_summary["hit_rate"]) * 100, 1) if _t1_port_summary.get("hit_rate") is not None else "-"
        t1_avg_ret = _fmt_pct(float(_t1_port_summary["avg_realized_return_pct"]) * 100) if _t1_port_summary.get("avg_realized_return_pct") is not None else "-"
        t1_pending = _t1_port_summary.get("pending_positions", 0)
        t1_hit = _t1_port_summary.get("hit_count", 0)

        t1_cards = f"""
        <div class="summary-card"><div class="label">已結算</div><div class="value">{t1_total}</div></div>
        <div class="summary-card"><div class="label">待結算</div><div class="value">{t1_pending}</div></div>
        <div class="summary-card"><div class="label">命中率（盤中 &ge;3%）</div><div class="value">{t1_hit}/{t1_total} = {t1_hit_rate}</div></div>
        <div class="summary-card"><div class="label">平均開→收報酬</div><div class="value">{t1_avg_ret}</div></div>
        """

        t1_port_table = (
            f"""
            <table class="data-table portfolio-table">
              <thead><tr>
                <th class="col-date">預測日 / 名次 / 代號</th>
                <th class="col-target">狀態 / 型態 / 價格</th>
                <th class="col-return">報酬 / 高低 / 命中</th>
              </tr></thead>
              <tbody>{_render_t1_portfolio_rows(_t1_port_df)}</tbody>
            </table>
            """
            if not _t1_port_df.empty
            else "<p class='muted'>目前沒有 T+1 帳本資料。</p>"
        )

        t1_portfolio_section = f"""
    <div class="section">
      <h2>T+1 實戰帳本</h2>
      <div class="grid">{t1_cards}</div>
      {t1_port_table}
    </div>
"""

    body = f"""
    <div class="section">
      <h2>20 日實戰帳本</h2>
      <div class="grid">{cards_html}</div>
      {portfolio_section}
    </div>
    {t1_portfolio_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} 帳本總覽",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# --- (end of old inline template removed) ---



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
    remote_url: str | None = None,
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
    t1_portfolio_summary, t1_portfolio_df = _load_t1_portfolio_snapshot()

    # Cross-model confirmation (T+1 x 20D)
    try:
        cross_confirmed = get_dual_confirmed_tickers(top_n=10)
    except Exception:
        cross_confirmed = []

    # Remote URL banner (injected into first email only)
    remote_banner = ""
    if remote_url:
        remote_banner = (
            f'\n<div class="section" style="text-align:center;padding:14px 16px;">'
            f'<a href="{html.escape(remote_url)}" '
            f'style="color:#2f6fed;font-size:16px;font-weight:700;text-decoration:none;">'
            f'&#x1F310; 遠端看盤：{html.escape(remote_url)}</a></div>\n'
        )

    # Build 3 separate emails
    prefix = settings.subject_prefix
    emails: list[tuple[str, str]] = []  # (subject, html)

    # 信1: ML 排行 + 雙重確認
    html_ml = build_email_ml(prediction_date, all_pred_df, leaderboard_df, cross_confirmed)
    if remote_banner:
        html_ml = html_ml.replace("</body>", remote_banner + "</body>", 1)
    emails.append((f"{prefix} {prediction_date} [1/3] ML 排行 + 雙重確認", html_ml))

    # 信2: T+1 動能 (skip if empty)
    _t1_df = t1_df if t1_df is not None else pd.DataFrame()
    if not _t1_df.empty:
        html_t1 = build_email_t1(prediction_date, all_pred_df, t1_date, _t1_df)
        emails.append((f"{prefix} {prediction_date} [2/3] T+1 動能預測", html_t1))

    # 信3: 帳本總覽
    html_port = build_email_portfolio(
        prediction_date, all_pred_df,
        portfolio_summary, portfolio_df,
        t1_portfolio_summary, t1_portfolio_df,
    )
    emails.append((f"{prefix} {prediction_date} [3/3] 帳本總覽", html_port))

    # Save combined preview
    preview_target = preview_path or DEFAULT_PREVIEW_PATH
    os.makedirs(os.path.dirname(preview_target), exist_ok=True)
    with open(preview_target, "w", encoding="utf-8") as f:
        f.write("\n<hr style='margin:40px 0;border:3px solid #2f6fed;'>\n".join(h for _, h in emails))

    if dry_run:
        print(f"[email] Dry run 完成：{preview_target}")
        return {
            "status": "preview",
            "preview_path": preview_target,
            "subjects": [s for s, _ in emails],
            "prediction_path": prediction_path,
        }

    for subject, html_body in emails:
        send_email(settings, subject, html_body)
    print(f"[email] 已寄出 {len(emails)} 封至: {', '.join(settings.to_emails)}")
    return {
        "status": "sent",
        "preview_path": preview_target,
        "subjects": [s for s, _ in emails],
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
