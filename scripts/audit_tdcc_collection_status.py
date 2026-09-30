"""Read one frozen collector checkpoint; classify absence without editing it."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label",required=True)
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import pandas as pd
    from scripts.backfill_tdcc_public_history import BASE,digest,exact_no_data,atomic_json
    plan=json.loads((BASE/"plan.json").read_text(encoding="utf-8"))
    content=(BASE/"progress.json").read_bytes()
    progress=json.loads(content)
    root=ROOT/"ml/reports/research/data_layer_consistency_20260906"
    report=root/f"tdcc_collection_status_{args.label}.json"
    assert not report.exists(),"Preserve historical status receipts"
    weeks=[]
    absence=[]
    all_events=[]
    for week in plan["weeks"]:
        day=week["date"]
        states=progress.get(day,{})
        sources=[]
        for item in week["universe_sources"]:
            path=Path(item["path"])
            assert digest(path)==item["sha256"]
            frame=pd.read_csv(path,dtype={"證券代號":str})
            frame["證券代號"]=frame["證券代號"].str.strip()
            sources.append((item,frame))
        for ticker,item in states.items():
            all_events.extend(event for attempt in item["attempts"] for event in attempt.get("http_events",[]))
            if item.get("status") not in ("SOURCE_NO_DATA","UNRESOLVED"):
                continue
            record={"date":day,"ticker":ticker,"status":item["status"],"http_status":item.get("http_status"),"raw_path":item.get("raw_path"),"raw_sha256":item.get("raw_sha256"),"attempts":len(item["attempts"])}
            if item.get("raw_path"):
                raw=Path(item["raw_path"])
                assert digest(raw)==item["raw_sha256"]
                record["exact_echoed_official_no_data"]=exact_no_data(raw.read_text(encoding="utf-8"),day,ticker)
            neighbors=[]
            for source,frame in sources:
                subset=frame[frame["證券代號"].eq(ticker)]
                neighbors.append({"date":Path(source["path"]).stem[5:],"present":not subset.empty,"rows":len(subset),"source_sha256":source["sha256"]})
            record["frozen_neighbors"]=neighbors
            if item.get("status")=="SOURCE_NO_DATA":
                assert record.get("exact_echoed_official_no_data")
                record["classification"]="OFFICIAL_NO_DATA_EXACT_QUERY; legal eligibility cause unresolved"
                if day=="20251009" and ticker=="009813":
                    record["classification"]="OFFICIAL_NO_DATA_BEFORE_LISTING; listing-date context confirmed, but pre-listing TDCC registration is possible"
                    record["listing_date"]="2025-10-20"
                    record["source"]="https://www.twse.com.tw/zh/ETFortune/newsDetail/8a8216d6993bf1510199e0a528f7029a"
                elif day=="20251009" and ticker in ("00673O","00706O"):
                    record["classification"]="OFFICIAL_NO_DATA; later-neighbor temporary-code/reverse-split context, exact O-code lifecycle not proven"
                    record["source"]="https://wwwc.twse.com.tw/zh/ETFortune/announcement?company=A00005&date=20250911&fund=00706L&seq=1&type=all"
                record["disposition"]="No retry loop; retain explicit absence evidence; do not fill zero, remove plan code or merge alternative ticker without source proof."
            else:
                record.update(error_type=item.get("error_type"),error=item.get("error"),disposition="Unresolved acquisition/parser issue remains an open coverage gap.")
            absence.append(record)
        counts=Counter(item["status"] for item in states.values())
        weeks.append({"date":day,"plan_tickers":week["target_count"],"attempted_tickers":len(states),"remaining_unattempted":week["target_count"]-len(states),"statuses":dict(counts),
            "all_plan_queries_terminal":len(states)==week["target_count"] and not counts["UNRESOLVED"],"whole_week_published":False})
    result={"asof":datetime.now().isoformat(),"status":"READ_ONLY_CHECKPOINT_SNAPSHOT","plan_sha256":digest(BASE/"plan.json"),
        "weeks":weeks,"absence":absence,"http_events_observed":len(all_events),
        "http_status_counts":dict(Counter(str(e.get("http_status")) for e in all_events)),"rate429_or503":sum(e.get("http_status") in (429,503) for e in all_events),"transport_errors":sum("network_error_type" in e for e in all_events),
        "bulk_route_review":"Inspected official TDCC public form, documented OpenAPI1-5 and data.gov11452 export; no documented date bulk found. Existing authorized FinMind historical bulk probe returned HTTP/API400 register-level denial. Unpublished third-party or unknown endpoints are not claimed exhaustively excluded.",
        "gates":"Terminal no-data is not a business eligibility determination. Whole-week staging requires complete attempted universe and a source absence manifest; production promotion additionally requires canonical summary and all latest TDCC features A/B."}
    atomic_json(report,result)
    print(json.dumps({k:v for k,v in result.items() if k!="absence"},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
