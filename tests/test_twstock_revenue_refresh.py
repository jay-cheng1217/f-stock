# -*- coding: utf-8 -*-
"""月營收更新的三個缺陷修正(2026-10-17 #37):空值列要重抓、讀不到舊檔不可覆寫、以官方彙總改正事後更正。"""
import pandas as pd

import twstock as tw

COLS = ["Date", "Name", "Monthly_Revenue", "Cumulative_Revenue", "YoY_pct_change", "Cumulative_YoY_pct_change"]


def _write(path, rows):
    pd.DataFrame(rows, columns=COLS).to_csv(path, index=False, encoding="utf-8-sig")


def _fake_fetch(values):
    def fetch(session, ticker, year, month):
        ym = f"{year}-{month:02d}"
        return {"Date": ym, "Name": "測試", "Monthly_Revenue": values[ym], "Cumulative_Revenue": values[ym],
                "YoY_pct_change": 1.0, "Cumulative_YoY_pct_change": 1.0} if ym in values else None
    return fetch


def test_rows_with_empty_revenue_are_refetched(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "REVENUE_DIR", str(tmp_path))
    _write(tmp_path / "revenue_1590.csv", [["2025-03", "亞德客-KY", None, None, None, None],
                                           ["2025-04", "亞德客-KY", 3600000, 7000000, 5.0, 6.0]])
    monkeypatch.setattr(tw, "_fetch_revenue_month", _fake_fetch({"2025-03": 3505568}))
    n, failed = tw._update_ticker_revenue(None, "1590", [(2025, 3), (2025, 4)])
    out = pd.read_csv(tmp_path / "revenue_1590.csv", dtype={"Date": str}).set_index("Date")
    assert n == 1 and not failed
    assert out.loc["2025-03", "Monthly_Revenue"] == 3505568 and out.loc["2025-04", "Monthly_Revenue"] == 3600000


def test_unreadable_existing_file_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "REVENUE_DIR", str(tmp_path))
    bad = tmp_path / "revenue_1234.csv"
    bad.write_text("this,is,not\na,revenue,file\n", encoding="utf-8")
    monkeypatch.setattr(tw, "_fetch_revenue_month", _fake_fetch({"2026-09": 100}))
    n, failed = tw._update_ticker_revenue(None, "1234", [(2026, 9)])
    assert n == 0 and failed == [("1234", None, None)]
    assert bad.read_text(encoding="utf-8") == "this,is,not\na,revenue,file\n"


def test_new_month_is_appended_and_history_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "REVENUE_DIR", str(tmp_path))
    _write(tmp_path / "revenue_6532.csv", [[f"2020-{m:02d}", "瑞耘", 1000 + m, 1000, 1.0, 1.0] for m in range(1, 13)])
    monkeypatch.setattr(tw, "_fetch_revenue_month", _fake_fetch({"2026-09": 80069}))
    n, _ = tw._update_ticker_revenue(None, "6532", [(2026, 9)])
    out = pd.read_csv(tmp_path / "revenue_6532.csv", dtype={"Date": str})
    assert n == 1 and len(out) == 13 and out["Date"].iloc[0] == "2020-01"


def test_corrections_use_official_value_but_keep_rounding_and_never_add_months(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "REVENUE_DIR", str(tmp_path))
    _write(tmp_path / "revenue_2392.csv", [["2026-06", "正崴", 5249279, 1, 4.70, 1.0],      # 只差四捨五入
                                           ["2026-07", "正崴", 8941317, 2, 30.0, 2.0],      # 事後更正
                                           ["2026-08", "正崴", None, None, None, None]])     # 空值
    summary = pd.DataFrame([
        {"ticker": "2392", "Date": "2026-06", "Name": "正崴", "Monthly_Revenue": 5249279, "Cumulative_Revenue": 1,
         "YoY_pct_change": 4.69, "Cumulative_YoY_pct_change": 1.0},
        {"ticker": "2392", "Date": "2026-07", "Name": "正崴", "Monthly_Revenue": 6089052, "Cumulative_Revenue": 37887863,
         "YoY_pct_change": -9.57, "Cumulative_YoY_pct_change": -23.44},
        {"ticker": "2392", "Date": "2026-08", "Name": "正崴", "Monthly_Revenue": 5072777, "Cumulative_Revenue": 42960640,
         "YoY_pct_change": -3.0, "Cumulative_YoY_pct_change": -20.0},
        {"ticker": "2392", "Date": "2026-09", "Name": "正崴", "Monthly_Revenue": 1, "Cumulative_Revenue": 1,
         "YoY_pct_change": 1.0, "Cumulative_YoY_pct_change": 1.0},
        {"ticker": "9999", "Date": "2026-07", "Name": "不存在", "Monthly_Revenue": 1, "Cumulative_Revenue": 1,
         "YoY_pct_change": 1.0, "Cumulative_YoY_pct_change": 1.0},
        {"ticker": "2392", "Date": "2026-07", "Name": "正崴", "Monthly_Revenue": 6089052, "Cumulative_Revenue": 37887863,
         "YoY_pct_change": -9.57, "Cumulative_YoY_pct_change": -23.44}])   # 同月同公司重複出現(真實彙總會發生)
    log = tmp_path / "corr.csv"
    fixes = tw._apply_revenue_corrections(summary, ["2392", "9999"], log_path=str(log))
    out = pd.read_csv(tmp_path / "revenue_2392.csv", dtype={"Date": str}).set_index("Date")
    assert {(f["Date"], f["local"]) for f in fixes} == {("2026-07", 8941317.0), ("2026-08", None)}
    assert out.loc["2026-07", "Monthly_Revenue"] == 6089052 and out.loc["2026-07", "YoY_pct_change"] == -9.57
    assert out.loc["2026-08", "Monthly_Revenue"] == 5072777
    assert out.loc["2026-06", "YoY_pct_change"] == 4.70                      # 四捨五入差不改
    assert "2026-09" not in out.index and not (tmp_path / "revenue_9999.csv").exists()   # 不新增月份、不新增檔案
    assert len(pd.read_csv(log)) == 2


def test_summary_parser_reads_the_two_level_mops_header():
    cols = pd.MultiIndex.from_tuples([
        ("Unnamed: 0_level_0", "公司 代號"), ("Unnamed: 1_level_0", "公司名稱"), ("營業收入", "當月營收"),
        ("營業收入", "上月營收"), ("營業收入", "去年當月營收"), ("營業收入", "上月比較 增減(%)"),
        ("營業收入", "去年同月 增減(%)"), ("累計營業收入", "當月累計營收"), ("累計營業收入", "去年累計營收"),
        ("累計營業收入", "前期比較 增減(%)"), ("備註", "備註")])
    t = pd.DataFrame([["2392", "正崴", 6089052, 0, 0, 0, -9.57, 37887863, 0, -23.44, "-"],
                      ["合計", "", 1, 0, 0, 0, 0, 0, 0, 0, "-"]], columns=cols)
    out = tw._parse_revenue_summary_html(t.to_html(), "2026-07").set_index("ticker")
    assert list(out.index) == ["2392"]
    assert out.loc["2392", "Monthly_Revenue"] == 6089052 and out.loc["2392", "Cumulative_YoY_pct_change"] == -23.44
    assert out.loc["2392", "Date"] == "2026-07"


def test_incomplete_summary_month_is_skipped(monkeypatch):
    class R:
        status_code, content = 200, b"<html></html>"

    class S:
        def get(self, *a, **k):
            return R()
    monkeypatch.setattr(tw.time, "sleep", lambda s: None)
    assert tw._fetch_revenue_summary(S(), 2026, 7) is None
