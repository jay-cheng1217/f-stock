# -*- coding: utf-8 -*-
"""#46:官方警示只擋本公司稀釋或內部人轉讓;子公司層級、代公司公告、本公司買賣/參與他公司證券、買回股份、法說會不擋。
標題取自 新聞資料/announcements_*.csv 的實際公告(2026-03~10)。"""
import pytest

from scripts import mops_alert as m

OWN_EVENTS = [   # 本公司事件:仍要擋
    "公告本公司現金增資認股基準日及相關事宜",
    "公告本公司董事會決議與3MUnitedKingdomLimited簽署私募普通股認購協議書",
    "公告本公司114年度現金增資全體董事放棄認購股數達得認購股數二分之一以上，並洽特定人認購事宜。",
    "公告本公司董事會決議辦理減資彌補虧損",
    "本公司董事會決議115年第一次私募普通股定價相關事宜及公告應募人",
    "公告本公司115年現金增資認股基準日等相關事宜\r\n(補充發行價格、委託代收及存儲價款行庫)",
    "元大期貨公告訂定現金增資認購價格暨代收及專戶存儲價款行庫等相關事宜",
    "公告本公司115年現金增資認股基準日,發行價格及繳款期間等相關事宜(補充對投資人權益之責任參見其他應敘明事項之(3))",
]
RELEASED = [     # 非本公司事件:不擋
    "代子公司南亞科技國際有限公司公告董事會決議現金增資",
    "公告本公司取得私募基金 Andra Capital Fund LP",
    "公告本公司董事會決議認購鑽全實業股份有限公司私募普通股",
    "本公司董事會決議參與榮福股份有限公司現金增資",
    "統一綜合證券(股)公司公告認購統一期貨現金增資",
    "本公司董事會決議註銷買回股份辦理減資事宜",
    "本公司受邀參加2026台灣創投暨私募投資年會-證交所專場之法人說明會",
    "富邦金控代富邦期貨公告決議辦理現金增資",
    "元大金控代元大證券(韓國)公告取得KMWInc.發行之第11次私募可轉換公司債",
    "本公司董事會決議處分芯鼎科技股份有限公司私募普通股",
]


@pytest.mark.parametrize("title", OWN_EVENTS)
def test_own_company_dilution_and_insider_events_stay_alerts(title):
    assert m.is_mops_alert(title)


@pytest.mark.parametrize("title", RELEASED)
def test_subsidiary_third_party_and_calendar_titles_are_released(title):
    assert not m.is_mops_alert(title)


def test_treasury_stock_and_unrelated_titles_are_not_alerts():
    assert not m.is_mops_alert("本公司董事會決議買回庫藏股並辦理減資")
    assert not m.is_mops_alert("公告本公司董事會決議發放現金股利")


def test_generator_and_news_report_share_one_rule():
    from scripts import fetch_industry_news as news
    from scripts import generate_entry_candidates as gen
    assert gen._is_mops_alert is m.is_mops_alert and news.is_mops_alert is m.is_mops_alert
    assert gen.MOPS_ALERT_KW == m.MOPS_ALERT_KW
