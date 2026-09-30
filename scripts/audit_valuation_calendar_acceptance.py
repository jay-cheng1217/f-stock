"""Verify new source-code versions from new physical filenames, preserving C freeze."""
from datetime import date,datetime
import hashlib
import importlib.util
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
    # Load physically copied, byte-identical production versions under their
    # canonical module names. No function or inference predicate is replaced.
    code=[]
    for name in ("valuation_source_contract","backfill_valuation"):
        copied=ROOT/f"scripts/{name}_calendar.py"
        original=Path(f"F:/stock/scripts/{name}.py")
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        assert digest(copied)==digest(original)
        spec=importlib.util.spec_from_file_location(f"scripts.{name}",copied)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        code.append({"production_path":str(original),"physical_test_path":str(copied),"sha256":digest(copied)})
    import pytest
    result=pytest.main(["-q","-o","log_file=logs/valuation_calendar_fixture.log","--basetemp=output/valuation_calendar_fixture_tmp","tests/test_backfill_valuation_calendar_version.py"])
    assert result==0
    from scripts.backfill_valuation import generate_trading_days,_VERIFIED_MARKET_YEAR_CLOSURES
    days=generate_trading_days(date(2021,1,1),date(2021,12,31))
    local={p.stem[-8:] for p in Path("F:/stock/估值資料").glob("valuation_2021*.csv")}
    actual={d.strftime("%Y%m%d") for d in days}
    assert actual==local and len(days)==244
    evidence={"status":"PASS_HISTORICAL_YEAR_CALENDAR_AND_FIXTURES","asof":datetime.now().isoformat(),"code":code,
        "year":2021,"official_url":"https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20210101&response=json",
        "calendar_sessions":len(days),"existing_valuation_dates":len(local),"date_sets_exact":True,"official_closure_dates":sorted(d.isoformat() for d in _VERIFIED_MARKET_YEAR_CLOSURES[2021]),
        "scope":"Real production calendar acquisition, exact response year validated; all2021sessions match actual244source dates. Unsupported years never inherit current-year holidays. Source rows/files and strategy/model rules unchanged."}
    (ROOT/"ml/reports/research/data_layer_consistency_20260906/valuation_calendar_acceptance.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(evidence,ensure_ascii=False),flush=True)


if __name__=="__main__":main()
