"""Preserve public TDCC rows in the canonical raw schema, without invented rows.

The public HTML's serial number is not the raw holding level: a total printed
as serial16 is still canonical level17. A missing adjustment remains absent.
All output is guarded research staging; this does not publish production data.
"""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def normalize_response(html,day,ticker,*,parser_backend="html.parser"):
    from bs4 import BeautifulSoup
    from scripts.backfill_tdcc_public_history import parse_response,compact,number,COLUMNS
    soup=BeautifulSoup(html,parser_backend)
    normal,meta=parse_response(html,day,ticker,_soup=soup)
    table=next(t for t in soup.select("table") if "持股/單位數分級" in t.get_text())
    extra=[]
    provenance=[]
    for row in table.select("tr"):
        cells=[c.get_text(" ",strip=True) for c in row.find_all("td",recursive=False)]
        if not cells:
            continue
        label=compact(cells[1])
        canonical=17 if label=="合計" else 16 if label.startswith("差異數調整") else None
        if canonical is None:
            continue
        people=number(cells[2],True) if cells[2].strip() else None
        extra.append(dict(zip(COLUMNS,[day,ticker,canonical,people,number(cells[3],True),number(cells[4])])))
        provenance.append({"canonical_level":canonical,"source_serial":number(cells[0],True),"source_label":cells[1],"source_people_blank":people is None})
    assert sum(r["持股分級"]==17 for r in extra)==1
    rows=sorted(normal+extra,key=lambda r:r["持股分級"])
    meta.update(canonical_observed_levels=[r["持股分級"] for r in rows],canonical_rows=len(rows),
                source_extra_rows=provenance,adjustment_row_present=any(r["持股分級"]==16 for r in rows),
                total_holders=sum(r["人數"] for r in normal),
                absent_adjustment_policy="Absent source adjustment is not fabricated as a zero row. Source total is always level17; only actual15holding levels enter canonical summary.")
    return rows,meta


def canonical_summary(raw_path,output_dir,source_file):
    """Run the actual production function with only its filesystem root isolated."""
    import pandas as pd
    import numpy as np
    import shutil
    node=next(n for n in ast.parse(source_file.read_text(encoding="utf-8-sig")).body if isinstance(n,ast.FunctionDef) and n.name=="_summarize_tdcc")
    output_dir.mkdir(parents=True,exist_ok=True)
    shutil.copy2(raw_path,output_dir/raw_path.name)
    scope={"os":os,"pd":pd,"np":np,"tqdm":lambda items,**kwargs:items,"TDCC_DIR":str(output_dir)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source_file),"exec"),scope)
    scope["_summarize_tdcc"]()
    return pd.read_csv(output_dir/"tdcc_summary.csv",dtype={"Date":str,"Ticker":str})


def reference(stage,source_root,*,cached=False):
    """Run only while the full collector is paused: one public session,4queries."""
    import pandas as pd
    import requests
    import truststore
    from scripts.backfill_tdcc_public_history import URL,COLUMNS,form_state,RequestPacer,paced_request,digest,atomic_json
    truststore.inject_into_ssl()
    day="20260904"
    tickers=["2330","2357","2603","6488"]
    events=[]
    if not cached:
        session=requests.Session()
        session.headers.update({"User-Agent":"Mozilla/5.0","Referer":URL})
        pacer=RequestPacer(.25)
        initial=paced_request(session,"GET",pacer,events,timeout=30)
        initial.raise_for_status()
        fields,dates=form_state(initial.text)
        assert day in dates
    output=[]
    metas=[]
    for ticker in tickers:
        path=stage/f"{day}_{ticker}.html"
        if not cached:
            assert not path.exists(),"Preserve earlier reference evidence"
            response=paced_request(session,"POST",pacer,events,evidence_path=path,
                data=dict(fields,scaDate=day,sqlMethod="StockNo",stockNo=ticker,stockName=""),timeout=30)
            response.raise_for_status()
            path.write_text(response.text,encoding="utf-8")
            fields,dates=form_state(response.text)
        rows,meta=normalize_response(path.read_text(encoding="utf-8"),day,ticker)
        meta.update(raw_path=str(path),raw_sha256=digest(path))
        output.extend(rows)
        metas.append(meta)
    normalized=pd.DataFrame(output,columns=COLUMNS)
    bulk_path=source_root/f"集保分散/tdcc_{day}.csv"
    bulk=pd.read_csv(bulk_path,dtype={"資料日期":str,"證券代號":str})
    bulk["證券代號"]=bulk["證券代號"].str.strip()
    bulk=bulk[bulk["證券代號"].isin(tickers)].copy()
    key=["資料日期","證券代號","持股分級"]
    a=normalized.set_index(key).sort_index()
    b=bulk.set_index(key).sort_index()
    common=a.index.intersection(b.index)
    assert len(common)>=15*len(tickers)
    adjustment_format_differences=[]
    for key in common:
        if key[2]==16:
            adjustment_format_differences.append({"key":key,"public":{c:None if pd.isna(a.loc[key,c]) else float(a.loc[key,c]) for c in a.columns},"bulk":{c:None if pd.isna(b.loc[key,c]) else float(b.loc[key,c]) for c in b.columns},
                "policy":"Preserve public source signed adjustment and blank people. The two official source formats are not claimed identical. Canonical summary excludes level16."})
    normal_total=common[common.get_level_values("持股分級")!=16]
    comparable_a=a.loc[normal_total]
    comparable_b=b.loc[normal_total]
    pd.testing.assert_frame_equal(comparable_a.reset_index(),comparable_b.reset_index(),check_exact=True,check_dtype=False)
    absent=b.index.difference(a.index).tolist()
    assert all(k[2]==16 and b.loc[k,"股數"]==0 and b.loc[k,"占集保庫存數比例%"]==0 for k in absent),"Unexpected source omission"
    assert not len(a.index.difference(b.index))
    normalized_path=stage/f"tdcc_{day}.csv"
    normalized.to_csv(normalized_path,index=False,encoding="utf-8-sig")
    official_dir=stage/"official_raw"
    official_dir.mkdir(exist_ok=True)
    official_path=official_dir/normalized_path.name
    bulk.to_csv(official_path,index=False,encoding="utf-8-sig")
    summaries=[canonical_summary(path,stage/name,source_root/"twstock.py") for path,name in [(normalized_path,"normalized_summary"),(official_path,"official_summary")]]
    pd.testing.assert_frame_equal(summaries[0],summaries[1],check_exact=True,check_dtype=False)
    result={"status":"PASS_SOURCE_CONTRACT_REFERENCE_ONLY","day":day,"tickers":tickers,"bulk_source":str(bulk_path),"bulk_source_sha256":digest(bulk_path),
        "public_rows":len(normalized),"bulk_rows":len(bulk),"common_normal_and_total_cells_exact":True,"source_adjustment_format_differences":adjustment_format_differences,"bulk_zero_adjustment_rows_absent_from_public":absent,
        "canonical_summary_exact":True,"summary":summaries[0].to_dict("records"),"source_metadata":metas,"http_events":events,
        "limits":"Four real same-week ticker source comparisons validate schema semantics, not historical whole-week coverage. Public absent adjustment rows remain absent; bulk's explicit zero rows are reported rather than invented."}
    atomic_json(stage/"reference.json",result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode",choices=["reference","stage"])
    ap.add_argument("--source-root",type=Path,default=Path("F:/stock"))
    ap.add_argument("--cached-reference",action="store_true",help="Read the four preserved HTML responses without network")
    ap.add_argument("--stage-label",default="tdcc_normalized")
    ap.add_argument("--resume",action="store_true",help="Verify immutable checkpoint and completed week hashes before skipping completed weeks")
    ap.add_argument("--parser-backend",choices=["html.parser","lxml"],default="html.parser")
    ap.add_argument("--collector-source",type=Path,help="New physical collector module, preserving already frozen workspace files")
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    if args.collector_source:
        import importlib.util
        collector_path=args.collector_source.resolve()
        assert collector_path.is_relative_to(ROOT)
        spec=importlib.util.spec_from_file_location("scripts.backfill_tdcc_public_history",collector_path)
        collector=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=collector
        spec.loader.exec_module(collector)
    from scripts.backfill_tdcc_public_history import BASE,COLUMNS,digest,atomic_json
    import re
    assert re.fullmatch(r"[a-z0-9_]+",args.stage_label)
    stage=ROOT/"output/historical_gaps_20260906"/args.stage_label
    stage.mkdir(parents=True,exist_ok=True)
    if args.mode=="reference":
        return reference(stage,args.source_root,cached=args.cached_reference)
    import pandas as pd
    plan=json.loads((BASE/"plan.json").read_text(encoding="utf-8"))
    progress=json.loads((BASE/"progress.json").read_text(encoding="utf-8"))
    if args.resume:
        assert json.loads((stage/"progress_snapshot.json").read_text(encoding="utf-8"))==progress,"Checkpoint changed"
        assert json.loads((stage/"plan_snapshot.json").read_text(encoding="utf-8"))==plan,"Plan changed"
    else:
        assert not (stage/"progress_snapshot.json").exists(),"Preserve prior normalization evidence; select a new stage label"
        atomic_json(stage/"progress_snapshot.json",progress)
        atomic_json(stage/"plan_snapshot.json",plan)
    results=[]
    for week in plan["weeks"]:
        manifest_path=stage/f"{week['date']}_manifest.json"
        if args.resume and manifest_path.exists():
            result=json.loads(manifest_path.read_text(encoding="utf-8"))
            expected={t for t,x in progress[week["date"]].items() if x["status"]=="VALIDATED"}
            assert expected=={x["ticker"] for x in result["source_manifest"]}
            assert digest(Path(result["path"]))==result["sha256"]
            for item in result["source_manifest"]:
                assert digest(Path(item["raw_path"]))==item["raw_sha256"]
            results.append({k:v for k,v in result.items() if k!="source_manifest"})
            print(f"Verified completed week {week['date']}; preserved output bytes",flush=True)
            continue
        rows=[]
        metadata=[]
        for ticker,item in progress.get(week["date"],{}).items():
            if item.get("status")!="VALIDATED":
                continue
            path=Path(item["raw_path"])
            assert digest(path)==item["raw_sha256"]
            records,meta=normalize_response(path.read_text(encoding="utf-8"),week["date"],ticker,parser_backend=args.parser_backend)
            meta.update(raw_path=str(path),raw_sha256=item["raw_sha256"])
            rows.extend(records)
            metadata.append(meta)
            if len(metadata)%250==0:
                print(f"Validated {week['date']}: {len(metadata)} tickers",flush=True)
        target=stage/f"tdcc_{week['date']}.csv"
        pd.DataFrame(rows,columns=COLUMNS).to_csv(target,index=False,encoding="utf-8-sig")
        result={"date":week["date"],"universe":week["target_count"],"observed_tickers":len(metadata),"observed_rows":len(rows),"path":str(target),"sha256":digest(target),
                "source_manifest":metadata,"parser_backend":args.parser_backend,"publication":"NOT_PUBLISHED; complete coverage and latest TDCC feature A/B are separate required gates."}
        atomic_json(stage/f"{week['date']}_manifest.json",result)
        results.append({k:v for k,v in result.items() if k!="source_manifest"})
    atomic_json(stage/"status.json",results)
    print(json.dumps(results,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
