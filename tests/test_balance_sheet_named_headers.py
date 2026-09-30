from pathlib import Path

import pandas as pd
import pytest

from scripts.backfill_balance_sheet import parse_balance_sheet
from scripts.stage_q2_financial_reconciliation import compare_and_merge


@pytest.fixture
def official_rows():
    text = (Path(__file__).parent / "fixtures/mops_bs_115Q2_source_rows.html").read_text(encoding="utf-8")
    return parse_balance_sheet(text).set_index("Ticker")


@pytest.mark.parametrize("ticker,assets,liabilities,equity,bvps", [
    ("2801", 3535310278, 3305610200, 229700078, 19.52),
    ("2880", 4546467034, 4304726423, 241740611, 17.37),
    ("2816", 27241415, 17603145, 9638270, 43.10),
    ("1409", 253988650, 194568582, 59420068, 29.83),
    ("2330", 9375654727, 2901183746, 6474470981, 248.05),
    ("6026", 17386050, 11084339, 6301711, 15.91),
])
def test_real_industry_headers_and_accounting_equation(official_rows, ticker, assets, liabilities, equity, bvps):
    row = official_rows.loc[ticker]
    assert row.Total_Assets == assets
    assert row.Total_Liabilities == liabilities
    assert row.Total_Equity == equity
    assert row.Book_Value_Per_Share == bvps
    assert abs(row.Total_Assets - row.Total_Liabilities - row.Total_Equity) <= 1


def test_financial_current_categories_remain_not_applicable(official_rows):
    assert official_rows.loc[["2801", "2880", "2816"], ["Current_Assets", "Current_Liabilities"]].isna().all().all()
    assert official_rows.loc["1409", "Current_Assets"] == 178175523
    assert official_rows.loc["6026", "Parent_Equity"] == 6301711


def test_rowspan_colspan_headers_resolve_actual_leaf_columns():
    html = '''<table class="hasBorder">
      <tr><th rowspan="2">公司 代號</th><th rowspan="2">公司名稱</th><th colspan="3">財務總額</th><th rowspan="2">每股參考淨值</th></tr>
      <tr><th>資產總額</th><th>負債總額</th><th>權益總額</th></tr>
      <tr><td>2812</td><td>台中銀</td><td>1000</td><td>900</td><td>100</td><td>15.95</td></tr></table>'''
    row = parse_balance_sheet(html).iloc[0]
    assert row.Total_Assets == 1000
    assert row.Total_Liabilities == 900
    assert row.Total_Equity == 100
    assert row.Book_Value_Per_Share == 15.95
    assert pd.isna(row.Current_Assets)


def test_missing_or_ambiguous_total_header_is_rejected():
    html = '<table class="hasBorder"><tr><th>公司代號</th><th>公司名稱</th><th>現金及約當現金</th></tr><tr><td>2812</td><td>台中銀</td><td>100</td></tr></table>'
    with pytest.raises(ValueError, match="headers"):
        parse_balance_sheet(html)


def test_quarter_merge_preserves_previously_filed_retired_company():
    before = pd.DataFrame({"Ticker": ["5371", "1806"], "Name": ["original", "company"], "EPS_Basic": [1., -.07]})
    official = pd.DataFrame({"Ticker": ["1806", "7717"], "Name": ["company", "new"], "EPS_Basic": [-.06, 3.]})
    merged, report = compare_and_merge(before, official)
    assert merged.set_index("Ticker").loc["5371", "EPS_Basic"] == 1.
    assert merged.set_index("Ticker").loc["1806", "EPS_Basic"] == -.06
    assert report["retained_omitted_historical_rows"] == ["5371"]
    assert report["added"] == ["7717"]
