"""Rebuild the entire proven mixed-source2021-05-10 valuation day in staging.

Current official exact-date TWSE and TPEx responses are the only replacement
source. Compare all cells and the latest ticker-local five-feature values.
No production source, model, cache, database or ledger is overwritten.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root",type=Path,required=True)
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import numpy as np
    import pandas as pd
    from scripts.stage_valuation_transfer_repair import official_valuation,NUMERIC,sha
    from ml.features.valuation import compute_valuation_features,VALUATION_FEATURE_COLS
    source_root=args.source_root.resolve()
    stage=ROOT/"output/historical_gaps_20260906/valuation_day_20210510"
    assert not stage.exists(),"Preserve audited output; use a new staging receipt"
    stage.mkdir(parents=True)
    raw=ROOT/"output/historical_gaps_20260906/raw"
    markets=[]
    evidence=[]
    for market,label in [("上市","valuation_20210510_TWSE"),("上櫃","valuation_20210510_TPEx")]:
        path=raw/f"{label}.txt"
        frame=official_valuation(json.loads(path.read_text(encoding="utf-8-sig")),market,"20210510")
        assert len(frame)>=650
        markets.append(frame)
        evidence.append({"market":market,"rows":len(frame),"sha256":sha(path),"metadata":json.loads((raw/f"{label}.json").read_text(encoding="utf-8"))})
    source_path=source_root/"估值資料/valuation_20210510.csv"
    original=source_path.read_bytes()
    before_sha=hashlib.sha256(original).hexdigest()
    source=pd.read_csv(io.BytesIO(original),dtype={"Ticker":str})
    candidate=pd.concat(markets,ignore_index=True)[source.columns]
    assert not candidate.Ticker.duplicated().any() and len(candidate)>=1500
    target=stage/source_path.name
    candidate.to_csv(target,index=False,encoding="utf-8-sig")
    (stage/"source_before.csv").write_bytes(original)
    pd.testing.assert_frame_equal(candidate,pd.read_csv(target,dtype={"Ticker":str}),check_exact=True)
    old=source.set_index(["Ticker","Market"])
    new=candidate.set_index(["Ticker","Market"])
    deleted=old.index.difference(new.index).tolist()
    added=new.index.difference(old.index).tolist()
    assert deleted==[("3092","上市")] and not added
    cells=[]
    for key in old.index.intersection(new.index):
        for col in NUMERIC:
            a,b=old.loc[key,col],new.loc[key,col]
            if not (pd.isna(a) and pd.isna(b)) and a!=b:
                cells.append({"ticker":key[0],"market":key[1],"field":col,"before":float(a) if pd.notna(a) else None,"after":float(b) if pd.notna(b) else None})

    # Only trailing60 observations can enter any latest valuation feature.
    # Freeze each source once, retain every available row if fewer than60.
    # Per-ticker cutoff is its actual last as-of daily bar, not a guessed date.
    daily={}
    for path in sorted((ROOT/"日K資料").glob("*.csv")):
        if not re.fullmatch(r"\d{4}",path.stem):
            continue
        frame=pd.read_csv(path,usecols=["Date"])
        dates=pd.to_datetime(frame.Date)
        dates=dates[dates<=pd.Timestamp("2026-09-04")]
        if len(dates):
            daily[path.stem]={"date":dates.max(),"sha256":sha(path)}
    counts=Counter()
    histories=[]
    manifests=[]
    paths=[p for p in sorted((source_root/"估值資料").glob("valuation_*.csv"),reverse=True) if p.stem[-8:]<="20260904"]
    for path in paths:
        day=pd.Timestamp(path.stem[-8:])
        wanted={t for t in daily if counts[t]<60 and day<=daily[t]["date"]}
        if not wanted:
            continue
        content=path.read_bytes()
        frame=pd.read_csv(io.BytesIO(content),dtype={"Ticker":str})
        rows=frame[frame.Ticker.isin(wanted)].copy()
        if rows.empty:
            continue
        counts.update(rows.Ticker)
        histories.append((path.name,rows))
        manifests.append({"name":path.name,"sha256":hashlib.sha256(content).hexdigest(),"retained_rows":len(rows)})
    branches=[stage/"latest_before",stage/"latest_after"]
    for directory in branches:
        directory.mkdir()
    for name,rows in reversed(histories):
        rows.to_csv(branches[0]/name,index=False,encoding="utf-8-sig")
        after=rows if name!=source_path.name else candidate[candidate.Ticker.isin(set(rows.Ticker))]
        after.to_csv(branches[1]/name,index=False,encoding="utf-8-sig")
    latest=[]
    for ticker,item in daily.items():
        frame=pd.DataFrame({"Date":[item["date"]]})
        pair=[compute_valuation_features(frame,ticker,str(d)).iloc[-1] for d in branches]
        a,b=[x[VALUATION_FEATURE_COLS].to_numpy(float) for x in pair]
        equal=np.isclose(a,b,rtol=0,atol=0,equal_nan=True)
        record={"ticker":ticker,"date":item["date"].date().isoformat(),"valuation_observations":counts[ticker],"changed_cells":int((~equal).sum()),"daily_sha256":item["sha256"]}
        record.update({f"before_{c}":pair[0][c] for c in VALUATION_FEATURE_COLS})
        record.update({f"after_{c}":pair[1][c] for c in VALUATION_FEATURE_COLS})
        latest.append(record)
    assert sha(source_path)==before_sha
    report=ROOT/"ml/reports/research/data_layer_consistency_20260906"
    pd.DataFrame(cells).to_csv(report/"valuation_20210510_cell_changes.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(latest).to_csv(report/"valuation_20210510_latest_ab.csv",index=False,encoding="utf-8-sig")
    result={"status":"SOURCE_ONLY_CANDIDATE_VALIDATED_NOT_PROMOTED","date":"2021-05-10","source_path":str(source_path),"source_before_sha256":before_sha,
        "candidate_path":str(target),"candidate_sha256":sha(target),"before_rows":len(source),"after_rows":len(candidate),
        "deleted_keys":deleted,"added_keys":added,"changed_numeric_cells":len(cells),"changed_tickers":len({x["ticker"] for x in cells}),
        "changes_by_market":dict(Counter(x["market"] for x in cells)),"changes_by_field":dict(Counter(x["field"] for x in cells)),
        "unknown_to_known":sum(x["before"] is None for x in cells),"known_to_official_unknown":sum(x["after"] is None for x in cells),
        "official_sources":evidence,"latest_feature_ab":{"tickers":len(latest),"features":VALUATION_FEATURE_COLS,"compared_cells":len(latest)*len(VALUATION_FEATURE_COLS),
            "changed_cells":sum(x["changed_cells"] for x in latest),"changed_tickers":[x["ticker"] for x in latest if x["changed_cells"]],
            "method":"Canonical valuation function over identical frozen per-ticker as-of trailing60 raw observations, the complete dependency window for latest5features. The only variable is the repaired2021-05-10day. Each latest daily Date is frozen once."},
        "feature_source_manifest":manifests,"old_origin":"The original request/response provenance has not established why the local TWSE values differ. Do not assert a vendor revision, cache issue, or wrong requested date without evidence.",
        "promotion":"Parent compare-and-swap CURRENT before SHA. No source overwrite, cache invalidation, model training, database or ledger write performed here.",
        "limits":"Full day official numeric reconstruction, not only duplicate removal. Latest feature equality is tested; historical full-universe rolling changes and historical model predictions are not claimed unchanged. The earlier3092-only A/B is insufficient for this expanded candidate."}
    (report/"valuation_20210510_repair.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in {"official_sources","feature_source_manifest"}},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
