"""Stage the proven 3092 pre-listing valuation duplicate repair, source only.

Require exact official source dates, the official transfer notice and both
markets' price tapes. Freeze identical per-ticker valuation histories for the
canonical feature A/B; no database, cache, prediction or source is overwritten.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
NUMERIC=["PE_Ratio","PB_Ratio","Dividend_Yield"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_valuation(payload,market,day):
    from scripts.valuation_source_contract import parse_official_valuation
    return parse_official_valuation(payload,market,day)


def repair_duplicate(source,twse,tpex,*,day,listing_date,ticker):
    import pandas as pd
    if not day<listing_date:
        raise ValueError("Cannot use pre-listing exclusion on or after listing")
    if ticker in set(twse.Ticker) or len(tpex[tpex.Ticker.eq(ticker)])!=1:
        raise ValueError("Official market presence does not support removal")
    mask=source.Ticker.eq(ticker)
    group=source.loc[mask]
    if len(group)!=2 or sorted(group.Market.tolist())!=["上市","上櫃"]:
        raise ValueError("Expected exactly one row from each market")
    valid=group[group.Market.eq("上櫃")].reset_index(drop=True)
    official=tpex[tpex.Ticker.eq(ticker)].reset_index(drop=True)
    pd.testing.assert_frame_equal(valid[["Ticker","Market"]+NUMERIC],official[["Ticker","Market"]+NUMERIC],check_exact=True,check_dtype=False)
    repaired=source.loc[~(mask&source.Market.eq("上市"))].reset_index(drop=True)
    if repaired.Ticker.duplicated().any():
        raise ValueError("Other duplicates remain; do not claim unique source")
    return repaired


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
    from bs4 import BeautifulSoup
    from ml.features.valuation import compute_valuation_features,VALUATION_FEATURE_COLS
    source_root=args.source_root.resolve()
    stage=ROOT/"output/historical_gaps_20260906/valuation_3092"
    assert not stage.exists(),"Preserve earlier audited staging; do not overwrite"
    stage.mkdir(parents=True)
    raw=ROOT/"output/historical_gaps_20260906/raw"
    def payload(label):
        return json.loads((raw/f"{label}.txt").read_text(encoding="utf-8-sig"))
    twse=official_valuation(payload("valuation_20210510_TWSE"),"上市","20210510")
    tpex=official_valuation(payload("valuation_20210510_TPEx"),"上櫃","20210510")
    assert len(twse)>=650 and len(tpex)>=650
    notice="".join(BeautifulSoup((raw/"3092_transfer_TPEx.txt").read_text(encoding="utf-8"),"html.parser").stripped_strings)
    assert "3092" in notice and "110年5月13日" in notice and "終止" in notice and "上市" in notice
    twse_price=payload("price_3092_202105_TWSE")
    assert twse_price["date"]=="20210501" and twse_price["stat"]=="OK" and "3092" in twse_price["title"]
    twse_dates=[r[twse_price["fields"].index("日期")] for r in twse_price["data"]]
    assert min(twse_dates)=="110/05/13" and "110/05/10" not in twse_dates
    otc_price=payload("price_3092_202105_TPEx_current")
    assert otc_price["date"]=="20210501" and otc_price["code"]=="3092" and otc_price["stat"]=="ok"
    table=otc_price["tables"][0]
    tape=pd.DataFrame(table["data"],columns=table["fields"])
    target_tape=tape[tape["日 期"].eq("110/05/10")]
    assert len(target_tape)==1 and float(target_tape.iloc[0]["收盤"])==73.6
    path=source_root/"估值資料/valuation_20210510.csv"
    original=path.read_bytes()
    source=pd.read_csv(io.BytesIO(original),dtype={"Ticker":str})
    before_sha=hashlib.sha256(original).hexdigest()
    candidate=repair_duplicate(source,twse,tpex,day="20210510",listing_date="20210513",ticker="3092")
    candidate_path=stage/path.name
    candidate.to_csv(candidate_path,index=False,encoding="utf-8-sig")
    (stage/"source_before.csv").write_bytes(original)
    reread=pd.read_csv(candidate_path,dtype={"Ticker":str})
    pd.testing.assert_frame_equal(candidate,reread,check_exact=True)
    pd.testing.assert_frame_equal(source[~(source.Ticker.eq("3092")&source.Market.eq("上市"))].reset_index(drop=True),reread,check_exact=True)
    assert sha(path)==before_sha
    official=pd.concat([twse,tpex],ignore_index=True).set_index(["Ticker","Market"]).sort_index()
    local=reread.set_index(["Ticker","Market"]).sort_index()
    assert local.index.equals(official.index)
    numeric_equal=np.isclose(local[NUMERIC].to_numpy(float),official[NUMERIC].to_numpy(float),rtol=0,atol=0,equal_nan=True)

    # Valuation features are ticker-local. Keep the full history of the only
    # changed ticker and an unchanged control, read each source byte stream once.
    dirs=[stage/"feature_before",stage/"feature_after"]
    for directory in dirs:
        directory.mkdir()
    history_manifest=[]
    for val_path in sorted((source_root/"估值資料").glob("valuation_*.csv")):
        content=val_path.read_bytes()
        frame=pd.read_csv(io.BytesIO(content),dtype={"Ticker":str})
        subset=frame[frame.Ticker.isin(["3092","2330"])]
        if subset.empty:
            continue
        history_manifest.append({"name":val_path.name,"sha256":hashlib.sha256(content).hexdigest(),"retained_rows":len(subset)})
        subset.to_csv(dirs[0]/val_path.name,index=False,encoding="utf-8-sig")
        after=subset if val_path.name!=path.name else reread[reread.Ticker.isin(["3092","2330"])]
        after.to_csv(dirs[1]/val_path.name,index=False,encoding="utf-8-sig")
    feature_results=[]
    changes=[]
    for ticker in ("3092","2330"):
        daily_path=ROOT/f"日K資料/{ticker}.csv"
        daily=pd.read_csv(daily_path)
        daily["Date"]=pd.to_datetime(daily.Date)
        daily=daily[daily.Date<=pd.Timestamp("2026-09-04")].sort_values("Date").reset_index(drop=True)
        branches=[compute_valuation_features(daily,ticker,str(d)) for d in dirs]
        a,b=[x[VALUATION_FEATURE_COLS].to_numpy(float) for x in branches]
        equal=np.isclose(a,b,rtol=0,atol=0,equal_nan=True)
        for ri,ci in np.argwhere(~equal):
            changes.append({"ticker":ticker,"date":daily.Date.iloc[ri].date().isoformat(),"feature":VALUATION_FEATURE_COLS[ci],
                            "before":float(a[ri,ci]) if np.isfinite(a[ri,ci]) else None,
                            "after":float(b[ri,ci]) if np.isfinite(b[ri,ci]) else None})
        if ticker=="2330":
            assert equal.all(),"Unchanged control differs"
        assert equal[-1].all(),"Latest valuation features changed"
        feature_results.append({"ticker":ticker,"rows":len(daily),"daily_sha256":sha(daily_path),"changed_cells":int((~equal).sum()),
             "changed_dates":int((~equal).any(axis=1).sum()),"latest_date":daily.Date.iloc[-1].date().isoformat(),
             "latest_before":{c:float(branches[0][c].iloc[-1]) if pd.notna(branches[0][c].iloc[-1]) else None for c in VALUATION_FEATURE_COLS},
             "latest_after":{c:float(branches[1][c].iloc[-1]) if pd.notna(branches[1][c].iloc[-1]) else None for c in VALUATION_FEATURE_COLS},"latest_exact_equal":True})
    report=ROOT/"ml/reports/research/data_layer_consistency_20260906"
    report.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(changes).to_csv(report/"valuation_3092_feature_changes.csv",index=False,encoding="utf-8-sig")
    result={"status":"VALIDATED_SOURCE_ONLY_CANDIDATE_NOT_PROMOTED","date":"2021-05-10","ticker":"3092","listing_date":"2021-05-13",
        "source_path":str(path),"source_before_sha256":before_sha,"candidate_path":str(candidate_path),"candidate_sha256":sha(candidate_path),
        "before_rows":len(source),"after_rows":len(candidate),"removed_row":source[source.Ticker.eq("3092")&source.Market.eq("上市")].iloc[0].to_dict(),
        "retained_row":candidate[candidate.Ticker.eq("3092")].iloc[0].to_dict(),"official_twse_rows":len(twse),"official_tpex_rows":len(tpex),
        "all_remaining_market_ticker_keys_match_official":True,"all_remaining_numeric_cells_equal_official":bool(numeric_equal.all()),
        "remaining_numeric_mismatches":int((~numeric_equal).sum()),"tpex_tape_close":73.6,"twse_tape_first_date":min(twse_dates),
        "canonical_feature_ab":feature_results,"history_manifest":history_manifest,
        "changed_features":{c:sum(x["feature"]==c for x in changes) for c in VALUATION_FEATURE_COLS},
        "change_date_start":min((x["date"] for x in changes),default=None),"change_date_end":max((x["date"] for x in changes),default=None),
        "source_evidence":[{"label":label,"sha256":sha(raw/f"{label}.txt"),"request":json.loads((raw/f"{label}.json").read_text(encoding="utf-8"))["url"]} for label in
            ["valuation_20210510_TWSE","valuation_20210510_TPEx","price_3092_202105_TWSE","price_3092_202105_TPEx_current","3092_transfer_TPEx"]],
        "invariants":"Only one inapplicable pre-listing row removed. All other original cells preserved exactly after CSV reread. No strategy, threshold, model, database or ledger change.",
        "impact_boundary":"Valuation feature function filters each ticker before rolling. Other tickers' source records are identical; unchanged2330control verified. Historical3092rolling effects quantified, latest5valuationfeatures exactequal. No new model inference or training claim.",
        "cache":"Long-lived valuation._VALUATION_CACHE is keyed only by directory. Parent must account for that cache on promotion; this audit uses fresh independent staging paths and does not mutate process/global production cache."}
    assert sha(path)==before_sha
    (report/"valuation_3092_repair.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in {"history_manifest","source_evidence"}},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
