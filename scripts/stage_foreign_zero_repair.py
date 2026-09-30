"""Stage proven ownership-percentage zeros from CURRENT source files.

The only edited cell is missing Foreign_Pct where held/issued proves an exact
zero or a displayed zero below0.01%, supported by two fresh official samples.
Original bytes and every changed cell are retained. No production write.
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


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def repair_mask(frame):
    import numpy as np
    issued=frame.Issued_Shares
    held=frame.Foreign_Held
    derived=held/issued*100
    valid=np.isfinite(issued)&np.isfinite(held)&issued.gt(0)&held.ge(0)
    return frame.Foreign_Pct.isna()&valid&derived.ge(0)&derived.lt(.01)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,required=True)
    args=parser.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    from scripts.stage_historical_gaps import validate_foreign_payload
    import pandas as pd
    source=(args.source_root.resolve()/"外資持股")
    stage=ROOT/"output/historical_gaps_20260906/zero_repair"
    assert not stage.exists(),"Use a new staging receipt; do not overwrite prior audited candidates"
    stage.mkdir(parents=True)
    (stage/"inputs").mkdir()
    (stage/"candidates").mkdir()
    evidence=[]
    # Directly verify displayed numeric0 for BOTH exact zero and positive held.
    for day in ("20200102","20260904"):
        official_path=ROOT/f"output/historical_gaps_20260906/raw/foreign_zero_{day}_TWSE.txt"
        official,_=validate_foreign_payload(json.loads(official_path.read_text(encoding="utf-8-sig")),"TWSE",day)
        old=pd.read_csv(source/f"{day}.csv",dtype={"Ticker":str})
        pair=old.merge(official,on="Ticker",suffixes=("_old","_official"),validate="one_to_one")
        matches=pair.Foreign_Pct_old.isna()&pair.Foreign_Pct_official.eq(0)&pair.Issued_Shares_old.eq(pair.Issued_Shares_official)&pair.Foreign_Held_old.eq(pair.Foreign_Held_official)
        matched=pair[matches]
        assert len(matched[matched.Foreign_Held_old.gt(0)])>=1,"No official evidence for positive-but-displayed-zero bucket"
        assert len(matched[matched.Foreign_Held_old.eq(0)])>=1
        evidence.append({"date":day,"official_sha256":sha_bytes(official_path.read_bytes()),
            "source_sha256":sha_bytes((source/f"{day}.csv").read_bytes()),
            "exact_zero_matched":int(matched.Foreign_Held_old.eq(0).sum()),
            "positive_held_displayed_zero_matched":int(matched.Foreign_Held_old.gt(0).sum()),
            "examples":matched[["Ticker","Issued_Shares_old","Foreign_Held_old","Foreign_Pct_official"]].head(25).to_dict("records")})
    files=[]
    changes=[]
    all_sources=[]
    latest=None
    for path in sorted(source.glob("*.csv")):
        original=path.read_bytes()
        before_sha=sha_bytes(original)
        frame=pd.read_csv(io.BytesIO(original),dtype={"Ticker":str})
        mask=repair_mask(frame)
        all_sources.append({"name":path.name,"sha256":before_sha,"rows":len(frame)})
        if not mask.any():
            continue
        assert not frame.Ticker.duplicated().any()
        repaired=frame.copy()
        repaired.loc[mask,"Foreign_Pct"]=0.0
        pd.testing.assert_frame_equal(frame.drop(columns="Foreign_Pct"),repaired.drop(columns="Foreign_Pct"),check_exact=True)
        pd.testing.assert_series_equal(frame.loc[~mask,"Foreign_Pct"],repaired.loc[~mask,"Foreign_Pct"],check_exact=True)
        input_copy=stage/"inputs"/path.name
        target=stage/"candidates"/path.name
        input_copy.write_bytes(original)
        repaired.to_csv(target,index=False,encoding="utf-8-sig")
        assert sha_bytes(path.read_bytes())==before_sha,"Live source changed during staging"
        exact=mask&frame.Foreign_Held.eq(0)
        rounded=mask&frame.Foreign_Held.gt(0)
        files.append({"date":path.stem,"target":str(target),"sha256":sha_bytes(target.read_bytes()),
            "source_before_path":str(path),"source_before_sha256":before_sha,
            "input_copy":str(input_copy),"rows":len(frame),"changed_cells":int(mask.sum()),
            "exact_zero_cells":int(exact.sum()),"displayed_zero_cells":int(rounded.sum()),
            "remaining_unknown":int(repaired.Foreign_Pct.isna().sum())})
        for row in frame.loc[mask].to_dict("records"):
            changes.append({"date":path.stem,"ticker":row["Ticker"],"old_value":None,"new_value":0.0,
                "Issued_Shares":row["Issued_Shares"],"Foreign_Held":row["Foreign_Held"],
                "derived_pct":row["Foreign_Held"]/row["Issued_Shares"]*100,
                "basis":"exact held0 / issued>0" if row["Foreign_Held"]==0 else "displayed0.00 validated against two official dated responses; derived pct in[0,.01)"})
        latest={"date":path.stem,"rows":len(frame),"api_unknown_to_zero":int(mask.sum()),
                "issued_shares_unchanged":True,"api_changed_tickers":frame.loc[mask,"Ticker"].tolist(),
                "before_sha256":before_sha,"after_sha256":sha_bytes(target.read_bytes())}
    # Final before-hash check makes this manifest safe for root's compare-and-swap.
    assert all(sha_bytes((source/x["name"]).read_bytes())==x["sha256"] for x in all_sources)
    result={"status":"VALIDATED_STAGING_ONLY","source_root":str(args.source_root.resolve()),
        "files":files,"input_manifest":all_sources,"official_evidence":evidence,"latest_changed":latest,
        "file_count":len(files),"changed_cells":len(changes),
        "exact_zero_cells":sum(x["exact_zero_cells"] for x in files),
        "displayed_zero_cells":sum(x["displayed_zero_cells"] for x in files),
        "remaining_unknown":sum(x["remaining_unknown"] for x in files),
        "invariants":"Same row order/tickers/issued/held/all nonmissing percentages; only proven missing displayed0 changed. Source files unchanged, including the4previously promoted dates.",
        "promotion":"Root must compare every CURRENT source_before_sha256 before atomic replacement; skip or re-stage if any conflict. Not yet promoted."}
    (stage/"manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    pd.DataFrame(changes).to_csv(stage/"changed_cells.csv",index=False,encoding="utf-8-sig")
    print(json.dumps({k:v for k,v in result.items() if k not in {"files","input_manifest","official_evidence"}},ensure_ascii=False))


if __name__=="__main__":
    main()
