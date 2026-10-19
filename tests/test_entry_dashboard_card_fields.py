# -*- coding: utf-8 -*-
"""網頁 card() 讀到的每個卡片欄位,都要在 build() 寫進網頁的白名單裡。

2026-10-19:build() 算好了 r["news"],但白名單沒列 news,新聞14天/📈 那行從 10/16 上線起從未顯示;
當時的測試只檢查版型字串,沒有檢查資料真的送進網頁。
"""
import re

from scripts import entry_dashboard as ed


def _card_function_body() -> str:
    start = ed.HTML.index("function card(r)")
    end = ed.HTML.index("\nfunction ", start + 10)
    return ed.HTML[start:end]


def test_every_field_card_reads_is_exported_to_the_page():
    used = set(re.findall(r"\br\.(\w+)", _card_function_body()))
    missing = sorted(used - set(ed.CARD_ROW_FIELDS))
    assert not missing, f"card() 讀了但沒寫進網頁的欄位:{missing}(加到 entry_dashboard.CARD_ROW_FIELDS)"


def test_news_line_is_exported():
    assert "news" in ed.CARD_ROW_FIELDS and "news" in set(re.findall(r"\br\.(\w+)", _card_function_body()))
