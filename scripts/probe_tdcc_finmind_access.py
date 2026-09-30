"""One bounded historical TDCC bulk access check with existing credentials.

Only sends the configured token to its intended FinMind API in an Authorization
header. Never logs credentials, request headers, or account configuration.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root",type=Path,required=True)
    ap.add_argument("--date",type=date.fromisoformat,default=date(2021,9,10))
    args=ap.parse_args()
    assert ROOT==Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root==ROOT
    from dotenv import dotenv_values
    import requests
    token=os.environ.get("FINMIND_TOKEN","").strip()
    if not token:
        token=str(dotenv_values(args.source_root.resolve()/".env",encoding="utf-8-sig").get("FINMIND_TOKEN") or "").strip()
    out=ROOT/"output/historical_gaps_20260906/finmind"
    out.mkdir(parents=True,exist_ok=True)
    result={"checked_at":datetime.now().isoformat(),"dataset":"TaiwanStockHoldingSharesPer",
            "requested_date":args.date.isoformat(),"scope":"Single exact-date whole-market query; no purchase or account change",
            "token_configured":bool(token),"endpoint":"https://api.finmindtrade.com/api/v4/data"}
    if not token:
        result["status"]="NO_EXISTING_TOKEN"
    else:
        started=time.monotonic()
        try:
            response=requests.get(result["endpoint"],headers={"Authorization":f"Bearer {token}"},
                params={"dataset":result["dataset"],"start_date":args.date.isoformat(),"end_date":args.date.isoformat()},timeout=60)
            result["http_status"]=response.status_code
            payload=response.json()
            result["api_status"]=payload.get("status")
            result["message"]=str(payload.get("msg","")).replace(token,"[REDACTED]")[:500]
            data=payload.get("data") or []
            result["rows"]=len(data)
            result["response_dates"]=sorted({str(x.get("date")) for x in data})
            result["tickers"]=len({str(x.get("stock_id")) for x in data})
            result["status"]="ACCESS_AND_DATA_AVAILABLE" if response.status_code==200 and payload.get("status")==200 and data else "ACCESS_OR_DATA_UNAVAILABLE"
            safe={"status":payload.get("status"),"msg":result["message"],"data":data}
            raw=json.dumps(safe,ensure_ascii=False,allow_nan=False).encode("utf-8")
            raw_path=out/f"tdcc_{args.date:%Y%m%d}.json"
            raw_path.write_bytes(raw)
            result["raw_path"]=str(raw_path)
            result["sha256"]=hashlib.sha256(raw).hexdigest()
        except Exception as exc:
            result.update(status="REQUEST_FAILED",error_type=type(exc).__name__)
        result["seconds"]=round(time.monotonic()-started,3)
    (out/"access_probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
