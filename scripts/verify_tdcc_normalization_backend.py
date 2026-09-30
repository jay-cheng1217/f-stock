"""Compare offline TDCC parser backends on preserved real responses and fixtures."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collector-source",type=Path,required=True)
    ap.add_argument("--normalizer-source",type=Path,required=True)
    ap.add_argument("--fixture",type=Path,required=True)
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    def load(path,name):
        path=path.resolve()
        assert path.is_relative_to(ROOT)
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        spec.loader.exec_module(module)
        return module
    collector=load(args.collector_source,"scripts.backfill_tdcc_public_history")
    legacy=load(ROOT/"scripts/normalize_tdcc_public_history.py","tdcc_legacy_normalizer")
    candidate=load(args.normalizer_source,"scripts.normalize_tdcc_public_history")
    progress=json.loads((collector.BASE/"progress.json").read_text(encoding="utf-8"))
    records=[]
    elapsed={"legacy_double_html_parser":0.,"single_lxml":0.}
    for day,items in progress.items():
        valid=sorted((ticker,item) for ticker,item in items.items() if item["status"]=="VALIDATED")
        selected={int(i*(len(valid)-1)/39) for i in range(40)}
        selected.update(i for i,(_,item) in enumerate(valid) if item["validation"]["adjustment_shares"]<0)
        for index in sorted(selected):
            ticker,item=valid[index]
            path=Path(item["raw_path"])
            assert collector.digest(path)==item["raw_sha256"]
            html=path.read_text(encoding="utf-8")
            start=time.perf_counter()
            a=legacy.normalize_response(html,day,ticker)
            elapsed["legacy_double_html_parser"]+=time.perf_counter()-start
            start=time.perf_counter()
            b=candidate.normalize_response(html,day,ticker,parser_backend="lxml")
            elapsed["single_lxml"]+=time.perf_counter()-start
            assert a==b,f"Parser backend differs:{day}/{ticker}"
            records.append({"date":day,"ticker":ticker,"raw_sha256":item["raw_sha256"],"rows":len(a[0])})
    import pytest
    assert args.fixture.resolve().is_relative_to(ROOT)
    code=pytest.main([str(args.fixture),"-q","-s","-p","no:cacheprovider",
                     "--log-file",str(ROOT/"logs/tdcc_parser_backend_fixtures.log"),
                     "--basetemp",str(ROOT/"output/tdcc_backend_fixture_tmp")])
    assert code==0
    report={"status":"PASS_REAL_SOURCE_EXACT_AND_FIXTURES","samples":len(records),"timing_seconds":elapsed,
            "collector_sha256":collector.digest(args.collector_source),"normalizer_sha256":collector.digest(args.normalizer_source),
            "fixture_sha256":collector.digest(args.fixture),"sources":records,
            "scope":"Offline HTML parser reuse/backend optimization only; every identity,level,total,percentage check retained. No inference/strategy logic or official network traffic."}
    path=ROOT/"ml/reports/research/data_layer_consistency_20260906/tdcc_normalization_backend.json"
    assert not path.exists()
    collector.atomic_json(path,report)
    print(json.dumps({k:v for k,v in report.items() if k!="sources"},ensure_ascii=False))


if __name__=="__main__":main()
