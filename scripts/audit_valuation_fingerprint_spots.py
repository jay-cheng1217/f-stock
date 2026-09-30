"""Three actual official date probes for the newly discovered fingerprint family."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]


def main():
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import pandas as pd
    import numpy as np
    import requests
    import truststore
    from scripts.valuation_source_contract import parse_official_valuation,NUMERIC_COLUMNS
    truststore.inject_into_ssl()
    stage=ROOT/"output/historical_gaps_20260906/valuation_fingerprint_spots"
    assert not stage.exists()
    stage.mkdir(parents=True)
    results=[]
    session=requests.Session()
    for day in ("20260302","20260318","20251022"):
        url=f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={day}&response=json"
        response=session.get(url,timeout=45)
        response.raise_for_status()
        raw=stage/f"official_twse_{day}.json"
        raw.write_bytes(response.content)
        official=parse_official_valuation(response.json(),"上市",day).set_index("Ticker").sort_index()
        local_path=Path(f"F:/stock/估值資料/valuation_{day}.csv")
        local=pd.read_csv(local_path,dtype={"Ticker":str})
        local=local[local.Market.eq("上市")].set_index("Ticker").sort_index()
        common=local.index.intersection(official.index)
        equal=np.isclose(local.loc[common,NUMERIC_COLUMNS].to_numpy(float),official.loc[common,NUMERIC_COLUMNS].to_numpy(float),rtol=0,atol=0,equal_nan=True)
        result={"date":day,"url":url,"http_status":response.status_code,"response_date":response.json()["date"],"official_raw_sha256":hashlib.sha256(response.content).hexdigest(),"local_before_sha256":hashlib.sha256(local_path.read_bytes()).hexdigest(),
            "local_rows":len(local),"official_rows":len(official),"common_tickers":len(common),"missing_official_tickers":official.index.difference(local.index).tolist(),"wrong_extra_local_tickers":local.index.difference(official.index).tolist(),
            "numeric_compared_cells":int(equal.size),"numeric_changed_cells":int((~equal).sum()),"changed_tickers":int((~equal).any(axis=1).sum()),
            "local2330":local.loc["2330",NUMERIC_COLUMNS].to_dict(),"official2330":official.loc["2330",NUMERIC_COLUMNS].to_dict()}
        results.append(result)
        time.sleep(3)
    report={"status":"OFFICIAL_THREE_DATE_DIAGNOSTIC_NOT_REPAIRED","asof":datetime.now().isoformat(),"results":results,
        "limits":"This is a bounded source diagnostic, not full57-date official reconciliation. Original request/overwrite mechanism is not established. No production source, model, database, prediction or ledger was changed."}
    path=ROOT/"ml/reports/research/data_layer_consistency_20260906/valuation_fingerprint_official_spots.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=="__main__":main()
