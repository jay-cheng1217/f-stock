"""AI 每日投資總結 — 用 Claude API 生成精簡的盤勢摘要。"""
from __future__ import annotations

import os
import re
import sys
import html

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.signal_explainability import explain_signal_row

PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
T1_PREDICTION_RE = re.compile(r"^predictions_t1_\d{4}-\d{2}-\d{2}\.csv$")
UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_\d{4}-\d{2}-\d{2}\.csv$")
DAILY_SUMMARY_BLOCKED_TERMS = ("T+1", "隔日沖", "selected_for_trade")
MARKET_INDEX_FILES = [
    ("TWII", "index_TWII.csv", "點"),
    ("S&P500", "index_GSPC.csv", "點"),
    ("SOX", "index_SOX.csv", "點"),
    ("VIX", "index_VIX.csv", "點"),
]


def _load_api_key() -> str | None:
    for path in [os.path.join(BASE_DIR, ".env.email"), os.path.join(BASE_DIR, ".env")]:
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1]
    return os.environ.get("ANTHROPIC_API_KEY")


def _latest_matching_file(directory: str, pattern: str, name_re: re.Pattern[str]) -> str | None:
    import glob

    candidates = sorted(
        path
        for path in glob.glob(os.path.join(directory, pattern))
        if name_re.match(os.path.basename(path))
    )
    return candidates[-1] if candidates else None


def _email_aligned_leaderboard(df, top_n: int = 10, cap_top_n: int = 30):
    """Return the same sector-capped 20D leaderboard used by the email tables."""
    from contextlib import redirect_stdout
    from io import StringIO

    from ml.predict import _sort_prediction_df, apply_sector_cap

    sorted_df = _sort_prediction_df(df)
    if {"pred_return_20d", "recommendation"} <= set(sorted_df.columns):
        # apply_sector_cap prints adjustment diagnostics; keep the AI context quiet.
        with redirect_stdout(StringIO()):
            capped = apply_sector_cap(sorted_df, top_n=max(cap_top_n, top_n)).reset_index(drop=True)
        return capped.head(top_n).copy().reset_index(drop=True)
    return sorted_df.head(top_n).copy().reset_index(drop=True)


def _format_pct(value) -> str:
    try:
        import pandas as pd

        if pd.isna(value):
            return "-"
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "-"


def _format_pp(value) -> str:
    try:
        import pandas as pd

        if pd.isna(value):
            return "-"
        return f"{float(value) * 100:+.2f} pp"
    except Exception:
        return "-"


def _settlement_action_label(value) -> str:
    raw = "" if value is None else str(value)
    if raw == "half_size_no_chase":
        return "半碼 / 不追價"
    if raw == "quarter_size_high_beta_no_chase":
        return "高 beta 四分之一碼 / 不追價"
    return "-"


def _describe_market_return(value) -> str:
    """Describe a market daily return by numeric threshold only."""
    try:
        import pandas as pd

        if pd.isna(value):
            return "資料不足"
        numeric = float(value)
    except Exception:
        return "資料不足"

    if numeric > 0.01:
        return "強勢上漲"
    if numeric >= 0:
        return "小漲整理"
    if numeric >= -0.01:
        return "小跌整理"
    if numeric >= -0.03:
        return "明顯下跌"
    return "顯著重挫"


def _describe_vix_return(value) -> str:
    """Describe VIX with volatility wording, still purely threshold based."""
    try:
        import pandas as pd

        if pd.isna(value):
            return "資料不足"
        numeric = float(value)
    except Exception:
        return "資料不足"

    if numeric > 0.01:
        return "波動升溫"
    if numeric >= 0:
        return "波動小幅升溫"
    if numeric >= -0.01:
        return "波動小幅降溫"
    if numeric >= -0.03:
        return "波動降溫"
    return "波動顯著降溫"


def _load_index_snapshot(code: str, filename: str, prediction_date: str | None) -> dict[str, object] | None:
    import pandas as pd

    index_path = os.path.join(BASE_DIR, "大盤指數", filename)
    if not os.path.exists(index_path):
        return None
    df = pd.read_csv(index_path)
    if df.empty or "Date" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    df["daily_return"] = df["Close"] / df["Close"].shift(1) - 1.0
    if prediction_date and prediction_date != "unknown":
        target = pd.to_datetime(prediction_date, errors="coerce")
        if not pd.isna(target):
            df = df[df["Date"] <= target]
    if df.empty:
        return None
    row = df.iloc[-1]
    daily_return = row.get("daily_return")
    description = _describe_vix_return(daily_return) if code == "VIX" else _describe_market_return(daily_return)
    return {
        "code": code,
        "date": row["Date"].date().isoformat(),
        "close": float(row["Close"]),
        "daily_return": daily_return,
        "description": description,
    }


def _build_market_summary_html(prediction_date: str | None) -> str:
    rows: list[str] = []
    for code, filename, unit in MARKET_INDEX_FILES:
        snapshot = _load_index_snapshot(code, filename, prediction_date)
        if snapshot is None:
            continue
        close_unit = f" {unit}" if unit else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{float(snapshot['close']):.2f}{close_unit}</td>"
            f"<td>{_format_pct(snapshot['daily_return'])}</td>"
            f"<td>{html.escape(str(snapshot['description']))}</td>"
            f"<td>{html.escape(str(snapshot['date']))}</td>"
            "</tr>"
        )
    if not rows:
        return "<h3>【今日盤勢】</h3><p>市場資料不足，請勿依賴文字摘要判斷盤勢。</p>"
    return f"""
<h3>【今日盤勢】</h3>
<table>
  <thead><tr><th>指標</th><th>收盤</th><th>日漲跌</th><th>數值判斷</th><th>資料日</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
""".strip()


def _unified_signal_path(prediction_date: str | None = None) -> str | None:
    from ml.config import MODEL_DIR

    if prediction_date and prediction_date != "unknown":
        exact = os.path.join(MODEL_DIR, f"unified_signals_{prediction_date}.csv")
        if os.path.exists(exact):
            return exact
        return None
    return _latest_matching_file(MODEL_DIR, "unified_signals_*.csv", UNIFIED_SIGNAL_RE)


def _rank_column(df) -> str:
    import pandas as pd

    if "two_stage_rank" in df.columns:
        ranks = pd.to_numeric(df["two_stage_rank"], errors="coerce")
        if int(ranks.notna().sum()) >= max(1, int(len(df) * 0.8)):
            return "two_stage_rank"
    return "rank_20d" if "rank_20d" in df.columns else ""


def _build_production_signal_html(prediction_date: str | None = None) -> str:
    import pandas as pd

    path = _unified_signal_path(prediction_date)
    if not path:
        return (
            "<h3>【今日 production 訊號】</h3>"
            f"<p>找不到 unified_signals_{html.escape(str(prediction_date or 'latest'))}.csv，今日不列個股名單。</p>"
        )
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if df.empty or "production_gate_status" not in df.columns:
        return "<h3>【今日 production 訊號】</h3><p>今日 production gate 資料不足，無進場訊號。</p>"

    open_df = df[df["production_gate_status"].fillna("").astype(str).str.upper() == "OPEN"].copy()
    if open_df.empty:
        reasons = df.get("production_gate_reason", pd.Series("", index=df.index)).fillna("").astype(str)
        top_reason = reasons.value_counts().head(1)
        reason_text = ""
        if not top_reason.empty and top_reason.index[0]:
            reason_text = f"主要原因：{html.escape(str(top_reason.index[0]))}（{int(top_reason.iloc[0])} 檔）。"
        return (
            "<h3>【今日 production 訊號】</h3>"
            f"<p>今日 production gate 未開，無進場訊號。{reason_text}</p>"
        )

    active_df = open_df.copy()
    if "signal_type" in active_df.columns:
        active_df = active_df[
            active_df["signal_type"].fillna("").astype(str).str.upper().ne("NONE")
        ].copy()
    if "tradability_status" in active_df.columns:
        active_df = active_df[
            active_df["tradability_status"].fillna("").astype(str).str.upper().eq("OPEN")
        ].copy()
    if active_df.empty:
        reasons = open_df.get("tradability_reason", pd.Series("", index=open_df.index)).fillna("").astype(str)
        top_reason = reasons.value_counts().head(1)
        reason_text = ""
        if not top_reason.empty and top_reason.index[0]:
            reason_text = f"主要原因：{html.escape(str(top_reason.index[0]))}（{int(top_reason.iloc[0])} 檔）。"
        return (
            "<h3>【今日 production 訊號】</h3>"
            f"<p>Production gate OPEN，但 tradability gate 目前沒有可進場標的。{reason_text}</p>"
        )

    rank_col = _rank_column(active_df)
    if rank_col:
        active_df[rank_col] = pd.to_numeric(active_df[rank_col], errors="coerce")
        active_df = active_df.sort_values([rank_col, "ticker"], na_position="last")
    else:
        active_df = active_df.sort_values("ticker")

    settlement_notice = ""
    if "futures_settlement_window" in active_df.columns:
        settlement_mask = active_df["futures_settlement_window"].fillna(False).astype(bool)
        if settlement_mask.any():
            settlement_date = html.escape(str(active_df.loc[settlement_mask, "futures_settlement_date"].iloc[0]))
            phase = str(active_df.loc[settlement_mask, "futures_settlement_phase"].iloc[0])
            phase_text = "結算日" if phase == "settlement_day" else "結算前一交易日"
            settlement_notice = (
                f"<p><strong>結算風控：</strong>{phase_text}，月結算日 {settlement_date}。"
                "已套用一般標的半碼、高 beta 標的四分之一碼，且不追價。</p>"
            )

    rows: list[str] = []
    for _, row in active_df.head(10).iterrows():
        ticker = html.escape(str(row.get("ticker", "")))
        rank = row.get(rank_col) if rank_col else "-"
        rank_text = "-" if pd.isna(rank) else str(int(rank))
        ret = _format_pct(row.get("pred_return_20d"))
        units = row.get("target_units", "-")
        weight = _format_pct(row.get("target_weight_ratio"))
        settlement_action = html.escape(_settlement_action_label(row.get("futures_settlement_action")))
        explain = explain_signal_row(row.to_dict(), source="unified")
        explain_text = html.escape(str(explain.get("summary") or "-"))
        rows.append(
            "<tr>"
            f"<td>{rank_text}</td><td><strong>{ticker}</strong></td><td>{ret}</td><td>{html.escape(str(units))} 單位</td>"
            f"<td>{weight}</td><td>{settlement_action}</td><td>{explain_text}</td>"
            "</tr>"
        )
    return f"""
<h3>【今日 production 訊號】</h3>
<p>名單來源：{html.escape(os.path.basename(path))}，僅列 production gate OPEN 且 tradability OPEN 的可進場標的。</p>
{settlement_notice}
<table>
  <thead><tr><th>Rank</th><th>Ticker</th><th>預估 20D 報酬</th><th>目標單位</th><th>訊號配置上限</th><th>結算操作</th><th>Explain</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<p>訊號配置上限先依當日訊號單位正規化，再套用風險折減；帳本依可用現金與既有部位計算實際配置，並受此總部位上限限制。
若空帳本僅有一檔訊號，現行配置器可能為待成交部位預留接近全部帳本資金，存在集中風險。
此數字不表示已成交或目前既有持倉比例。</p>
""".strip()


def _build_risk_summary_html() -> str:
    import json

    shadow_path = os.path.join(BASE_DIR, "ml", "reports", "shadow_mode_latest.json")
    if not os.path.exists(shadow_path):
        return "<h3>【風險提示】</h3><p>Shadow 對照資料不存在；請以正式訊號、風控規則與帳本為準。</p>"
    try:
        with open(shadow_path, "r", encoding="utf-8") as f:
            sr = json.load(f)
    except Exception as exc:
        return f"<h3>【風險提示】</h3><p>Shadow 對照資料讀取失敗：{html.escape(str(exc))}。</p>"

    comp = sr.get("comparison", {}) or {}
    overlap = sr.get("selection_overlap", {}) or {}
    lines = [
        f"平均已實現報酬差異：{_format_pp(comp.get('avg_realized_return_pct_delta'))}",
        f"帳本 MDD 差異：{_format_pp(comp.get('book_mdd_pct_delta'))}",
        f"停損率差異：{_format_pp(comp.get('stop_loss_rate_delta'))}",
        f"選股重疊：{overlap.get('overlap_count', '-')} / {overlap.get('union_count', '-')} 檔",
    ]
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f"<h3>【風險提示】</h3><ul>{items}</ul><p>所有差異數字皆以 pp 表示；請勿解讀為無單位小數。</p>"


def _summary_html_to_text(markup: str) -> str:
    text = re.sub(r"</(h3|p|li|tr)>", "\n", markup)
    text = re.sub(r"<(br|br /)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _latest_t1_status_line(pred_date: str) -> str:
    import pandas as pd
    from ml.config import MODEL_DIR

    t1_path = _latest_matching_file(MODEL_DIR, "predictions_t1_*.csv", T1_PREDICTION_RE)
    if not t1_path:
        return "T+1 今日無可用預測檔，模型觀點不納入短線選股。"

    t1 = pd.read_csv(t1_path, dtype={"ticker": str})
    if t1.empty:
        return "T+1 今日無可用預測資料，模型觀點不納入短線選股。"

    t1_date = str(t1["date"].iloc[0]) if "date" in t1.columns else "unknown"
    if pred_date != "unknown" and t1_date != str(pred_date):
        return f"T+1 最新檔日期 {t1_date} 與 20D 預測日期 {pred_date} 不一致，今日摘要不納入舊 T+1 選股。"

    selected_flag = t1.get("selected_for_trade", pd.Series(False, index=t1.index)).astype(bool)
    selected = t1[selected_flag].head(3)
    if selected.empty:
        return "T+1 今日沒有 selected_for_trade=True 的實際交易標的，模型觀點不另列短線選股。"

    items = []
    for _, row in selected.iterrows():
        ticker = html.escape(str(row.get("ticker", "")))
        prob = _format_pct(row.get("hit_prob_3pct"))
        items.append(f"<strong>{ticker}</strong>（觸及 3% 機率 {prob}）")
    return "T+1 實際選入標的為 " + "、".join(items) + "。"


def _build_model_viewpoint_html(
    *,
    prediction_date: str | None = None,
    all_pred_df=None,
    leaderboard_df=None,
    include_t1: bool = False,
) -> str:
    """Build the model-view section deterministically from the email leaderboard."""
    import pandas as pd
    from ml.config import MODEL_DIR

    pred_date = prediction_date or "unknown"
    if leaderboard_df is not None:
        leaderboard = leaderboard_df.head(5).copy().reset_index(drop=True)
    else:
        if all_pred_df is not None:
            df = all_pred_df.copy()
        else:
            pred_path = _latest_matching_file(MODEL_DIR, "predictions_*.csv", PRODUCTION_PREDICTION_RE)
            df = pd.read_csv(pred_path, dtype={"ticker": str}) if pred_path else None
        if df is None or df.empty:
            leaderboard = pd.DataFrame()
        else:
            if prediction_date is None:
                pred_date = str(df["date"].iloc[0]) if "date" in df.columns else "unknown"
            leaderboard = _email_aligned_leaderboard(df, top_n=5, cap_top_n=30)

    if leaderboard.empty:
        d20_line = "20D 今日沒有可用的正式選股名單。"
    else:
        items = []
        for _, row in leaderboard.iterrows():
            ticker = html.escape(str(row.get("ticker", "")))
            rec = html.escape(str(row.get("recommendation", "")))
            ret = _format_pct(row.get("pred_return_20d"))
            items.append(f"<strong>{ticker}</strong>（{rec}，預估20D {ret}）")
        d20_line = (
            "20D 正式選股前五名依信件表格排序為 "
            + "、".join(items)
            + "；模型觀點不另行改排序或改名單。"
        )

    paragraphs = [f"<p>{d20_line}</p>"]
    if include_t1:
        paragraphs.append(f"<p>{_latest_t1_status_line(str(pred_date))}</p>")

    return f"""
<h3>【模型觀點】</h3>
{''.join(paragraphs)}
""".strip()


def _strip_model_viewpoint_section(markup: str) -> str:
    start = markup.find("【模型觀點】")
    if start < 0:
        return markup
    risk = markup.find("【風險提示】", start)
    if risk < 0:
        return markup[:start].rstrip()
    return (markup[:start] + markup[risk:]).strip()


def _insert_model_viewpoint_section(markup: str, model_viewpoint_html: str) -> str:
    cleaned = _strip_model_viewpoint_section(markup)
    risk = cleaned.find("【風險提示】")
    if risk < 0:
        return f"{cleaned}\n\n{model_viewpoint_html}".strip()
    return f"{cleaned[:risk].rstrip()}\n\n{model_viewpoint_html}\n\n{cleaned[risk:].lstrip()}".strip()


def _drop_blocked_daily_summary_terms(markup: str) -> str:
    """Remove generated sentences that mention disabled short-horizon models."""
    chunks = re.split(r"([。！？!?]\s*)", markup)
    kept: list[str] = []
    for i in range(0, len(chunks), 2):
        sentence = chunks[i]
        punctuation = chunks[i + 1] if i + 1 < len(chunks) else ""
        if any(term in sentence for term in DAILY_SUMMARY_BLOCKED_TERMS):
            continue
        kept.append(sentence + punctuation)
    return "".join(kept).strip()


def _collect_context(
    *,
    prediction_date: str | None = None,
    all_pred_df=None,
    leaderboard_df=None,
    include_model_selection: bool = True,
    include_t1: bool = True,
) -> str:
    """收集當天所有可用數據，組成給 AI 的 context。"""
    import json
    import pandas as pd
    from ml.config import MODEL_DIR

    sections: list[str] = []
    pred_date = prediction_date or "unknown"

    if include_model_selection:
        # 1. 20D 預測 Top 10：必須與信件選股表同源、同排序、同 sector cap。
        if all_pred_df is not None:
            df = all_pred_df.copy()
        else:
            pred_path = _latest_matching_file(MODEL_DIR, "predictions_*.csv", PRODUCTION_PREDICTION_RE)
            df = pd.read_csv(pred_path, dtype={"ticker": str}) if pred_path else None

        if df is not None:
            if df.empty:
                sections.append("20D 模型推薦: 今日無可用預測資料")
            else:
                if prediction_date is None:
                    pred_date = df["date"].iloc[0] if "date" in df.columns else "unknown"
                buy_mask = df["recommendation"].isin(["強力買進", "建議買進"])
                total_buys = int(buy_mask.sum())
                watch = int((df["recommendation"] == "觀望").sum())
                if leaderboard_df is not None:
                    leaderboard = leaderboard_df.head(10).copy().reset_index(drop=True)
                else:
                    leaderboard = _email_aligned_leaderboard(df, top_n=10, cap_top_n=30)
                lines = [
                    f"預測日期: {pred_date}",
                    f"20D 模型推薦: {total_buys} 檔買進, {watch} 檔觀望",
                    "Top 10 選股（與信件選股表同排序，已套用 sector cap）:",
                ]
                for _, r in leaderboard.iterrows():
                    rec = r.get("recommendation", "")
                    ret = r.get("pred_return_20d", None)
                    ret_str = f"{float(ret)*100:.1f}%" if pd.notna(ret) else "-"
                    lines.append(f"  {r['ticker']} | {rec} | 預估20D報酬: {ret_str}")
                sections.append("\n".join(lines))

        if include_t1:
            # 2. T+1 預測 Top 10；若 T+1 最新檔與 20D 日期不同，不把舊短線訊號混入今日摘要。
            t1_path = _latest_matching_file(MODEL_DIR, "predictions_t1_*.csv", T1_PREDICTION_RE)
            if t1_path:
                t1 = pd.read_csv(t1_path, dtype={"ticker": str})
                if t1.empty:
                    sections.append("T+1 模型推薦: 今日無可用預測資料")
                else:
                    t1_date = t1["date"].iloc[0] if "date" in t1.columns else "unknown"
                    if pred_date != "unknown" and str(t1_date) != str(pred_date):
                        sections.append(
                            f"T+1 模型推薦: 最新檔日期 {t1_date} 與 20D 預測日期 {pred_date} 不一致，"
                            "今日摘要不納入舊 T+1 選股。"
                        )
                    else:
                        selected_flag = t1.get("selected_for_trade", pd.Series(False, index=t1.index)).astype(bool)
                        selected = t1[selected_flag]
                        if selected.empty:
                            selected = t1.head(10)
                        else:
                            selected = selected.head(10)
                        lines = [f"T+1 預測日期: {t1_date}", "T+1 動能 Top 10 (今日買進、明日出場):"]
                        for _, r in selected.iterrows():
                            prob = r.get("hit_prob_3pct", None)
                            prob_str = f"{float(prob)*100:.1f}%" if pd.notna(prob) else "-"
                            tags = r.get("setup_tags", "")
                            risk = r.get("risk_tags", "")
                            lines.append(f"  {r['ticker']} | 觸及3%機率: {prob_str} | 型態: {tags} | 風險: {risk}")
                        # Veto check
                        veto_col = t1.get("veto_reason", pd.Series("", index=t1.index)).fillna("")
                        market_halted = veto_col.str.contains("大盤熔斷").any()
                        if market_halted:
                            lines.append(f"  *** 大盤熔斷啟動，全數不選 ***")
                        sections.append("\n".join(lines))

    # 3. 20D 帳本摘要
    try:
        from scripts.update_paper_portfolio import DEFAULT_DB_PATH, connect_db, summarize_ledger
        if os.path.exists(DEFAULT_DB_PATH):
            with connect_db() as conn:
                s = summarize_ledger(conn)
            sections.append(
                f"20D 帳本: runs={s['total_runs']} 持倉={s['open_positions']} "
                f"已平倉={s['closed_positions']} 停損={s['stopped_out_positions']} "
                f"平均報酬={s.get('avg_realized_return_pct', '-')}"
            )
    except Exception:
        pass

    if include_t1:
        # 4. T+1 帳本摘要
        try:
            from scripts.update_paper_portfolio_t1 import DEFAULT_DB_PATH as T1_DB, connect_db as t1_conn, summarize_ledger as t1_summary
            if os.path.exists(T1_DB):
                with t1_conn() as conn:
                    s = t1_summary(conn)
                sections.append(
                    f"T+1 帳本: 已結算={s.get('closed_positions', 0)} "
                    f"命中率={s.get('hit_rate', '-')} 平均報酬={s.get('avg_realized_return_pct', '-')}"
                )
        except Exception:
            pass

    # 5. Shadow mode
    shadow_path = os.path.join(MODEL_DIR, "..", "reports", "shadow_mode_latest.json")
    if os.path.exists(shadow_path):
        try:
            with open(shadow_path, "r", encoding="utf-8") as f:
                sr = json.load(f)
            comp = sr.get("comparison", {})
            ol = sr.get("selection_overlap", {})
            sections.append(
                f"Shadow vs Production: "
                f"報酬delta={comp.get('avg_realized_return_pct_delta', '-')} "
                f"MDD delta={comp.get('book_mdd_pct_delta', '-')} "
                f"停損率delta={comp.get('stop_loss_rate_delta', '-')} "
                f"重疊={ol.get('overlap_count', '-')}/{ol.get('union_count', '-')}"
            )
        except Exception:
            pass

    # 6. 大盤指數
    try:
        index_dir = os.path.join(BASE_DIR, "大盤指數")
        index_files = [
            ("TWII", "index_TWII.csv"),
            ("SOX", "index_SOX.csv"),
            ("GSPC", "index_GSPC.csv"),
            ("VIX", "index_VIX.csv"),
            ("USDTWDX", "index_USDTWDX.csv"),
        ]
        lines = ["大盤指數:"]
        for code, filename in index_files:
            idx_path = os.path.join(index_dir, filename)
            if not os.path.exists(idx_path):
                continue
            idx = pd.read_csv(idx_path)
            if idx.empty:
                continue
            r = idx.iloc[-1]
            close = r.get("Close", r.get("close", "-"))
            date = r.get("Date", r.get("date", "-"))
            lines.append(f"  {code}: {close} ({date})")
        if len(lines) > 1:
            sections.append("\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(sections)


def generate_daily_summary(
    *,
    prediction_date: str | None = None,
    all_pred_df=None,
    leaderboard_df=None,
) -> str:
    """Build the daily investment summary from production artifacts only.

    This section used to call an LLM. For daily production email correctness we
    now keep it deterministic: market wording is threshold-based, stock lists
    come only from unified_signals, and risk numbers always include units.
    """
    return "\n\n".join(
        [
            _build_market_summary_html(prediction_date),
            _build_production_signal_html(prediction_date),
            _build_risk_summary_html(),
        ]
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(generate_daily_summary())
