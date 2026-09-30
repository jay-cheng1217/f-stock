"""Run both real production fetch functions in an isolated source-only smoke."""
from __future__ import annotations
from datetime import date,datetime
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
    from scripts.backfill_valuation import fetch_twse_valuation,fetch_tpex_valuation,combine_complete_sources
    from scripts.valuation_source_contract import parse_official_valuation
    from scripts.stage_valuation_transfer_repair import sha
    stage=ROOT/"output/historical_gaps_20260906/valuation_day_20210510"
    results=[fetch_twse_valuation("20210510"),fetch_tpex_valuation(date(2021,5,10))]
    combined=combine_complete_sources(*results)
    candidate=stage/"valuation_20210510.csv"
    expected=pd.read_csv(candidate,dtype={"Ticker":str})
    pd.testing.assert_frame_equal(combined,expected,check_exact=True,check_dtype=False)
    # The actual wrong-day source is rejected even though every number looks
    # plausible and its whole-market row count exceeds the completion floor.
    may18=json.loads((stage/"official_20210518_TWSE.json").read_text(encoding="utf-8-sig"))
    try:
        parse_official_valuation(may18,"上市","20210510")
    except ValueError as exc:
        rejection=str(exc)
    else:
        raise AssertionError("Actual wrong-date official source was accepted")
    result={"status":"PASS_REAL_FETCH_AND_WRONG_DAY_REJECTION","asof":datetime.now().isoformat(),"requested_date":"20210510",
        "source_statuses":[x.status for x in results],"source_rows":[len(x.frame) for x in results],"combined_rows":len(combined),"candidate_sha256":sha(candidate),
        "exact_candidate_equal":True,"actual_official20210518_as20210510_rejection":rejection,
        "production_fetch_sha256":sha(ROOT/"scripts/backfill_valuation.py"),"pure_contract_sha256":sha(ROOT/"scripts/valuation_source_contract.py"),
        "scope":"Real network and production fetch functions, only source rows in memory. No source promotion, DB, cache, ledger, model training or prediction performed.",
        "empty_policy":"Empty, missing-date or unrecognized-schema payload now returns error after bounded existing retries, not legal no_data. A holiday must be established by its calendar/official closure workflow."}
    (ROOT/"ml/reports/research/data_layer_consistency_20260906/valuation_fetch_contract_acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":main()
