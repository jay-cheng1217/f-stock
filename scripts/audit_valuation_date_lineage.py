"""Compare the frozen bad May10 TWSE data against May18 local/official values."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]


def main():
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    import requests
    import truststore
    import pandas as pd
    import numpy as np
    from scripts.stage_valuation_transfer_repair import official_valuation,sha,NUMERIC
    truststore.inject_into_ssl()
    stage=ROOT/"output/historical_gaps_20260906/valuation_day_20210510"
    before=stage/"source_before.csv"
    local_path=Path("F:/stock/估值資料/valuation_20210518.csv")
    frames=[pd.read_csv(p,dtype={"Ticker":str}) for p in (before,local_path)]
    old,local=[f[f.Market.eq("上市")].set_index("Ticker").sort_index() for f in frames]
    url="https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20210518&response=json"
    path=stage/"official_20210518_TWSE.json"
    assert not path.exists(),"Preserve original network evidence"
    response=requests.get(url,timeout=45)
    response.raise_for_status()
    path.write_bytes(response.content)
    official=official_valuation(response.json(),"上市","20210518").set_index("Ticker").sort_index()
    result={"status":"READ_ONLY_DATE_LINEAGE_AUDIT","fetched_at":datetime.now().isoformat(),"url":url,"http_status":response.status_code,
        "frozen_bad_may10_sha256":sha(before),"local_may18_sha256":sha(local_path),"official_may18_sha256":sha(path),"comparisons":[]}
    for label,reference in [("local20210518",local),("official20210518",official)]:
        common=old.index.intersection(reference.index)
        equal=np.isclose(old.loc[common,NUMERIC].to_numpy(float),reference.loc[common,NUMERIC].to_numpy(float),rtol=0,atol=0,equal_nan=True)
        result["comparisons"].append({"reference":label,"bad_may10_rows":len(old),"reference_rows":len(reference),"common_tickers":len(common),"keys_exact":old.index.equals(reference.index),"name_cells_equal":int((old.loc[common,"Name"]==reference.loc[common,"Name"]).sum()),"numeric_cells":int(equal.size),"numeric_changed_cells":int((~equal).sum())})
    result["conclusion"]="Whole-market numeric/date fingerprint can establish wrong-day content if the exact keys and all values match May18. Original acquisition request/overwrite mechanism remains unproven without historical logs."
    target=ROOT/"ml/reports/research/data_layer_consistency_20260906/valuation_20210510_date_lineage.json"
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":main()
