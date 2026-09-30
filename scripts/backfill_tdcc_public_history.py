"""Resumable, single-session TDCC public-history collector for research staging.

Freeze each week's full adjacent-raw union before any query. A limited run does
not shrink that universe. Every response must match its requested date/ticker
and all 15 named holding levels. No production raw, summary or model is written.
"""
from __future__ import annotations
import argparse
from collections import deque
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from email.utils import parsedate_to_datetime

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"output/historical_gaps_20260906/tdcc_public_collect"
URL="https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
DATES=("20251009","20251023","20260226")
COLUMNS=["資料日期","證券代號","持股分級","人數","股數","占集保庫存數比例%"]
LEVELS=["1-999","1000-5000","5001-10000","10001-15000","15001-20000",
        "20001-30000","30001-40000","40001-50000","50001-100000",
        "100001-200000","200001-400000","400001-600000","600001-800000",
        "800001-1000000","1000001以上"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def compact(text):
    return re.sub(r"[\s,]","",text)


def number(value,integer=False):
    result=float(compact(value))
    if not math.isfinite(result) or (integer and result!=int(result)):
        raise ValueError("Nonfinite/noninteger TDCC number")
    return int(result) if integer else result


def parse_response(html,day,ticker,*,_soup=None):
    """Validate source identity, level labels, totals and percentage semantics."""
    from bs4 import BeautifulSoup
    soup=_soup if _soup is not None else BeautifulSoup(html,"html.parser")
    text=soup.get_text(" ",strip=True)
    symbol=re.search(r"證券代號\s*[：:]\s*([0-9A-Za-z]+)",text)
    date_match=re.search(r"資料日期\s*[：:]\s*(\d{3,4})年(\d{1,2})月(\d{1,2})日",text)
    if not symbol or symbol.group(1)!=ticker:
        raise ValueError("Missing or mismatched response ticker (empty result is unresolved)")
    if not date_match:
        raise ValueError("Missing response date")
    y,m,d=map(int,date_match.groups())
    actual=f"{y+1911 if y<1911 else y:04d}{m:02d}{d:02d}"
    if actual!=day:
        raise ValueError(f"Response date mismatch: {actual} != {day}")
    tables=[t for t in soup.select("table") if "持股/單位數分級" in t.get_text()]
    if len(tables)!=1:
        raise ValueError("Missing/ambiguous holding distribution table")
    expected_header=["序","持股/單位數分級","人數","股數/單位數","占集保庫存數比例(%)"]
    header=[compact(x.get_text()) for x in tables[0].select("th")]
    if header!=expected_header:
        raise ValueError("Unexpected named TDCC table schema")
    records=[]
    total=None
    adjustment=0
    adjustment_seen=False
    for row in tables[0].select("tr"):
        cells=[x.get_text(" ",strip=True) for x in row.find_all("td",recursive=False)]
        if not cells:
            continue
        if len(cells)!=5:
            raise ValueError("Ragged TDCC row")
        level=number(cells[0],True)
        label=compact(cells[1])
        shares=number(cells[3],True)
        pct=number(cells[4])
        if label=="合計":
            if total is not None:
                raise ValueError("Duplicate total row")
            total={"shares":shares,"people":number(cells[2],True),"pct":pct}
        elif label.startswith("差異數調整"):
            if adjustment_seen or cells[2].strip():
                raise ValueError("Duplicate/invalid adjustment row")
            adjustment_seen=True
            adjustment=shares
        else:
            if level not in range(1,16) or label!=LEVELS[level-1]:
                raise ValueError("Holding level number/label mismatch")
            people=number(cells[2],True)
            if min(people,shares,pct)<0 or pct>100:
                raise ValueError("Negative/out-of-range normal TDCC value")
            records.append(dict(zip(COLUMNS,[day,ticker,level,people,shares,pct])))
    if sorted(r["持股分級"] for r in records)!=list(range(1,16)):
        raise ValueError("Missing or duplicate 15 holding levels")
    if total is None or min(total["shares"],total["people"])<0 or abs(total["pct"]-100)>.001:
        raise ValueError("Missing/invalid source total")
    if sum(r["股數"] for r in records)+adjustment!=total["shares"] or sum(r["人數"] for r in records)!=total["people"]:
        raise ValueError("TDCC source share/people totals do not reconcile")
    if total["shares"]==0:
        if any(r["股數"] or r["占集保庫存數比例%"] for r in records):
            raise ValueError("Nonzero values with zero source total")
    elif any(abs(r["股數"]/total["shares"]*100-r["占集保庫存數比例%"])>.011 for r in records):
        raise ValueError("TDCC level percentage/share mismatch")
    return sorted(records,key=lambda r:r["持股分級"]),{"actual_date":actual,"ticker":ticker,"levels":15,
        "total_shares":total["shares"],"adjustment_shares":adjustment,"whale_pct":next(r["占集保庫存數比例%"] for r in records if r["持股分級"]==15)}


def form_state(html):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html,"html.parser")
    form=soup.find("form",id="form1")
    if form is None:
        raise ValueError("Missing public query form")
    fields={x["name"]:x.get("value","") for x in form.select('input[type="hidden"][name]')}
    if not fields.get("SYNCHRONIZER_TOKEN"):
        raise ValueError("Missing public session token")
    dates=[x.get("value") for x in form.select('select[name="scaDate"] option')]
    return fields,dates


def exact_no_data(html,day,ticker):
    """An empty result is evidence only when its echoed query is exact."""
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html,"html.parser")
    stock=soup.select_one('form#form1 input[name="stockNo"]')
    selected=soup.select_one('form#form1 select[name="scaDate"] option[selected]')
    messages=[compact(t.get_text()) for t in soup.select("table td[colspan]")]
    return bool(stock and selected and stock.get("value")==ticker and selected.get("value")==day and "查無此資料" in messages)


def retry_after_seconds(value,now=None):
    if not value:
        return 0.0
    try:
        return max(0.0,float(value))
    except ValueError:
        try:
            return max(0.0,parsedate_to_datetime(value).timestamp()-(time.time() if now is None else now))
        except (TypeError,ValueError,OverflowError):
            return 0.0


class RequestPacer:
    """Single session: requested completion delay and at most3starts/second."""
    def __init__(self,delay,clock=time.monotonic,sleeper=time.sleep):
        self.delay=delay
        self.clock=clock
        self.sleep=sleeper
        self.starts=deque()
        self.last_complete=None

    def before(self):
        while True:
            now=self.clock()
            while self.starts and now-self.starts[0]>=1.0:
                self.starts.popleft()
            wait=max(0.0,(self.last_complete+self.delay-now) if self.last_complete is not None else 0.0,
                     self.starts[0]+1.001-now if len(self.starts)>=3 else 0.0)
            if wait<=0:
                self.starts.append(now)
                return now
            self.sleep(wait)

    def complete(self):
        self.last_complete=self.clock()


def paced_request(session,method,pacer,events,*,evidence_path=None,**kwargs):
    """Bounded transport retry; refresh CSRF after uncertain POST outcomes."""
    import requests
    for attempt in range(1,4):
        event={"method":method,"start_monotonic":pacer.before(),"attempt":attempt}
        response=None
        try:
            response=session.request(method,URL,**kwargs)
            event.update(http_status=response.status_code,retry_after=response.headers.get("Retry-After"))
            if evidence_path is not None and response.status_code>=400:
                evidence=evidence_path.with_name(evidence_path.stem+f"_http{len(events)+1}.html")
                evidence.write_text(response.text,encoding="utf-8")
                event.update(raw_path=str(evidence),raw_sha256=digest(evidence))
        except requests.RequestException as exc:
            event["network_error_type"]=type(exc).__name__
            if attempt==3:
                raise
        finally:
            pacer.complete()
            event["seconds"]=round(pacer.clock()-event["start_monotonic"],3)
            events.append(event)
        if response is not None and response.status_code not in (429,503):
            return response
        if response is not None:
            pacer.delay=max(pacer.delay,1.0)
            event["fallback_delay_seconds"]=pacer.delay
        if attempt==3:
            return response
        backoff=max(2.0**attempt,retry_after_seconds(event.get("retry_after")))
        event["backoff_seconds"]=backoff
        pacer.sleep(backoff)
        if method=="POST":
            # A timeout can occur after server-side token consumption. A fresh
            # ordinary public GET avoids replaying an invalid CSRF token.
            fresh=paced_request(session,"GET",pacer,events,evidence_path=evidence_path,timeout=30)
            fresh.raise_for_status()
            fields,_=form_state(fresh.text)
            fields.update({k:v for k,v in kwargs["data"].items() if k in ("scaDate","sqlMethod","stockNo","stockName")})
            kwargs["data"]=fields
    raise RuntimeError("Unreachable retry state")


def create_plan():
    import pandas as pd
    plan_path=BASE/"plan.json"
    if plan_path.exists():
        plan=json.loads(plan_path.read_text(encoding="utf-8"))
        assert all(digest(Path(p["path"]))==p["sha256"] for w in plan["weeks"] for p in w["universe_sources"]),"Frozen universe source changed"
        return plan
    available=sorted((ROOT/"集保分散").glob("tdcc_2*.csv"))
    calendar=ROOT/"output/historical_gaps_20260906/raw/tdcc_history_page.txt"
    _,dates=form_state(calendar.read_text(encoding="utf-8"))
    weeks=[]
    for day in DATES:
        assert day in dates,"Requested date absent from official published calendar"
        previous=max(p for p in available if p.stem[5:]<day)
        following=min(p for p in available if p.stem[5:]>day)
        universe=set()
        sources=[]
        for path in (previous,following):
            frame=pd.read_csv(path,dtype={"證券代號":str,"資料日期":str})
            assert set(frame["資料日期"])=={path.stem[5:]}
            codes=set(frame["證券代號"].str.strip())
            assert all(re.fullmatch(r"[0-9A-Z]+",c) for c in codes)
            universe.update(codes)
            sources.append({"path":str(path),"sha256":digest(path),"tickers":len(codes),"rows":len(frame)})
        weeks.append({"date":day,"week":str(pd.Timestamp(day).to_period("W-FRI")),"tickers":sorted(universe),"target_count":len(universe),"universe_sources":sources})
    plan={"version":1,"created_at":datetime.now().isoformat(),"source":URL,"weeks":weeks,
          "calendar_sha256":digest(calendar),"scope":"Frozen union of nearest preceding and following official raw security sets; includes non-model securities. It is an explicit collection universe, not an independently authoritative exact-date listing census.",
          "source_evidence":"Official form supports one security/date; data.gov11452 and OpenAPI1-5 document current bulk without a date parameter.",
          "promotion":"Never automatic. Partial results and unresolved no-data are not full-market raw. Whole-week publication still requires coverage review, source mapping and latest TDCC feature A/B."}
    atomic_json(plan_path,plan)
    return plan


def verified_receipt(item,day,ticker):
    raw=Path(item["raw_path"])
    table=Path(item["table_path"])
    if digest(raw)!=item["raw_sha256"] or digest(table)!=item["table_sha256"]:
        raise ValueError("Checkpoint source/table hash mismatch")
    parse_response(raw.read_text(encoding="utf-8"),day,ticker)


def summarize(plan,progress):
    results=[]
    for week in plan["weeks"]:
        states=progress.get(week["date"],{})
        ok=sum(x.get("status")=="VALIDATED" for x in states.values())
        failed=sum(x.get("status")=="UNRESOLVED" for x in states.values())
        no_data=sum(x.get("status")=="SOURCE_NO_DATA" for x in states.values())
        results.append({"date":week["date"],"target_count":week["target_count"],"validated":ok,
                        "unresolved_attempted":failed,"official_no_data":no_data,"remaining":week["target_count"]-ok,
                        "complete_for_frozen_universe":ok==week["target_count"],"production_published":False})
    return results


def prioritize_jobs(jobs,priority_tickers):
    """Change execution order only; every frozen date/ticker pair stays present."""
    return sorted(jobs,key=lambda pair:0 if pair[1] in priority_tickers else 1 if re.fullmatch(r"\d{4}",pair[1]) else 2)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode",choices=["plan","collect","status"])
    ap.add_argument("--limit",type=int,default=12,help="Maximum new queries in this run; 0 means all remaining")
    ap.add_argument("--tickers",default="",help="Optional work subset, never shrinks the frozen week universe")
    ap.add_argument("--delay",type=float,default=1.0,help="Minimum seconds after requests, at least0.25; always at most3starts/sec")
    ap.add_argument("--retry-unresolved",action="store_true")
    ap.add_argument("--priority-manifest",type=Path,help="Frozen source-universe manifest: model/source tickers first across weeks, then4digit ordinary codes, then original tail; plan unchanged")
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    assert args.delay>=.25 and args.limit>=0
    plan=create_plan()
    progress_path=BASE/"progress.json"
    progress=json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    reclassified=False
    for week in plan["weeks"]:
        for ticker,item in progress.get(week["date"],{}).items():
            assert ticker in week["tickers"]
            if item.get("status")=="VALIDATED":
                verified_receipt(item,week["date"],ticker)
            elif item.get("raw_path") and item.get("http_status")==200:
                raw=Path(item["raw_path"])
                assert digest(raw)==item["raw_sha256"]
                if exact_no_data(raw.read_text(encoding="utf-8"),week["date"],ticker):
                    item["status"]="SOURCE_NO_DATA"
                    item["classification"]="Exact echoed query with official 查無此資料; no-data is not retried automatically and is not fabricated as15zero levels. Date-specific eligibility still requires review."
                    reclassified=True
    if reclassified:
        atomic_json(progress_path,progress)
    requests_made=0
    costs=[]
    all_http_events=[]
    effective_delay=args.delay
    if args.mode=="collect":
        import pandas as pd
        import requests
        import truststore
        truststore.inject_into_ssl()
        selected=set(args.tickers.split(",")) if args.tickers else None
        if selected:
            assert all(selected<=set(w["tickers"]) for w in plan["weeks"]),"Subset must exist in every frozen week universe"
        jobs=[(w["date"],t) for w in plan["weeks"] for t in w["tickers"]
              if (selected is None or t in selected) and (t not in progress.get(w["date"],{})
              or (args.retry_unresolved and progress[w["date"]][t].get("status")=="UNRESOLVED"))]
        if args.priority_manifest:
            priority=json.loads(args.priority_manifest.read_text(encoding="utf-8"))
            priority_tickers=set(priority["raw_metadata"])
            assert priority_tickers
            jobs=prioritize_jobs(jobs,priority_tickers)
            print(json.dumps({"priority_manifest":str(args.priority_manifest),"priority_manifest_sha256":digest(args.priority_manifest),"priority_pool_tickers":len(priority_tickers),"remaining_priority_jobs":sum(t in priority_tickers for _,t in jobs),"remaining_all_jobs":len(jobs),"fixed_plan_sha256":digest(BASE/"plan.json")},ensure_ascii=False),flush=True)
        if args.limit:
            jobs=jobs[:args.limit]
        session=requests.Session()
        session.headers.update({"User-Agent":"Mozilla/5.0","Referer":URL})
        failures=0
        pacer=RequestPacer(args.delay)
        if jobs:
            initial=paced_request(session,"GET",pacer,all_http_events,timeout=30)
            initial.raise_for_status()
            fields,public_dates=form_state(initial.text)
        for day,ticker in jobs:
            assert day in public_dates
            old=progress.setdefault(day,{}).get(ticker,{})
            attempt=len(old.get("attempts",[]))+1
            path=BASE/day/"raw"/f"{ticker}_attempt{attempt}.html"
            path.parent.mkdir(parents=True,exist_ok=True)
            item={"status":"UNRESOLVED","queried_at":datetime.now().isoformat(),"date":day,"ticker":ticker,"attempts":old.get("attempts",[]),"http_events":[]}
            started=time.monotonic()
            fatal=False
            try:
                params=dict(fields,scaDate=day,sqlMethod="StockNo",stockNo=ticker,stockName="")
                response=paced_request(session,"POST",pacer,item["http_events"],evidence_path=path,data=params,timeout=30)
                requests_made+=1
                path.write_text(response.text,encoding="utf-8")
                item.update(http_status=response.status_code,raw_path=str(path),raw_sha256=digest(path))
                fatal=response.status_code in (401,403,429)
                response.raise_for_status()
                fields,public_dates=form_state(response.text)
                if exact_no_data(response.text,day,ticker):
                    item.update(status="SOURCE_NO_DATA",classification="Official no-data with exact echoed query; eligibility review required, no fabricated zero rows")
                else:
                    rows,validation=parse_response(response.text,day,ticker)
                    table=BASE/day/"tables"/f"{ticker}.csv"
                    table.parent.mkdir(parents=True,exist_ok=True)
                    pd.DataFrame(rows,columns=COLUMNS).to_csv(table,index=False,encoding="utf-8-sig")
                    item.update(status="VALIDATED",validation=validation,table_path=str(table),table_sha256=digest(table))
                failures=0
            except Exception as exc:
                item.update(error_type=type(exc).__name__,error=str(exc)[:250])
                failures+=1
            item["seconds"]=round(time.monotonic()-started,3)
            all_http_events.extend(item["http_events"])
            costs.append(item["seconds"])
            item["attempts"].append({k:v for k,v in item.items() if k!="attempts"})
            progress[day][ticker]=item
            atomic_json(progress_path,progress)
            print(json.dumps({"date":day,"ticker":ticker,"status":item["status"],"seconds":item["seconds"]},ensure_ascii=False),flush=True)
            if fatal or failures>=3:
                break
        effective_delay=pacer.delay
    result={"status":"RESEARCH_STAGING_ONLY","weeks":summarize(plan,progress),
            "new_queries":requests_made,"mean_query_seconds":sum(costs)/len(costs) if costs else None,
            "delay_seconds":args.delay,"effective_delay_seconds":effective_delay,"max_request_starts_per_second":3,
            "http_events":all_http_events,"http_request_count":len(all_http_events),
            "rate_limit_or_unavailable_responses":sum(x.get("http_status") in (429,503) for x in all_http_events),
            "transport_errors":sum("network_error_type" in x for x in all_http_events),"plan_sha256":digest(BASE/"plan.json"),
            "remaining_queries":sum(w["target_count"]-sum(i.get("status")=="VALIDATED" for i in progress.get(w["date"],{}).values()) for w in plan["weeks"]),
            "scope":plan["scope"],"promotion":plan["promotion"]}
    if costs:
        result["estimated_remaining_hours_at_observed_rate"]=result["remaining_queries"]*result["mean_query_seconds"]/3600
        result["estimate_limits"]="Logical-query wall time already includes pacing; estimate is not a promise and source latency differs strongly between ordinary stocks and nonordinary tail."
    atomic_json(BASE/"status.json",result)
    print(json.dumps({k:v for k,v in result.items() if k!="http_events"},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
