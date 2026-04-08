"""AI 每日投資總結 — 用 Claude API 生成精簡的盤勢摘要。"""
from __future__ import annotations

import os
import sys

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)


def _load_api_key() -> str | None:
    for path in [os.path.join(BASE_DIR, ".env.email"), os.path.join(BASE_DIR, ".env")]:
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1]
    return os.environ.get("ANTHROPIC_API_KEY")


def _collect_context() -> str:
    """收集當天所有可用數據，組成給 AI 的 context。"""
    import glob
    import json
    import pandas as pd
    from ml.config import MODEL_DIR

    sections: list[str] = []

    # 1. 20D 預測 Top 10
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))
    pred_files = [f for f in pred_files if "shadow" not in f and "t1" not in f]
    if pred_files:
        df = pd.read_csv(pred_files[-1], dtype={"ticker": str})
        pred_date = df["date"].iloc[0] if "date" in df.columns else "unknown"
        top10 = df.head(10)
        lines = [f"預測日期: {pred_date}", "20D 模型 Top 10:"]
        for _, r in top10.iterrows():
            rec = r.get("recommendation", "")
            ret = r.get("pred_return_20d", None)
            ret_str = f"{float(ret)*100:.1f}%" if pd.notna(ret) else "-"
            lines.append(f"  {r['ticker']} | {rec} | 預估20D報酬: {ret_str}")
        sections.append("\n".join(lines))

    # 2. T+1 預測 Top 10
    t1_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if t1_files:
        t1 = pd.read_csv(t1_files[-1], dtype={"ticker": str})
        t1_date = t1["date"].iloc[0] if "date" in t1.columns else "unknown"
        selected = t1[t1.get("selected_for_trade", pd.Series(False)).astype(bool)]
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
        idx_path = os.path.join(BASE_DIR, "大盤指數", "index_prices.csv")
        if os.path.exists(idx_path):
            idx = pd.read_csv(idx_path)
            latest = idx.groupby("code").last().reset_index()
            lines = ["大盤指數:"]
            for _, r in latest.iterrows():
                lines.append(f"  {r['code']}: {r.get('close', '-')} ({r.get('change_pct', '-')}%)")
            sections.append("\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(sections)


def generate_daily_summary() -> str:
    """呼叫 Claude API 生成每日投資總結，回傳 HTML 字串。"""
    api_key = _load_api_key()
    if not api_key:
        return "<p>AI 摘要不可用：未設定 ANTHROPIC_API_KEY</p>"

    context = _collect_context()
    if not context.strip():
        return "<p>AI 摘要不可用：無可用數據</p>"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": f"""你是台股投資分析助理。根據以下今日系統數據，用繁體中文撰寫一份精簡的每日投資總結。

要求：
1. 用 HTML 格式輸出（不要包含 <html>/<body> 標籤，只要內容）
2. 分成 3 段：【今日盤勢】【模型觀點】【風險提示】
3. 每段 2-3 句話，簡潔有力
4. 如果有大盤熔斷或異常狀況，優先強調
5. 提到具體股票代號時用粗體
6. 不要編造數據中沒有的資訊

今日數據：
{context}""",
            }],
        )
        result = msg.content[0].text.strip()
        # 移除 AI 可能包的 code fence
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
        if result.endswith("```"):
            result = result.rsplit("```", 1)[0]
        return result.strip()
    except Exception as e:
        return f"<p>AI 摘要生成失敗: {e}</p>"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(generate_daily_summary())
