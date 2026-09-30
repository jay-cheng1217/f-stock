"""Bounded rechecks of failed queries or independently reviewed TDCC no-data.

Only these checkpoint entries can change. Preserve all original attempts and
reject concurrent checkpoint writes. Full normalization revalidates all other
records later; this does not pretend a targeted retry validates the whole file.
"""
from datetime import datetime
import argparse
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
TARGETS=[("20251023","YFK2"),("20251023","YGM3"),("20260226","2616"),("20260226","YGB3"),("20251009","2347"),("20251009","YLB2")]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind",choices=["anomalies","no-data-confirm"],default="anomalies")
    args=parser.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import pandas as pd
    import requests
    import truststore
    from scripts.backfill_tdcc_public_history import BASE,URL,COLUMNS,create_plan,digest,atomic_json,RequestPacer,paced_request,form_state,parse_response,exact_no_data
    truststore.inject_into_ssl()
    plan=create_plan()
    path=BASE/"progress.json"
    expected_sha=digest(path)
    before_sha=expected_sha
    progress=json.loads(path.read_text(encoding="utf-8"))
    receipt=BASE/("targeted_recheck_2134.json" if args.kind=="anomalies" else "independent_no_data_confirmation_2134.json")
    assert not receipt.exists(),"This one-off reasoned recheck already ran; no no-data retry loop"
    targets=TARGETS if args.kind=="anomalies" else [(day,ticker) for day,items in progress.items() for ticker,item in items.items()
        if item["status"]=="SOURCE_NO_DATA" and "confirmed independently" not in item.get("classification","")]
    assert targets
    for day,ticker in targets:
        assert ticker in next(w["tickers"] for w in plan["weeks"] if w["date"]==day)
        assert progress[day][ticker]["status"] in ("UNRESOLVED","SOURCE_NO_DATA")
    session=requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0","Referer":URL})
    pacer=RequestPacer(.25)
    events=[]
    response=paced_request(session,"GET",pacer,events,timeout=30)
    response.raise_for_status()
    fields,dates=form_state(response.text)
    outcomes=[]
    for day,ticker in targets:
        old=progress[day][ticker]
        prior=Path(old["raw_path"])
        assert digest(prior)==old["raw_sha256"]
        attempt=len(old["attempts"])+1
        raw=BASE/day/"raw"/f"{ticker}_attempt{attempt}.html"
        assert not raw.exists()
        item={"date":day,"ticker":ticker,"status":"UNRESOLVED","queried_at":datetime.now().isoformat(),"attempts":list(old["attempts"]),"http_events":[],
              "review_reason":"Prior request failure or contradictory official date" if old["status"]=="UNRESOLVED" else
                  "One independent confirmation:2347proved this endpoint can temporarily return false-empty; no repeated no-data loop" if args.kind=="no-data-confirm" else
                  "Exact no-data despite presence in both frozen adjacent raw weeks; one independent confirmation authorized"}
        try:
            response=paced_request(session,"POST",pacer,item["http_events"],evidence_path=raw,
                data=dict(fields,scaDate=day,sqlMethod="StockNo",stockNo=ticker,stockName=""),timeout=30)
            raw.write_text(response.text,encoding="utf-8")
            item.update(http_status=response.status_code,raw_path=str(raw),raw_sha256=digest(raw))
            response.raise_for_status()
            fields,dates=form_state(response.text)
            assert day in dates
            if exact_no_data(response.text,day,ticker):
                item.update(status="SOURCE_NO_DATA",classification="Official no-data confirmed independently; not fabricated as zero and not repeatedly retried")
            else:
                rows,validation=parse_response(response.text,day,ticker)
                table=BASE/day/"tables"/f"{ticker}_review{attempt}.csv"
                assert not table.exists()
                pd.DataFrame(rows,columns=COLUMNS).to_csv(table,index=False,encoding="utf-8-sig")
                item.update(status="VALIDATED",validation=validation,table_path=str(table),table_sha256=digest(table))
        except Exception as exc:
            item.update(error_type=type(exc).__name__,error=str(exc)[:250])
            # An invalid date is retained as source evidence, never relabeled
            # with the requested date to make the table pass.
            fresh=paced_request(session,"GET",pacer,events,timeout=30)
            fresh.raise_for_status()
            fields,dates=form_state(fresh.text)
        item["attempts"].append({k:v for k,v in item.items() if k!="attempts"})
        assert digest(path)==expected_sha,"Another collector changed the checkpoint"
        progress[day][ticker]=item
        atomic_json(path,progress)
        expected_sha=digest(path)
        outcome={k:v for k,v in item.items() if k not in ("attempts","http_events")}
        outcome.update(before_status=old["status"],prior_raw_path=old["raw_path"],prior_raw_sha256=old["raw_sha256"])
        outcomes.append(outcome)
        print(json.dumps(outcome,ensure_ascii=False),flush=True)
    result={"status":"BOUNDED_TARGETED_RECHECK_COMPLETE","time":datetime.now().isoformat(),"checkpoint_before_sha256":before_sha,"checkpoint_after_sha256":expected_sha,
            "kind":args.kind,"queries":len(targets),"plan_sha256":digest(BASE/"plan.json"),"outcomes":outcomes,"session_events":events,
            "limits":"Bounded target list only. Official date anomalies remaining after recheck stay unresolved; independently confirmed no-data is not repeated again. Complete raw normalization and2031latest feature A/B remain required."}
    atomic_json(receipt,result)


if __name__=="__main__":main()
