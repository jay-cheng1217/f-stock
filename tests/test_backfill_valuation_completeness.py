from datetime import date

import pandas as pd
import pytest

from scripts import backfill_valuation
from scripts.valuation_source_contract import parse_official_valuation


def _official_payload(market):
    # Actual official 2021-05-10 source schemas and rows, fetched2026-09-06.
    if market=="上市":
        return {"date":"20210510","stat":"OK","fields":["證券代號","證券名稱","收盤價","殖利率(%)","股利年度","本益比","股價淨值比","財報年/季"],"data":[["1101","台泥","52.90","6.62",109,"12.51","1.54","109/4"]]}
    return {"date":"20210510","stat":"ok","tables":[{"date":"110/05/10","fields":["股票代號","公司名稱","本益比","每股股利","股利年度","殖利率(%)","股價淨值比"],"data":[["1240","茂生農經        ","12.70","2.50000000",109,"4.43","1.75"]]}]}


def _frame(market, count, prefix):
    return pd.DataFrame(
        {
            "Ticker": [f"{prefix}{index:04d}" for index in range(count)],
            "Name": ["test"] * count,
            "PE_Ratio": [20.0] * count,
            "PB_Ratio": [2.0] * count,
            "Dividend_Yield": [3.0] * count,
            "Market": [market] * count,
        }
    )


def test_pending_selection_does_not_delete_partial_file(tmp_path):
    path = tmp_path / "valuation_20260701.csv"
    _frame("上市", 10, "1").to_csv(path, index=False, encoding="utf-8-sig")

    pending = backfill_valuation.select_pending_days(
        [date(2026, 7, 1)],
        str(tmp_path),
    )

    assert pending == [date(2026, 7, 1)]
    assert path.exists()


def test_single_market_response_is_rejected():
    twse = backfill_valuation.SourceResult("ok", _frame("上市", 1000, "1"))
    tpex = backfill_valuation.SourceResult("error", error="timeout")

    with pytest.raises(ValueError, match="TPEx=error"):
        backfill_valuation.combine_complete_sources(twse, tpex)


def test_two_markets_below_row_floor_are_rejected():
    twse = backfill_valuation.SourceResult("ok", _frame("上市", 700, "1"))
    tpex = backfill_valuation.SourceResult("ok", _frame("上櫃", 700, "6"))

    with pytest.raises(ValueError, match="1400"):
        backfill_valuation.combine_complete_sources(twse, tpex)


def test_complete_two_market_response_is_combined():
    twse = backfill_valuation.SourceResult("ok", _frame("上市", 900, "1"))
    tpex = backfill_valuation.SourceResult("ok", _frame("上櫃", 700, "6"))

    combined = backfill_valuation.combine_complete_sources(twse, tpex)

    assert len(combined) == 1600
    assert set(combined["Market"]) == {"上市", "上櫃"}


@pytest.mark.parametrize("market",["上市","上櫃"])
def test_fetch_real_schema_uses_names_after_columns_move(monkeypatch,market):
    from types import SimpleNamespace
    data=_official_payload(market)
    table=data if market=="上市" else data["tables"][0]
    table["fields"].reverse()
    table["data"][0].reverse()
    monkeypatch.setattr(backfill_valuation.SESSION,"get",lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:data))
    result=backfill_valuation.fetch_twse_valuation("20210510") if market=="上市" else backfill_valuation.fetch_tpex_valuation(date(2021,5,10))
    assert result.status=="ok"
    row=result.frame.iloc[0]
    assert (row.PE_Ratio,row.PB_Ratio,row.Dividend_Yield)==((12.51,1.54,6.62) if market=="上市" else (12.70,1.75,4.43))


@pytest.mark.parametrize("market",["上市","上櫃"])
@pytest.mark.parametrize("fault",["date","missing_date","empty","missing_fields","duplicate","ragged","inf","ambiguous_table"])
def test_fetch_bad_source_never_returns_success_or_legal_no_data(monkeypatch,market,fault):
    from types import SimpleNamespace
    import copy
    data=_official_payload(market)
    table=data if market=="上市" else data["tables"][0]
    if fault=="date":data["date"]="20210518"
    elif fault=="missing_date":data.pop("date")
    elif fault=="empty":table["data"]=[]
    elif fault=="missing_fields":table.pop("fields")
    elif fault=="duplicate":table["data"].append(table["data"][0][:]);table["data"][-1][0]+=" "
    elif fault=="ragged":table["data"][0].pop()
    elif fault=="inf":table["data"][0][table["fields"].index("本益比")]="inf"
    elif market=="上櫃":data["tables"].append(copy.deepcopy(table))
    else:table["fields"].append(table["fields"][0]);table["data"][0].append(table["data"][0][0])
    monkeypatch.setattr(backfill_valuation,"MAX_RETRIES",1)
    monkeypatch.setattr(backfill_valuation.SESSION,"get",lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:data))
    result=backfill_valuation.fetch_twse_valuation("20210510") if market=="上市" else backfill_valuation.fetch_tpex_valuation(date(2021,5,10))
    assert result.status=="error" and result.frame is None and result.error


def test_market_collision_blocks_write_even_with_enough_rows():
    twse=backfill_valuation.SourceResult("ok",_frame("上市",900,"1"))
    tpex=backfill_valuation.SourceResult("ok",_frame("上櫃",700,"6"))
    tpex.frame.loc[0,"Ticker"]=" "+twse.frame.loc[0,"Ticker"]+" "
    with pytest.raises(ValueError,match="重複券碼"):
        backfill_valuation.combine_complete_sources(twse,tpex)


def test_tpex_embedded_roc_date_must_match_top_level_date():
    data=_official_payload("上櫃")
    data["tables"][0]["date"]="110/05/18"
    with pytest.raises(ValueError,match="table date"):
        parse_official_valuation(data,"上櫃","20210510")


def test_tpex_official_literal_null_is_unknown_without_changing_zero():
    data = _official_payload("上櫃")
    table = data["tables"][0]
    table["data"][0][table["fields"].index("本益比")] = "null"
    table["data"][0][table["fields"].index("殖利率(%)")] = "0"
    result = parse_official_valuation(data, "上櫃", "20210510")
    assert pd.isna(result.PE_Ratio.iloc[0]) and result.Dividend_Yield.iloc[0] == 0


def test_shared2026calendar_excludes_settlement_and_cny_without_network(monkeypatch):
    def unexpected(*a,**k):raise AssertionError("2026 canonical calendar should not need network")
    monkeypatch.setattr(backfill_valuation.SESSION,"get",unexpected)
    assert backfill_valuation.generate_trading_days(date(2026,2,9),date(2026,2,20))==[date(2026,2,d) for d in (9,10,11)]


def test_unsupported_year_requires_exact_official_calendar(monkeypatch):
    from types import SimpleNamespace
    calendar={"date":"20210101","queryYear":2021,"stat":"OK","fields":["日期","名稱","說明"],"data":[
        ["2021-02-05","農曆春節前最後交易日","農曆春節前最後交易。"],
        ["2021-02-08","農曆春節前最後交易日","2月8日市場無交易，僅辦理結算交割作業。"],
        ["2021-02-09","農曆春節前最後交易日","2月9日市場無交易，僅辦理結算交割作業。"]]}
    monkeypatch.setattr(backfill_valuation,"_VERIFIED_MARKET_YEAR_CLOSURES",{})
    calls=[]
    def fetch(*a,**k):
        calls.append(k["params"])
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:calendar)
    monkeypatch.setattr(backfill_valuation.SESSION,"get",fetch)
    assert backfill_valuation.generate_trading_days(date(2021,2,5),date(2021,2,9))==[date(2021,2,5)]
    assert calls==[{"date":"20210101","response":"json"}]
    backfill_valuation.generate_trading_days(date(2021,2,5),date(2021,2,9))
    assert len(calls)==1


def test_server_ignoring_historical_year_fails_closed(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(backfill_valuation,"_VERIFIED_MARKET_YEAR_CLOSURES",{})
    monkeypatch.setattr(backfill_valuation.SESSION,"get",lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"date":"20260101","queryYear":2026,"stat":"OK"}))
    with pytest.raises(ValueError,match="calendar year"):
        backfill_valuation.generate_trading_days(date(2021,2,5),date(2021,2,9))


def test_annual_calendar_unknown_event_is_not_assumed_tradable():
    from scripts.valuation_source_contract import parse_official_market_holidays
    data={"date":"20210101","queryYear":2021,"stat":"OK","fields":["日期","名稱","說明"],"data":[["2021-02-08","新事件","需要人工判定"]]}
    with pytest.raises(ValueError,match="Unclassified"):
        parse_official_market_holidays(data,2021)
