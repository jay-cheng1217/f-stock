import copy
import ast
from pathlib import Path

import numpy as np

import pytest

from scripts.stage_historical_gaps import validate_foreign_payload


def payload():
    return {"stat":"OK","date":"20260807", "fields":["全體外資及陸資持股比率","發行股數","證券代號","全體外資及陸資持有股數"],
            "data":[["12.34","1,000,000","2330","123,456"]]}


def test_named_fields_handle_different_order():
    frame, check=validate_foreign_payload(payload(),"TWSE","20260807")
    assert frame.loc[0,"Foreign_Held"]==123456
    assert frame.loc[0,"Issued_Shares"]==1000000
    assert check["unknown_rows"]==0


def test_wrong_response_date_rejected():
    with pytest.raises(ValueError,match="date"):
        validate_foreign_payload(payload(),"TWSE","20260806")


def test_missing_field_rejected():
    p=payload()
    p["fields"][0]="foreign guess"
    with pytest.raises(ValueError,match="fields"):
        validate_foreign_payload(p,"TWSE","20260807")


def test_mixed_units_rejected():
    p=payload()
    p["data"][0][1]="1,000,000,000"
    with pytest.raises(ValueError,match="unit"):
        validate_foreign_payload(p,"TWSE","20260807")


def test_official_unknown_preserved_not_zeroed():
    p=payload()
    p["data"][0][0]="--"
    frame,check=validate_foreign_payload(p,"TWSE","20260807")
    assert frame.Foreign_Pct.isna().all()
    assert check["unknown_rows"]==1


def test_unknown_does_not_hide_other_invalid_number():
    p=payload()
    p["data"][0][0]="--"
    p["data"][0][1]="-100"
    with pytest.raises(ValueError,match="range"):
        validate_foreign_payload(p,"TWSE","20260807")


def test_duplicate_ticker_rejected():
    p=payload()
    p["data"].append(copy.deepcopy(p["data"][0]))
    with pytest.raises(ValueError,match="Duplicate"):
        validate_foreign_payload(p,"TWSE","20260807")


@pytest.mark.parametrize("value,expected", [(0,0.0),(0.0,0.0),("0",0.0),("0.00%",0.0),("1,234",1234.0)])
def test_canonical_twstock_number_keeps_observed_zero(value,expected):
    # Execute the real pure function without twstock's unrelated startup I/O.
    tree=ast.parse((Path(__file__).resolve().parents[1]/"twstock.py").read_text(encoding="utf-8-sig"))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_parse_twse_number")
    namespace={"np":np}
    exec(compile(ast.Module(body=[node],type_ignores=[]),"twstock._parse_twse_number","exec"),namespace)
    assert namespace["_parse_twse_number"](value)==expected


@pytest.mark.parametrize("value", [None,"","  ","--","N/A","bad",float("nan")])
def test_canonical_twstock_missing_remains_unknown(value):
    tree=ast.parse((Path(__file__).resolve().parents[1]/"twstock.py").read_text(encoding="utf-8-sig"))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_parse_twse_number")
    namespace={"np":np}
    exec(compile(ast.Module(body=[node],type_ignores=[]),"twstock._parse_twse_number","exec"),namespace)
    assert np.isnan(namespace["_parse_twse_number"](value))


def test_zero_repair_excludes_ambiguous_or_known_percentages():
    import pandas as pd
    from scripts.stage_foreign_zero_repair import repair_mask
    frame=pd.DataFrame({"Issued_Shares":[1000,1000000,1000,0,np.nan,1000,1000],
                        "Foreign_Held":[0,1,1,0,0,np.nan,0],
                        "Foreign_Pct":[np.nan,np.nan,np.nan,np.nan,np.nan,np.nan,2.]})
    assert repair_mask(frame).tolist()==[True,True,False,False,False,False,False]
