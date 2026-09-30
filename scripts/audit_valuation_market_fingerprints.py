"""Read-only wrong-day family scan, with the proven old May10 as a control."""
from collections import defaultdict
from datetime import datetime
import hashlib
import io
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
    import pandas as pd
    sources=sorted(Path("F:/stock/估值資料").glob("valuation_*.csv"))
    control=ROOT/"output/historical_gaps_20260906/valuation_day_20210510/source_before.csv"
    groups=defaultdict(list)
    issues=[]
    manifest=[]
    for path in sources+[control]:
        content=path.read_bytes()
        frame=pd.read_csv(io.BytesIO(content),dtype={"Ticker":str})
        frame["Ticker"]=frame.Ticker.str.strip()
        frame["Name"]=frame.Name.astype(str).str.strip()
        origin="known_pre_repair_control_20210510" if path==control else path.stem[-8:]
        if frame.duplicated("Ticker").any():
            issues.append({"date":origin,"duplicate_tickers":sorted(frame.loc[frame.duplicated("Ticker",keep=False),"Ticker"].unique().tolist())})
        manifest.append({"date":origin,"source_path":str(path),"sha256":hashlib.sha256(content).hexdigest(),"rows":len(frame)})
        for market,rows in frame.groupby("Market"):
            normalized=rows[["Ticker","Name","PE_Ratio","PB_Ratio","Dividend_Yield"]].sort_values("Ticker").to_csv(index=False,na_rep="UNKNOWN").encode("utf-8")
            key=(market,hashlib.sha256(normalized).hexdigest())
            groups[key].append({"date":origin,"rows":len(rows)})
    matches=[{"market":key[0],"fingerprint_sha256":key[1],"members":members} for key,members in groups.items() if len(members)>1]
    positive=next(x for x in matches if x["market"]=="上市" and any(m["date"]=="known_pre_repair_control_20210510" for m in x["members"]))
    assert any(m["date"]=="20210518" for m in positive["members"]),"Known wrong-day positive control not detected"
    candidates=[]
    for match in matches:
        current=[x for x in match["members"] if not x["date"].startswith("known_")]
        if len(current)>1:
            candidates.append(dict(match,members=current))
    result={"status":"READ_ONLY_MARKET_FINGERPRINT_SCREEN","asof":datetime.now().isoformat(),"production_files":len(sources),"market_groups":sum(len(v) for v in groups.values())-2,
        "current_cross_date_identical_market_groups":candidates,"known_positive_control":positive,"duplicate_ticker_findings":issues,"source_manifest":manifest,
        "limits":"Identical full-market numeric/name/ticker fingerprints identify suspected wrong-day copies, not which date is correct. A negative result does not prove every source date/value correct; nonidentical wrong-day or partial-overwrite cases require official date provenance. This scan makes no network requests and changes no source."}
    target=ROOT/"ml/reports/research/data_layer_consistency_20260906/valuation_market_fingerprint_screen.json"
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="source_manifest"},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
