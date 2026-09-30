import pandas as pd
import pytest

from scripts.enrich_new_stock_sources import CoverageContractError, FUND_COLS, MARGIN_COLS, exact_enrich


def test_exact_enrichment_preserves_unknown_and_observed_zero():
    bars = pd.DataFrame([{"Ticker": "3718", "Date": "2026-09-03", "Open": 85., "High": 86.,
        "Low": 77.2, "Close": 78.3, "Volume": 3400000}, {"Ticker": "3718", "Date": "2026-09-04",
        "Open": 80., "High": 80.5, "Low": 75.2, "Close": 75.5, "Volume": 4129000}])
    fund = pd.DataFrame([{"Ticker": "3718", "Date": "2026-09-03", "Foreign_BuySell": 0,
                         "Trust_BuySell": -2, "Dealer_BuySell": None}])
    margin = pd.DataFrame(columns=["Ticker", "Date", *MARGIN_COLS])
    result = exact_enrich(bars, fund, margin)
    assert result.loc[0, "Foreign_BuySell"] == 0
    assert result.loc[0, "Trust_BuySell"] == -2
    assert pd.isna(result.loc[0, "Dealer_BuySell"])
    assert result.loc[1, FUND_COLS].isna().all()
    assert result[MARGIN_COLS].isna().all().all()
    pd.testing.assert_frame_equal(result[bars.columns], bars)


def test_conflicting_source_duplicate_is_rejected():
    bars = pd.DataFrame(columns=["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"])
    fund = pd.DataFrame([{"Ticker": "3718", "Date": "2026-09-03", **dict.fromkeys(FUND_COLS, x)} for x in [0, 1]])
    with pytest.raises(CoverageContractError, match="Conflicting"):
        exact_enrich(bars, fund, pd.DataFrame(columns=["Ticker", "Date", *MARGIN_COLS]))


def test_canonical_isin_parser_includes_innovation_board(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_BASE_DIR", str(tmp_path))
    from scripts import fetch_sector_mapping as mod

    html = '''<table class="h4">
      <tr><td colspan="7">股票</td></tr>
      <tr><td>2330　台積電</td><td>TW0002330008</td><td>1994/09/05</td><td>上市</td><td>半導體業</td><td>ESVUFR</td><td></td></tr>
      <tr><td colspan="7">創新板</td></tr>
      <tr><td>4590　富田-創</td><td>TW0004590005</td><td>2026/01/29</td><td>上市臺灣創新板</td><td>電機機械</td><td>ESVUFR</td><td></td></tr>
      <tr><td>7610　聯友金屬-創</td><td>TW0007610B14</td><td>2025/09/09</td><td>上市臺灣創新板</td><td>綠能環保</td><td>ESVUFR</td><td></td></tr>
      <tr><td>1234　not-common</td><td>x</td><td>2026/01/01</td><td>x</td><td>x</td><td>DWXXXX</td><td></td></tr>
      <tr><td colspan="7">ETF</td></tr>
      <tr><td>0050　ETF</td><td>x</td><td>x</td><td>x</td><td>x</td><td>EUXXXX</td><td></td></tr>
    </table>'''
    class Response:
        status_code = 200
        content = html.encode("cp950")
    monkeypatch.setattr(mod.SESSION, "get", lambda *a, **k: Response())
    rows = mod.fetch_isin_listing(2, "上市")
    assert [r["Ticker"] for r in rows] == ["2330", "4590", "7610"]
    assert [r["Sector"] for r in rows] == ["半導體業", "電機機械", "綠能環保"]
