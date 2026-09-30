import copy
import pandas as pd
import pytest
from scripts.stage_valuation_transfer_repair import official_valuation,repair_duplicate


def payload(market="上市"):
    names=["證券代號","證券名稱","本益比","股價淨值比","殖利率(%)"] if market=="上市" else ["股票代號","公司名稱","本益比","股價淨值比","殖利率(%)"]
    table={"fields":names,"data":[["3092","鴻碩","64.56","3.38","2.04"]]}
    return dict(stat="OK",date="20210510",**table) if market=="上市" else dict(stat="ok",date="20210510",tables=[table])


@pytest.mark.parametrize("market",["上市","上櫃"])
def test_named_source_columns_survive_reordering(market):
    data=payload(market)
    table=data if market=="上市" else data["tables"][0]
    table["fields"].reverse()
    table["data"][0].reverse()
    result=official_valuation(data,market,"20210510").iloc[0]
    assert (result.Ticker,result.PE_Ratio,result.PB_Ratio,result.Dividend_Yield)==("3092",64.56,3.38,2.04)


@pytest.mark.parametrize("change",["date","duplicate","ragged","inf"])
def test_invalid_official_payload_rejected(change):
    data=payload()
    if change=="date":data["date"]="20210518"
    elif change=="duplicate":data["data"].append(data["data"][0][:])
    elif change=="ragged":data["data"][0].pop()
    else:data["data"][0][2]="inf"
    with pytest.raises(ValueError):official_valuation(data,"上市","20210510")


def test_official_unknown_is_preserved_and_numeric_zero_is_known():
    data=payload()
    data["data"][0][2:]=["--","0","0.00"]
    row=official_valuation(data,"上市","20210510").iloc[0]
    assert pd.isna(row.PE_Ratio) and row.PB_Ratio==0 and row.Dividend_Yield==0


def test_remove_only_proven_wrong_market_before_listing():
    valid=official_valuation(payload("上櫃"),"上櫃","20210510")
    wrong=valid.assign(Market="上市",PE_Ratio=51.05)
    source=pd.concat([wrong,valid],ignore_index=True)
    empty=valid.iloc[:0]
    repaired=repair_duplicate(source,empty,valid,day="20210510",listing_date="20210513",ticker="3092")
    pd.testing.assert_frame_equal(repaired,valid)
    with pytest.raises(ValueError,match="on or after"):
        repair_duplicate(source,empty,valid,day="20210513",listing_date="20210513",ticker="3092")
    with pytest.raises(ValueError,match="market presence"):
        repair_duplicate(source,wrong,valid,day="20210510",listing_date="20210513",ticker="3092")


def test_conflicting_retained_market_value_prevents_partial_repair():
    valid=official_valuation(payload("上櫃"),"上櫃","20210510")
    source=pd.concat([valid.assign(Market="上市"),valid.assign(PE_Ratio=1)],ignore_index=True)
    with pytest.raises(AssertionError):
        repair_duplicate(source,valid.iloc[:0],valid,day="20210510",listing_date="20210513",ticker="3092")
