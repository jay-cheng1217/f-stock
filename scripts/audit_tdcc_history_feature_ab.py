"""Freeze latest actual daily rows, then audit added TDCC source weeks only.

The full existing summary prefix is retained, including missing-week effects.
The canonical raw summarizer processes every recovered ticker; no model changes
or production promotion happen here. Complete plan traversal is required.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode",choices=["freeze","compare"])
    ap.add_argument("--source-root",type=Path,default=Path("F:/stock"))
    ap.add_argument("--source-manifest",type=Path,help="Frozen source-universe manifest with2031raw metadata and one DuckDB")
    ap.add_argument("--v3-dir",type=Path,help="Verified84-stock classic-indicator source overlay; Date/OHLCV must remain identical")
    ap.add_argument("--stage-label",default="tdcc_feature_ab")
    ap.add_argument("--priority-only",action="store_true",help="Require all frozen2031source-pool plan queries, retaining explicit incomplete full-market status")
    ap.add_argument("--normalized-stage",default="tdcc_normalized")
    ap.add_argument("--feature-source",type=Path,help="Byte-identical current production feature module at a new physical filename; preserve existing C freeze")
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import pandas as pd
    import numpy as np
    from scripts.backfill_tdcc_public_history import BASE,digest,atomic_json
    from scripts.normalize_tdcc_public_history import canonical_summary
    if args.feature_source:
        import importlib.util
        function_path=args.feature_source.resolve()
        assert function_path.is_relative_to(ROOT)
        spec=importlib.util.spec_from_file_location("tdcc_history_ab_frozen",function_path)
        feature_module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(feature_module)
        compute_tdcc_features=feature_module.compute_tdcc_features
        TDCC_FEATURE_COLS=feature_module.TDCC_FEATURE_COLS
    else:
        from ml.features.tdcc import compute_tdcc_features,TDCC_FEATURE_COLS
        function_path=ROOT/"ml/features/tdcc.py"
    assert re.fullmatch(r"[a-z0-9_]+",args.stage_label)
    stage=ROOT/"output/historical_gaps_20260906"/args.stage_label
    original=args.source_root.resolve()
    if args.mode=="freeze":
        assert not stage.exists(),"Do not overwrite frozen baseline"
        stage.mkdir(parents=True)
        source_summary=original/"集保分散/tdcc_summary.csv"
        data=source_summary.read_bytes()
        (stage/"summary_before.csv").write_bytes(data)
        summary=pd.read_csv(io.BytesIO(data),dtype={"Date":str,"Ticker":str})
        assert not summary.duplicated(["Date","Ticker"]).any()
        rows=[]
        manifest=[]
        if args.source_manifest:
            import duckdb
            source_scope=json.loads(args.source_manifest.read_text(encoding="utf-8"))
            assert args.v3_dir and digest(Path(source_scope["db_path"]))==source_scope["db_sha256"]
            connection=duckdb.connect(source_scope["db_path"],read_only=True)
            frames=[]
            for ticker,item in source_scope["raw_metadata"].items():
                frame=connection.execute('SELECT * FROM daily_k WHERE _ab_ticker = ? AND TRY_CAST("Date" AS DATE) <= ? ORDER BY TRY_CAST("Date" AS DATE) DESC,_ab_row DESC LIMIT 1',[ticker,"2026-09-04"]).fetchdf()
                assert len(frame)==1,f"No valid as-of raw row for{ticker}"
                source_info={"ticker":ticker,"db_sha256":source_scope["db_sha256"],"original_source_sha256":item["source_sha256"],"branch":item["branch"]}
                if ticker in source_scope["new_tickers"]:
                    path=args.v3_dir/f"{ticker}.csv"
                    content=path.read_bytes()
                    overlay=pd.read_csv(io.BytesIO(content))
                    overlay["Date"]=pd.to_datetime(overlay.Date)
                    overlay=overlay[overlay.Date<=pd.Timestamp("2026-09-04")].sort_values("Date").iloc[-1:]
                    assert pd.Timestamp(frame.Date.iloc[0])==overlay.Date.iloc[0]
                    columns=["Open","High","Low","Close","Volume"]
                    assert np.isclose(frame[columns].to_numpy(float),overlay[columns].to_numpy(float),rtol=0,atol=0,equal_nan=True).all()
                    frame=overlay
                    source_info.update(path=str(path),v3_sha256=hashlib.sha256(content).hexdigest())
                frames.append((ticker,frame,source_info))
            connection.close()
        else:
            frames=[]
            for path in sorted((original/"日K資料").glob("*.csv")):
                if not re.fullmatch(r"\d{4}",path.stem):continue
                content=path.read_bytes()
                frames.append((path.stem,pd.read_csv(io.BytesIO(content)),{"ticker":path.stem,"path":str(path),"sha256":hashlib.sha256(content).hexdigest()}))
        for ticker,frame,source_info in frames:
            frame["Date"]=pd.to_datetime(frame["Date"])
            frame=frame[frame.Date<=pd.Timestamp("2026-09-04")].sort_values("Date")
            if frame.empty:continue
            row=frame.iloc[-1:].copy()
            row["Ticker"]=ticker
            rows.append(row)
            manifest.append(dict(source_info,actual_asof_date=row.Date.iloc[0].date().isoformat()))
        pd.concat(rows,ignore_index=True).to_csv(stage/"daily_last.csv",index=False,encoding="utf-8-sig")
        assert digest(function_path)==digest(original/"ml/features/tdcc.py")
        result={"frozen_at":datetime.now().isoformat(),"summary_path":str(source_summary),"summary_sha256":hashlib.sha256(data).hexdigest(),"summary_rows":len(summary),
                "feature_code_sha256":digest(function_path),"summarizer_source_sha256":digest(original/"twstock.py"),"daily_frame_sha256":digest(stage/"daily_last.csv"),"daily_sources":manifest,
                "source_pool_manifest_sha256":digest(args.source_manifest) if args.source_manifest else None,"v3_dir":str(args.v3_dir) if args.v3_dir else None,"tickers":len(rows)}
        atomic_json(stage/"frozen.json",result)
        print(json.dumps({k:v for k,v in result.items() if k!="daily_sources"},ensure_ascii=False),flush=True)
        return
    frozen=json.loads((stage/"frozen.json").read_text(encoding="utf-8"))
    assert digest(stage/"summary_before.csv")==frozen["summary_sha256"]
    assert digest(stage/"daily_last.csv")==frozen["daily_frame_sha256"]
    assert digest(function_path)==frozen["feature_code_sha256"]
    assert digest(original/"集保分散/tdcc_summary.csv")==frozen["summary_sha256"],"Concurrent production summary changed; re-freeze explicitly"
    plan=json.loads((BASE/"plan.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[a-z0-9_]+",args.normalized_stage)
    normalized=ROOT/"output/historical_gaps_20260906"/args.normalized_stage
    progress=json.loads((normalized/"progress_snapshot.json").read_text(encoding="utf-8"))
    assert json.loads((normalized/"plan_snapshot.json").read_text(encoding="utf-8"))==plan
    priority_tickers={x["ticker"] for x in frozen["daily_sources"]}
    coverage=[]
    new_raw=stage/"new_raw"
    new_raw.mkdir(exist_ok=True)
    source_manifests=[]
    absent=[]
    for week in plan["weeks"]:
        day=week["date"]
        states=progress.get(day,{})
        required=set(week["tickers"])&priority_tickers if args.priority_only else set(week["tickers"])
        assert required<=set(states),"Required plan traversal incomplete"
        assert all(states[t]["status"] in ("VALIDATED","SOURCE_NO_DATA") for t in required),"Required source/parser errors remain"
        coverage.append({"date":day,"full_plan":len(week["tickers"]),"attempted":len(states),"required_source_pool_jobs":len(required),"required_terminal":True,
                         "source_pool_not_in_frozen_historical_union":sorted(priority_tickers-set(week["tickers"])),"whole_plan_terminal":set(states)==set(week["tickers"]) and all(x["status"] in ("VALIDATED","SOURCE_NO_DATA") for x in states.values())})
        meta=json.loads((normalized/f"{day}_manifest.json").read_text(encoding="utf-8"))
        expected={t for t,x in states.items() if x["status"]=="VALIDATED"}
        assert {x["ticker"] for x in meta["source_manifest"]}==expected
        path=Path(meta["path"])
        assert digest(path)==meta["sha256"]
        frame=pd.read_csv(path,dtype={"資料日期":str,"證券代號":str})
        assert set(frame["資料日期"])=={day} and set(frame["證券代號"])==expected
        assert not frame.duplicated(["資料日期","證券代號","持股分級"]).any()
        shutil.copy2(path,new_raw/path.name)
        source_manifests.append({"path":str(new_raw/path.name),"normalized_input_path":str(path),"sha256":meta["sha256"],"rows":len(frame),"observed_tickers":len(expected)})
        absent.extend({"date":day,"ticker":t,"raw_path":x["raw_path"],"raw_sha256":x["raw_sha256"],"status":x["status"]} for t,x in states.items() if x["status"]=="SOURCE_NO_DATA")
    first=Path(source_manifests[0]["normalized_input_path"])
    added=canonical_summary(first,new_raw,original/"twstock.py")
    before=pd.read_csv(stage/"summary_before.csv",dtype={"Date":str,"Ticker":str})
    keys=["Date","Ticker"]
    assert not added.duplicated(keys).any()
    assert not len(before.set_index(keys).index.intersection(added.set_index(keys).index))
    assert len(added)==sum(x["observed_tickers"] for x in source_manifests)
    after=pd.concat([before,added],ignore_index=True).sort_values(["Ticker","Date"]).reset_index(drop=True)
    old_index=before.set_index(keys).sort_index()
    after_index=after.set_index(keys).sort_index()
    pd.testing.assert_frame_equal(old_index,after_index.loc[old_index.index],check_exact=True,check_dtype=False)
    after.to_csv(stage/"summary_after.csv",index=False,encoding="utf-8-sig")
    daily=pd.read_csv(stage/"daily_last.csv",dtype={"Ticker":str})
    details=[]
    changes=[]
    for ticker,frame in daily.groupby("Ticker",sort=True):
        frame=frame.drop(columns="Ticker").copy()
        frame["Date"]=pd.to_datetime(frame.Date)
        pair=[compute_tdcc_features(frame,ticker,str(stage/name)).iloc[-1] for name in ("summary_before.csv","summary_after.csv")]
        a,b=[x[TDCC_FEATURE_COLS].to_numpy(float) for x in pair]
        equal=np.isclose(a,b,rtol=0,atol=0,equal_nan=True)
        row={"ticker":ticker,"date":frame.Date.iloc[-1].date().isoformat(),"changed_cells":int((~equal).sum())}
        for i,col in enumerate(TDCC_FEATURE_COLS):
            row[f"before_{col}"]=a[i]
            row[f"after_{col}"]=b[i]
            if not equal[i]:
                changes.append({"ticker":ticker,"date":row["date"],"feature":col,"before":float(a[i]) if np.isfinite(a[i]) else None,"after":float(b[i]) if np.isfinite(b[i]) else None})
        details.append(row)
    pd.DataFrame(details).to_csv(stage/"latest_ab.csv",index=False,encoding="utf-8-sig")
    result={"status":"LATEST_TDCC_FEATURE_AB_COMPLETE_NOT_PROMOTED","completed_at":datetime.now().isoformat(),"source_weeks":source_manifests,"official_no_data":absent,
        "priority_only":args.priority_only,"coverage":coverage,"whole_plan_terminal":all(x["whole_plan_terminal"] for x in coverage),
        "summary_before_rows":len(before),"summary_added_rows":len(added),"summary_after_rows":len(after),"existing_summary_cells_exact":True,
        "summary_before_sha256":frozen["summary_sha256"],"summary_after_sha256":digest(stage/"summary_after.csv"),"feature_code_sha256":frozen["feature_code_sha256"],
        "daily_frame_sha256":frozen["daily_frame_sha256"],"tickers":len(details),"features":TDCC_FEATURE_COLS,"compared_cells":len(details)*len(TDCC_FEATURE_COLS),"changed_cells":len(changes),"changes":changes,
        "method":"Actual canonical _summarize_tdcc on all recovered raw tickers, append only new date/ticker keys to one frozen existing summary. All existing summary cells remain exact. Both canonical compute_tdcc_features branches receive the full historical summary prefix and identical actual latest daily rows; streak/rolling history is not truncated.",
        "limits":"Complete traversal of frozen adjacent-week union does not independently prove an exact-date market census. Explicit official no-data rows remain absent, never zeros. Historical feature/prediction impacts are not claimed absent. Root reviews coverage and latest changes before any promotion/invalidation."}
    atomic_json(stage/"result.json",result)
    print(json.dumps({k:v for k,v in result.items() if k not in ("official_no_data","changes")},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
