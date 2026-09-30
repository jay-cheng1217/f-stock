"""Strict, side-effect-free parsing of exact-date official valuation responses."""
from __future__ import annotations
from datetime import date
import re
import numpy as np
import pandas as pd

NUMERIC_COLUMNS=["PE_Ratio","PB_Ratio","Dividend_Yield"]
OUTPUT_COLUMNS=["Ticker","Name",*NUMERIC_COLUMNS,"Market"]


def _table_date(value):
    text=str(value).strip()
    if re.fullmatch(r"\d{8}",text):
        return text
    match=re.fullmatch(r"(\d{3,4})/(\d{1,2})/(\d{1,2})",text)
    if not match:
        raise ValueError("Unrecognized official valuation table date")
    year,month,day=map(int,match.groups())
    return date(year+1911 if year<1911 else year,month,day).strftime("%Y%m%d")


def parse_official_valuation(payload,market,expected_date):
    """Reject wrong dates, schema drift, partial rows and duplicate identifiers.

    Unknown official numeric markers remain NaN; genuine numeric zero remains
    zero. An empty or malformed response is an error, never proof of a holiday.
    """
    if market not in ("上市","上櫃"):
        raise ValueError("Unknown official valuation market")
    if not re.fullmatch(r"\d{8}",str(expected_date)):
        raise ValueError("Expected valuation date must be YYYYMMDD")
    if not isinstance(payload,dict) or payload.get("date")!=str(expected_date):
        raise ValueError("Official valuation response date mismatch or missing")
    if str(payload.get("stat","")).strip().lower()!="ok":
        raise ValueError("Official valuation status is not OK")
    names={"Ticker":"證券代號" if market=="上市" else "股票代號",
        "Name":"證券名稱" if market=="上市" else "公司名稱",
        "PE_Ratio":"本益比","PB_Ratio":"股價淨值比","Dividend_Yield":"殖利率(%)"}
    tables=[payload] if market=="上市" else payload.get("tables",[])
    if not isinstance(tables,list):
        raise ValueError("Official valuation tables schema is invalid")
    eligible=[]
    for table in tables:
        if not isinstance(table,dict):
            raise ValueError("Official valuation table is invalid")
        fields=table.get("fields",[])
        if not isinstance(fields,list):
            raise ValueError("Official valuation fields schema is invalid")
        fields=[str(v).strip() for v in fields]
        if len(fields)!=len(set(fields)):
            raise ValueError("Duplicate official valuation field names")
        if set(names.values())<=set(fields):
            eligible.append((table,fields))
    if len(eligible)!=1:
        raise ValueError("Missing or ambiguous official named valuation schema")
    table,fields=eligible[0]
    if "date" in table and _table_date(table["date"])!=str(expected_date):
        raise ValueError("Official valuation table date mismatch")
    rows=table.get("data")
    if not isinstance(rows,list) or not rows:
        raise ValueError("Empty official valuation table is not verified no-data")
    if any(not isinstance(row,(list,tuple)) or len(row)!=len(fields) for row in rows):
        raise ValueError("Ragged official valuation source row")
    frame=pd.DataFrame(rows,columns=fields)
    result=pd.DataFrame({k:frame[v] for k,v in names.items()})
    result["Ticker"]=result.Ticker.astype(str).str.strip()
    result["Name"]=result.Name.astype(str).str.strip()
    if not result.Ticker.str.fullmatch(r"[0-9A-Za-z]+").all() or result.Name.isin(["","None","nan"]).any():
        raise ValueError("Missing or malformed official ticker/name")
    if result.Ticker.duplicated().any():
        raise ValueError("Duplicate official market ticker after normalization")
    for col in NUMERIC_COLUMNS:
        values=result[col].astype(str).str.strip().str.replace(",","",regex=False)
        result[col]=pd.to_numeric(values.where(~values.isin(["","-","--","N/A","None","nan","null"])),errors="raise")
        if np.isinf(result[col]).any():
            raise ValueError("Nonfinite official valuation number")
    if not result[NUMERIC_COLUMNS].notna().any(axis=None):
        raise ValueError("Official valuation market has no numeric observations")
    result["Market"]=market
    return result[OUTPUT_COLUMNS]


def parse_official_market_holidays(payload,year):
    """Verify the requested annual TWSE calendar; settlement-only is no trade."""
    if not isinstance(payload,dict) or payload.get("date")!=f"{year}0101" or payload.get("queryYear")!=year or str(payload.get("stat","")).lower()!="ok":
        raise ValueError("Official market calendar year/date/status mismatch")
    if payload.get("fields")!=["日期","名稱","說明"] or not payload.get("data"):
        raise ValueError("Official market calendar schema/rows missing")
    closures=set()
    seen=set()
    for row in payload["data"]:
        if not isinstance(row,list) or len(row)!=3:
            raise ValueError("Ragged official market calendar")
        day=date.fromisoformat(str(row[0]))
        if day.year!=year or day in seen:
            raise ValueError("Official market calendar duplicate or wrong-year day")
        seen.add(day)
        label,explanation=str(row[1]),str(row[2])
        combined=label+explanation
        if any(marker in combined for marker in ("市場無交易","不交易","放假","補假")):
            closures.add(day)
        elif "開始交易" not in combined and "最後交易" not in combined:
            raise ValueError("Unclassified official market calendar event")
    return closures
