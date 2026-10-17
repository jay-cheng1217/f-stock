# -*- coding: utf-8 -*-
"""新聞正面聲量:窗口、正向判定(排除價格回聲)、名次、只呈現不排序。"""
import json

import pandas as pd

from scripts import entry_dashboard, rere_news_volume as nv

TAX = {"ORDER_VISIBILITY": {"keywords": ["產能滿載", "接單"], "polarity": "positive"},
       "GOVERNANCE_RISK": {"keywords": ["檢調"], "polarity": "negative"}}


def _news(rows):
    return pd.DataFrame(rows, columns=["ticker", "published_at", "title"])


def test_window_is_14_calendar_days_up_to_the_data_date_and_never_after():
    news = _news([("6532", "2026-10-02 10:00", "瑞耘 台灣廠產能滿載"),     # 窗口第一天(10/15−13)
                  ("6532", "2026-10-01 23:59", "瑞耘 接單暢旺"),          # 早一天,不算
                  ("6532", "2026-10-16 08:00", "瑞耘 產能滿載再擴產")])   # 資料日之後,不可用(無前視)
    out = nv.summarize(news, ["6532"], "2026-10-15", TAX).set_index("ticker")
    assert out.loc["6532", "any14"] == 1 and out.loc["6532", "pos14"] == 1


def test_price_echo_titles_count_as_coverage_but_never_as_positive():
    news = _news([("1111", "2026-10-14 09:00", "某股 產能滿載 股價漲停"), ("1111", "2026-10-14 10:00", "某股 檢調搜索")])
    out = nv.summarize(news, ["1111"], "2026-10-15", TAX).set_index("ticker")
    assert out.loc["1111", "any14"] == 2 and out.loc["1111", "pos14"] == 0


def test_rank_puts_most_positive_first_and_stocks_without_news_get_zero():
    news = _news([("1111", "2026-10-14", "甲 接單"), ("1111", "2026-10-13", "甲 產能滿載"), ("2222", "2026-10-14", "乙 接單")])
    out = nv.summarize(news, ["1111", "2222", "3333"], "2026-10-15", TAX).set_index("ticker")
    assert list(out["pos14"]) == [2, 1, 0] and list(out["pos_rank"]) == [1, 2, 3] and set(out["pool_n"]) == {3}


def test_run_records_once_per_trade_date(tmp_path, monkeypatch):
    plan = {"trade_date": "2026-10-19", "as_of_date": "2026-10-16", "rows": [{"stock": "6532 瑞耘"}],
            "shadow_rere_pool": [{"ticker": "1111"}]}
    p = tmp_path / "entry_list_20261019.json"
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(nv, "OUT", tmp_path / "nv.csv")
    monkeypatch.setattr(nv, "_load_news", lambda tickers, as_of: _news([("6532", "2026-10-15", "瑞耘 台灣廠產能滿載")]))
    monkeypatch.setattr(nv, "classify_titles", lambda pairs: {p: "B" for p in pairs})
    first = nv.run(p)
    assert first.startswith("2026-10-19") and nv.run(p) == "2026-10-19 already recorded"   # 同日不重寫,保留當時數值
    rec = pd.read_csv(tmp_path / "nv.csv", dtype={"ticker": str}).set_index("ticker")
    assert rec.loc["6532", "pos14"] == 1 and rec.loc["1111", "any14"] == 0 and len(rec) == 2
    assert rec.loc["6532", "bull14"] == 1 and rec.loc["1111", "bull14"] == 0


def test_card_shows_news_volume_as_information_only():
    assert 'class="card-news"' in entry_dashboard.HTML and "新聞14天" in entry_dashboard.HTML
    assert "集保與新聞聲量為研究資訊，不是排序依據" in entry_dashboard.HTML
    # 2026-10-17 #33 工具驗收 FAIL 後,卡片不得顯示「正向」判定(關鍵字 42.5%、本地模型 81.7%,皆低於 85% 門檻)
    card_line = entry_dashboard.HTML.split('class="card-news"')[1].split("</div>")[0]
    assert "正向" not in card_line and "pos" not in card_line
    # #35 PASS:只顯示單向 📈 利多,不顯示利空/中性;說明須寫明「沒有📈不代表沒利多」
    assert "📈 利多" in card_line and "bull>0" in card_line
    assert "沒有📈不代表沒利多" in entry_dashboard.HTML


def test_bull14_counts_only_bullish_titles_inside_the_window():
    news = _news([("1111", "2026-10-14", "甲 Q3獲利創新高"), ("1111", "2026-10-13", "甲 參展"),
                  ("1111", "2026-09-01", "甲 舊利多")])                         # 窗口外不算
    labels = {("1111", "甲 Q3獲利創新高"): "B", ("1111", "甲 參展"): "N", ("1111", "甲 舊利多"): "B"}
    out = nv.summarize(news, ["1111", "2222"], "2026-10-15", TAX, bull_labels=labels).set_index("ticker")
    assert out.loc["1111", "bull14"] == 1 and out.loc["2222", "bull14"] == 0


def test_bull14_is_blank_not_zero_when_classifier_unavailable():
    news = _news([("1111", "2026-10-14", "甲 Q3獲利創新高")])
    out = nv.summarize(news, ["1111"], "2026-10-15", TAX, bull_labels=None)
    assert out["bull14"].isna().all()


def test_classifier_caches_and_returns_none_when_ollama_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "BULL_CACHE", tmp_path / "bull.csv")
    monkeypatch.setattr(nv, "_bull_prompt", lambda: ("p", "m", "u"))
    calls = []
    got = nv.classify_titles([("1111", "甲 獲利創高")], ask=lambda t, n, s: calls.append(s) or "B")
    assert got == {("1111", "甲 獲利創高"): "B"}
    again = nv.classify_titles([("1111", "甲 獲利創高")], ask=lambda t, n, s: (_ for _ in ()).throw(AssertionError))
    assert again == got and len(calls) == 1                                          # 快取命中不重判

    def down(t, n, s):
        raise ConnectionError("ollama down")
    assert nv.classify_titles([("2222", "乙 新標題")], ask=down) is None


def test_card_loader_passes_bull_and_tolerates_old_rows_without_it(tmp_path, monkeypatch):
    csv = tmp_path / "ml" / "reports" / "rere_news_volume.csv"
    csv.parent.mkdir(parents=True)
    pd.DataFrame([{"trade_date": "2026-10-19", "as_of_date": "2026-10-16", "ticker": "6532", "any14": 3,
                   "pos14": 0, "pos_rank": 1, "pool_n": 2, "bull14": 2},
                  {"trade_date": "2026-10-19", "as_of_date": "2026-10-16", "ticker": "1111", "any14": 1,
                   "pos14": 0, "pos_rank": 1, "pool_n": 2, "bull14": None}]).to_csv(csv, index=False)
    monkeypatch.setattr(entry_dashboard, "BASE_DIR", tmp_path)
    got = entry_dashboard.load_news_volume("2026-10-19")
    assert got["6532"]["bull"] == 2 and got["1111"]["bull"] is None
