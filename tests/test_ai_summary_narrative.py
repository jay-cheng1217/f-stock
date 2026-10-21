# -*- coding: utf-8 -*-
"""AI 總評段(本地模型)的防呆:模型掛了不得影響信件,輸出異常寧可不放。"""
import html

from scripts import ai_summary

FACTS = "<h3>【盤勢】</h3><p>TWII 收 48341.31 點,小跌整理。</p><h3>【風險提示】</h3><p>流動性門檻 500 張。</p>"


def test_narrative_omitted_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300: None)
    assert ai_summary._build_ai_narrative_html(FACTS) == ""


def test_narrative_is_escaped_and_labeled(monkeypatch):
    raw = "大盤小跌整理,風險提示照舊;<b>注意</b>流動性門檻。整體維持觀望基調,等待更明確的方向。"
    monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300: raw)
    out = ai_summary._build_ai_narrative_html(FACTS)
    assert "AI 總評" in out and "實驗性" in out and "以上方段落為準" in out
    assert "<b>" not in out and html.escape("<b>注意</b>") in out      # 模型輸出一律跳脫,不得注入 HTML


def test_narrative_rejects_malformed_or_extreme_output(monkeypatch):
    for bad in ("太短", "| 表格 | 失控 | " + "長" * 80, "# 標題開頭 " + "字" * 60, "字" * 700):
        monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300, _b=bad: _b)
        assert ai_summary._build_ai_narrative_html(FACTS) == "", bad[:12]


def test_narrative_strips_blocked_short_horizon_terms(monkeypatch):
    raw = ("大盤小跌整理,觀望為主,風險控管照舊,維持既有紀律與分散配置,"
           "名單與數據仍以上方段落為準,今天沒有需要特別加碼的理由。T+1 模型今天訊號偏多。")
    monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300: raw)
    out = ai_summary._build_ai_narrative_html(FACTS)
    assert out and "T+1" not in out


def test_daily_summary_never_breaks_when_narrative_raises(monkeypatch):
    monkeypatch.setattr(ai_summary, "_build_market_summary_html", lambda d: "<p>市</p>")
    monkeypatch.setattr(ai_summary, "_build_production_signal_html", lambda d=None: "<p>訊</p>")
    monkeypatch.setattr(ai_summary, "_build_risk_summary_html", lambda: "<p>險</p>")

    def boom(markup):
        raise RuntimeError("fixture")

    monkeypatch.setattr(ai_summary, "_build_ai_narrative_html", boom)
    out = ai_summary.generate_daily_summary(prediction_date="2026-10-15")
    assert out == "<p>市</p>\n\n<p>訊</p>\n\n<p>險</p>"


def test_narrative_with_a_number_not_in_the_facts_is_dropped(monkeypatch, capsys):
    raw = "大盤小跌整理,台股收在 48000 點附近,風險提示照舊,維持既有紀律與分散配置,名單與數據仍以上方段落為準。"
    monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300: raw)
    assert ai_summary._build_ai_narrative_html(FACTS) == ""
    assert "48000" in capsys.readouterr().err                          # 印出原值,隔天可從 stdio log 追查


def test_numbers_from_the_facts_pass_with_rounding_and_sign_in_words(monkeypatch):
    """2026-10-21 實例:原段落「TWII 46292.93 點 -1.1%」,總評寫「跌幅達1.1%」——方向用文字表達,數字有出處。"""
    facts = FACTS + "<p>TWII 日漲跌 -1.13%,停損率差異 +4.92 pp,已實現損益 -1,571,839 元。</p>"
    raw = ("大盤收 48341.31 點,跌幅約 1.1%,停損率差異約 4.9 pp,已實現損益約 1,571,839 元,"
           "流動性門檻仍為 500 張,名單與數據仍以上方段落為準。")
    monkeypatch.setattr(ai_summary, "_ollama", lambda prompt, timeout=300: raw)
    assert ai_summary._build_ai_narrative_html(facts)


def test_integers_and_stock_codes_must_match_exactly():
    facts = "停損率差異 +2.79 pp;代號 2330 台積電。"
    assert ai_summary._ungrounded_numbers("共 3 檔", facts) == ["3"]            # 整數不可由 2.79 四捨五入而來
    assert ai_summary._ungrounded_numbers("2454 聯發科", facts) == ["2454"]
    assert ai_summary._ungrounded_numbers("2330 台積電,差異約 2.8", facts) == []
