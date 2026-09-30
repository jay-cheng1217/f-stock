"""Separate unobserved TDCC buckets from officially closed business weeks.

The TWSE HTML form uses `date=YYYY0101`; the older `queryYear` parameter is
ignored by the server. Response year/date must be exact before calendar use.
Research classification only: no feature grid or raw week is changed.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    from scripts.stage_historical_gaps import fetch,STAGE,write_json
    import pandas as pd
    report=ROOT/"ml/reports/research/data_layer_consistency_20260906"
    prior=json.loads((report/"tdcc_historical_gap_inventory.json").read_text(encoding="utf-8"))
    weeks=prior["old43_by_actual_raw_bucket"]
    calendars={}
    for year in sorted({int(w["week"][:4]) for w in weeks if w["twii_sessions"]==0}):
        path=STAGE/f"raw/twse_holiday_{year}.txt"
        url=f"https://www.twse.com.tw/holidaySchedule/holidaySchedule?date={year}0101&response=json"
        if not path.exists():
            fetch(f"twse_holiday_{year}",url)
        payload=json.loads(path.read_text(encoding="utf-8-sig"))
        assert str(payload.get("stat","")).lower()=="ok" and payload.get("date")==f"{year}0101" and payload.get("queryYear")==year
        assert payload.get("fields")==["日期","名稱","說明"] and all(row[0].startswith(str(year)) for row in payload["data"])
        calendars[year]={"url":url,"source_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"rows":payload["data"]}
    results=[]
    for old in weeks:
        row=dict(old)
        row["original_category"]=old["category"]
        if old["twii_sessions"]==0:
            period=pd.Period(old["week"],freq="W-FRI")
            workdays=pd.date_range(period.start_time,period.end_time,freq="B").strftime("%Y-%m-%d").tolist()
            calendar=calendars[period.start_time.year]
            matches=[x for x in calendar["rows"] if x[0] in workdays]
            settlement=[x[0] for x in matches if "結算交割" in x[1]+x[2] and "市場無交易" in x[1]+x[2]]
            closure=[x[0] for x in matches if x[0] not in settlement and ("放假" in x[2] or "春節" in x[1] or "除夕" in x[1])]
            assert set(workdays)==set(settlement+closure),"No-trade classification lacks official weekday coverage"
            row.update(official_calendar_url=calendar["url"],calendar_sha256=calendar["source_sha256"],
                       official_week_rows=matches,settlement_dates=settlement)
            if not settlement:
                row["category"]="LEGAL_NO_BUSINESS_DAY_WEEK"
                row["classification_basis"]="All five weekdays officially closed; TDCC defines observation at last business-day close. Legal absence, not a recoverable file gap. Historical conclusion inferred from the official business calendar; no fabricated TDCC response."
            else:
                row["category"]="OLDER_UNOBSERVED_WITH_SETTLEMENT_DAYS"
                row["expected_last_business_date"]=max(settlement)
                row["classification_basis"]="No trading is not no business: settlement days exist. Keep unobserved; expected date is a calendar inference, not a verified archived TDCC publication date."
        elif old["category"]=="OUTSIDE_PUBLIC_ONE_YEAR_WINDOW":
            row["category"]="OLDER_UNOBSERVED_WITH_TRADING_DAYS"
        results.append(row)
    counts=dict(Counter(x["category"] for x in results))
    result={"checked_at":datetime.now().isoformat(),"original_unobserved_buckets":len(results),"category_counts":counts,
        "weeks":results,"calendar_evidence":calendars,"tdcc_business_day_definition_url":"https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
        "calendar_parameter_warning":"Official HTML form exposes name=date. queryYear=110/112 returned current115calendar and was rejected; use date=YYYY0101 and verify response date/queryYear/title.",
        "data_layer_scope":"Classification report only; unchanged production TDCC raw, summary, feature missing-week semantics, model or ledger.",
        "historical_access":"Existing configured FinMind token tested2021-09-10:HTTP400/API400, level register, rows0; not a stale assumption. No account change or purchase."}
    write_json(report/"tdcc_calendar_reconciliation.json",result)
    lines=["# TDCC 歷史未觀測週的官方行事曆核對","",
           "原43個W-FRI未觀測格，重新分類為：","",
           "| 分類 | 格數 |","|---|---:|"]
    lines += [f"| {k} | {v} |" for k,v in counts.items()]
    lines += ["","39個舊期格不能全部稱為可補缺週。其中2022-02-04、2023-01-27、2025-01-31的整個工作週均為正式春節假期；連同近年的2026-02-20，共4格可合法無觀測。這是依官方工作日與TDCC最後營業日定義的分類，不是捏造歷史TDCC回應。","",
              "2021-02-12那週雖然沒有交易，但2/8與2/9仍辦理結算交割，不能一併當作整週無營業；預期最後營業日2/9尚需歷史TDCC來源直接佐證。其餘舊格有交易日，繼續標記未觀測；現有FinMind register權限實測不允許歷史bulk。","",
              "近一年3週仍由公開單券查詢收集，不能將12筆小批驗證充當全週。分類不更改原raw、summary或feature缺週格點。","",
              "| 零交易週 | 分類 | 官方期別證據 |","|---|---|---|"]
    lines += [f"| {x['week']} | {x['category']} | [TWSE 年度行事曆]({x['official_calendar_url']}) |" for x in results if x["twii_sessions"]==0]
    lines += ["","TWSE年度查詢的實際參數是date=YYYY0101。以queryYear傳民國年會被忽略而返回最新年度；本報告驗證回應date與queryYear等於請求年度，避免拿今年行事曆解釋往年。"]
    (report/"tdcc_calendar_reconciliation.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"category_counts":counts,"reports":str(report)},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
