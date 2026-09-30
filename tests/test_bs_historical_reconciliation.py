import pandas as pd
import pytest

from scripts.stage_bs_historical_reconciliation import identity_inventory
from scripts.stage_q2_financial_reconciliation import compare_and_merge
from scripts.stage_bs_retained_official_reconciliation import parse_individual_anchor


def test_retained_bad_history_is_reported_not_deleted_or_filled():
    before = pd.DataFrame({"Ticker": ["2330", "5371", "2867"],
        "Total_Assets": [10.0, 10.0, 10.0], "Total_Liabilities": [4.0, 4.0, 4.0],
        "Total_Equity": [6.0, 0.0, float("nan")]})
    official = before.iloc[:1].copy()
    merged, report = compare_and_merge(before, official)
    assert len(merged) == 3 and report["retained_omitted_historical_rows"] == ["2867", "5371"]
    inventory = identity_inventory(merged)
    assert inventory == {"known_rows": 2, "unknown_rows": 1, "violation_tickers": ["5371"]}
    assert pd.isna(merged.set_index("Ticker").loc["2867", "Total_Equity"])


def test_official_correction_repairs_source_identity_without_redefining_unknown():
    before = pd.DataFrame({"Ticker": ["2330"], "Total_Assets": [10],
                           "Total_Liabilities": [4], "Total_Equity": [0]})
    official = before.assign(Total_Equity=6)
    merged, report = compare_and_merge(before, official)
    assert identity_inventory(before)["violation_tickers"] == ["2330"]
    assert identity_inventory(merged)["violation_tickers"] == []
    assert report["existing_changes"] == [{"Ticker": "2330", "column": "Total_Equity", "old": 0, "new": 6}]


def test_individual_statement_explicit_year_and_current_column_anchor():
    # Account/date values taken from official 5371 109Q1 t164sb03 response.
    html = '''<h2>民國109年第1季</h2><table><tr><th>109年03月31日</th><th>108年12月31日</th></tr>
      <tr><td>資產總額</td><td>45,335,501</td><td>100.00</td><td>49,323,120</td></tr>
      <tr><td>負債總額</td><td>22,905,183</td><td>50.52</td><td>25,770,560</td></tr>
      <tr><td>權益總額</td><td>22,430,318</td><td>49.48</td><td>23,552,560</td></tr></table>'''
    assert parse_individual_anchor(html, 2020, 1) == {
        "Total_Assets": 45335501, "Total_Liabilities": 22905183, "Total_Equity": 22430318}
    with pytest.raises(ValueError, match="year/quarter"):
        parse_individual_anchor(html, 2021, 1)
    with pytest.raises(ValueError, match="accounting date"):
        parse_individual_anchor(html.replace("109年03月31日", "108年03月31日"), 2020, 1)
    with pytest.raises(ValueError, match="identity"):
        parse_individual_anchor(html.replace("22,430,318", "1,000"), 2020, 1)
