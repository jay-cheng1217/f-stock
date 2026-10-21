# -*- coding: utf-8 -*-
"""官方重大訊息(MOPS)警示判定——名單否決(generate_entry_candidates)與產業新聞日報(fetch_industry_news)共用同一份規則。

警示 = 標題(去空白)含下列關鍵字之一、不是庫藏股,且不屬於放行的「非本公司事件」。
放行六類(#46 BT-mops-veto-precision PASS,2026-10-22 訊號起;依據、驗證與回測見 docs/STRATEGY_overview.md #46):
子公司層級、代(子)公司公告、本公司買賣/投資/認購他公司私募或可轉債、本公司參與他公司現金增資、買回股份(同庫藏股)、
法說會(行事曆,產生器另標 📅)。regex 與 scripts/backtest_mops_veto_precision.py 的 EXEMPT 相同,改動須重跑該回測。
"""
from __future__ import annotations

import re

MOPS_ALERT_KW = ("持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資")   # 收窄:裸「董事」會誤殺例行董事會公告
_ENTITY = r"(公司|證券|期貨|銀行|人壽|控股|投控|Co\.|Ltd|Inc|L\.?P|Fund|基金)"
MOPS_ALERT_EXEMPT = (
    re.compile(r"子公司"),                                                                   # 子公司/孫公司層級
    re.compile(r"(^|[)）]|金控|控股)代(?!收|理)"),                                             # 代(子)公司公告
    re.compile(r"(取得|處分|贖回|投資(?!人)|認購)(?:(?!本公司).){0,40}(私募|可轉換|可交換)"),       # 本公司買賣/投資他公司私募或可轉債
    re.compile(r"(參與|投資(?!人)|取得|認購)(?=(?:(?!本公司).){1,40}現金增資)(?=.{0,40}" + _ENTITY + r".{0,25}現金增資)"),
                                                                                             # 本公司參與他公司現金增資
    re.compile(r"買回股份"),                                                                  # 同庫藏股
    re.compile(r"法人說明會|法說"),                                                            # 行事曆,不是警示
)


def is_mops_alert(title: str) -> bool:
    """本公司稀釋或內部人轉讓類的官方警示(標題去空白後判定)。"""
    t = re.sub(r"\s+", "", str(title))
    return (any(k in t for k in MOPS_ALERT_KW) and "庫藏股" not in t
            and not any(p.search(t) for p in MOPS_ALERT_EXEMPT))
