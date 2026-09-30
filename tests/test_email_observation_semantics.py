from html.parser import HTMLParser

import pandas as pd

from scripts import send_daily_email


def test_retired_desktop_cannot_claim_updated_even_with_legacy_user_action(monkeypatch):
    monkeypatch.setattr(send_daily_email, "_chipk_mobile_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(send_daily_email, "_load_chipk_status", lambda: {
        "status": "retired", "asof_date": None, "user_action": "桌面版快照已更新",
    })

    section = send_daily_email._build_chipk_mobile_review_section("2026-09-04", pd.DataFrame())

    assert "桌面自動來源已退役" in section
    assert "人工截圖確認" in section
    assert "已更新" not in section


def test_unknown_desktop_status_has_no_default_freshness_claim(monkeypatch):
    monkeypatch.setattr(send_daily_email, "_chipk_mobile_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(send_daily_email, "_load_chipk_status", lambda: {})

    section = send_daily_email._build_chipk_mobile_review_section("2026-09-04", pd.DataFrame())

    assert "日期與狀態仍需確認" in section
    assert "已更新" not in section


def test_daily_bar_review_is_not_intraday_execution_confirmation():
    touched, held, conclusion = send_daily_email._entry_support_review(
        entry_low=100.0, entry_high=110.0, stop_loss=95.0,
        ohlc={"open": 103.0, "high": 112.0, "low": 98.0, "close": 105.0},
        special_blocked=False, formal_ok=True,
    )

    assert touched == "有"
    assert held == "有守停損但有下洗"
    assert "日K不能證明盤中守穩" in conclusion
    assert "下次觸價仍待確認" in conclusion


def test_candidate_layout_keeps_stock_order_and_full_width_evidence(monkeypatch):
    class TableRows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.cell = None
            self.field_label = False

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.rows.append([])
            elif tag == "td":
                self.cell = {"attrs": dict(attrs), "text": ""}
                self.rows[-1].append(self.cell)
            elif tag == "span" and dict(attrs).get("class") == "entry-field-label":
                self.field_label = True

        def handle_endtag(self, tag):
            if tag == "td":
                self.cell = None
            elif tag == "span":
                self.field_label = False

        def handle_data(self, data):
            if self.cell is not None and not self.field_label:
                self.cell["text"] += data

    rows = [dict(
        ticker=ticker, name=f"名稱{ticker}", scope="觀察", label="等待確認", sector="測試",
        entry_zone="100.00 ~ 110.00", entry_limit=105, stop_loss=95,
        ohlc_text="103 / 112 / 98 / 105", source_date="2026-09-04",
        zone_touched="有", support_hold="待確認", conclusion=f"結論{ticker}",
        reason=f"證據{ticker}", support_tags="有量", missing_tags="缺盤中確認",
    ) for ticker in ("2603", "2330")]
    monkeypatch.setattr(send_daily_email, "_build_entry_candidate_records", lambda *_a, **_kw: (rows, "fixture.csv", {}))
    monkeypatch.setattr(send_daily_email, "_load_json_object", lambda *_a: {})

    parser = TableRows()
    parser.feed(send_daily_email._build_entry_candidate_email_section("2026-09-04", pd.DataFrame()))
    rendered = [row for row in parser.rows if row]

    assert [len(row) for row in rendered] == [6, 1, 6, 1]
    assert [rendered[i][0]["text"] for i in (0, 2)] == ["2603名稱2603", "2330名稱2330"]
    for i, ticker in ((0, "2603"), (2, "2330")):
        assert rendered[i][2]["text"] == "100.00 ~ 110.00掛價參考 105.00"
        assert "日K資料日：2026-09-04（盤後）" in rendered[i][3]["text"]
        assert all(cell["attrs"].get("data-label") for cell in rendered[i])
        assert rendered[i + 1][0]["attrs"]["colspan"] == "6"
        assert rendered[i + 1][0]["text"] == f"結論：結論{ticker}證據{ticker}符合：有量缺少：缺盤中確認"
