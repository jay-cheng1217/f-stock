"""Audit historical staging and prove the latest foreign feature boundary.

Read-only source frames are frozen per ticker for both branches. Research
reports are the only outputs; no database, model, cache or ledger is mutated.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "output/historical_gaps_20260906"
REPORT = ROOT / "ml/reports/research/data_layer_consistency_20260906"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def main():
    global REPORT
    assert ROOT == Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0,str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root == ROOT
    import numpy as np
    import pandas as pd
    from scripts.stage_historical_gaps import validate_foreign_payload
    from ml.features.institutional import compute_institutional_features

    zero_repair = "--zero-repair" in sys.argv
    if zero_repair:
        REPORT = REPORT / "foreign_zero"
    REPORT.mkdir(parents=True,exist_ok=True)
    inventory=json.loads((STAGE/"inventory.json").read_text(encoding="utf-8"))
    staged=json.loads((STAGE/"foreign_candidates.json").read_text(encoding="utf-8"))
    all_files=sorted((ROOT/"外資持股").glob("*.csv"))
    before_files={p.name:p for p in all_files}
    after_files=dict(before_files)
    after_files.update({Path(x["target"]).name:Path(x["target"]) for x in staged})
    if zero_repair:
        zero_manifest=json.loads((STAGE/"zero_repair/manifest.json").read_text(encoding="utf-8"))
        before_files={x["name"]:Path(zero_manifest["source_root"])/"外資持股"/x["name"] for x in zero_manifest["input_manifest"]}
        assert all(sha(before_files[x["name"]])==x["sha256"] for x in zero_manifest["input_manifest"]),"Current sources changed since zero staging"
        after_files=dict(before_files)
        after_files.update({Path(x["target"]).name:Path(x["target"]) for x in zero_manifest["files"]})
    latest_name=max(before_files)
    assert max(after_files)==latest_name=="20260904.csv"
    maps=[]
    for files in (before_files,after_files):
        frame=pd.read_csv(files[max(files)],dtype={"Ticker":str})
        maps.append(frame.set_index("Ticker")[["Issued_Shares","Foreign_Pct"]].sort_index())
    pd.testing.assert_series_equal(maps[0].Issued_Shares,maps[1].Issued_Shares,check_exact=True)
    api_changes=maps[0].Foreign_Pct.isna()&maps[1].Foreign_Pct.eq(0)
    pd.testing.assert_series_equal(maps[0].loc[~api_changes,"Foreign_Pct"],maps[1].loc[~api_changes,"Foreign_Pct"],check_exact=True)
    if not zero_repair:
        assert not api_changes.any()

    feature_columns=[f"foreign_cumsum_{w}d{suffix}" for w in (1,3,5,10,20) for suffix in ("","_norm")]+["foreign_trust_sync","foreign_reversal_buy","foreign_buy_streak","turnover_rate"]
    records=[]
    source_manifest=[]
    feature_paths=[] if "--inventory-only" in sys.argv else sorted((ROOT/"日K資料").glob("*.csv"))
    for path in feature_paths:
        if not re.fullmatch(r"\d{4}",path.stem):
            continue
        raw=pd.read_csv(path)
        if "Date" not in raw or raw.empty:
            continue
        raw["Date"]=pd.to_datetime(raw.Date,errors="raise")
        raw=raw[raw.Date<=pd.Timestamp("2026-09-04")].sort_values("Date").reset_index(drop=True)
        if raw.empty:
            continue
        pair=[]
        for source in maps:
            features=compute_institutional_features(raw)
            shares=source.loc[path.stem,"Issued_Shares"] if path.stem in source.index else None
            features["turnover_rate"]=(features.Volume/shares*100).astype(np.float32) if shares is not None and shares>0 else np.nan
            pair.append(features[feature_columns].iloc[-1])
        a,b=pair[0].to_numpy(float),pair[1].to_numpy(float)
        equal=np.isclose(a,b,rtol=0,atol=0,equal_nan=True)
        assert equal.all(),f"Latest foreign feature changed for {path.stem}"
        record={"ticker":path.stem,"date":raw.Date.iloc[-1].date().isoformat(),"changed_cells":int((~equal).sum())}
        for col in feature_columns:
            record[f"before_{col}"]=pair[0][col]
            record[f"after_{col}"]=pair[1][col]
        records.append(record)
        source_manifest.append({"ticker":path.stem,"sha256":sha(path),"rows":len(raw),"asof":record["date"]})
    if "--inventory-only" not in sys.argv:
        pd.DataFrame(records).to_csv(REPORT/"foreign_latest_group_ab.csv",index=False,encoding="utf-8-sig")
        write(REPORT/"foreign_latest_group_ab_manifest.json",source_manifest)
    ab={"status":"PASS","as_of":"2026-09-04","tickers":len(records),"feature_columns":feature_columns,
        "compared_cells":len(records)*len(feature_columns),"changed_cells":0,"max_abs_delta":0.0,
        "input_snapshot":"Each full as-of daily frame read once and passed unchanged to canonical institutional feature function in both branches; holdings file map overlay is only variable.",
        "latest_selected_file":latest_name,"latest_sha256":sha(before_files[latest_name]),"latest_map_rows":len(maps[0]),
        "latest_after_sha256":sha(after_files[latest_name]),
        "latest_issued_shares_map_exact_equal":True,
        "latest_issued_shares_and_foreign_pct_maps_exact_equal":not bool(api_changes.any()),
        "api_unknown_to_zero":int(api_changes.sum()),"api_changed_tickers":maps[0].index[api_changes].tolist(),
        "explanation":"foreign_cumsum1/3/5/10/20 read daily Foreign_BuySell, not ownership CSV. V1/V2/T1 use ownership latest Issued_Shares for turnover, which remains exact. App uses latest Foreign_Pct; the zero-repair branch explicitly reports unknown-to-zero API changes.",
        "cache":"dataset._raw_cache_source_patterns includes ownership/*.csv, so atomic promotion changes mtime and next load_raw_cache invalidates automatically. Process issued-share memo selects same latest file; no numerical change. No cache, snapshot or prediction is rewritten by this audit.",
        "prediction_scope":"This proves the affected latest input feature group is exactly equal; it is not a new model inference or full-pipeline execution claim."}
    if "--inventory-only" in sys.argv:
        ab=json.loads((REPORT/"foreign_latest_group_ab.json").read_text(encoding="utf-8"))
        assert ab["latest_sha256"]==sha(before_files[latest_name])
        prior_manifest=json.loads((REPORT/"foreign_latest_group_ab_manifest.json").read_text(encoding="utf-8"))
        assert all(sha(ROOT/"日K資料"/f"{x['ticker']}.csv")==x["sha256"] for x in prior_manifest)
    else:
        write(REPORT/"foreign_latest_group_ab.json",ab)
    if "--feature-only" in sys.argv:
        print(json.dumps(ab,ensure_ascii=False),flush=True)
        return

    # The 2021 ticket concerned partial daily files, not blanket zero-null rules.
    daily=pd.read_csv(ROOT/"日K資料/2330.csv",usecols=["Date"])
    daily_dates=pd.to_datetime(daily.Date)
    expected=set(daily_dates[daily_dates.dt.year.eq(2021)].dt.strftime("%Y%m%d"))
    valuations=[]
    for path in sorted((ROOT/"估值資料").glob("valuation_2021*.csv")):
        df=pd.read_csv(path,dtype={"Ticker":str})
        assert {"Ticker","PE_Ratio","PB_Ratio","Dividend_Yield","Market"}<=set(df.columns)
        market_counts=df.Market.value_counts().to_dict()
        numbers=df[["PE_Ratio","PB_Ratio","Dividend_Yield"]].apply(pd.to_numeric,errors="raise")
        assert len(df)>=1500 and set(market_counts)=={"上市","上櫃"}
        assert min(market_counts.values())>=650
        assert not np.isinf(numbers.to_numpy(float)).any()
        assert numbers.PB_Ratio.notna().mean()>=.80 and numbers.Dividend_Yield.dropna().mean()<=30
        valuations.append({"date":path.stem[-8:],"rows":len(df),"markets":market_counts,
            "nonnull_counts":numbers.notna().sum().to_dict(),"sha256":sha(path),
            "duplicate_tickers":sorted(df.loc[df.Ticker.duplicated(False),"Ticker"].unique().tolist())})
    missing=sorted(expected-{v["date"] for v in valuations})
    valuation={"status":"OLD_PARTIAL_TICKET_NOT_REPRODUCIBLE" if not missing else "OPEN",
        "files":len(valuations),"expected_2330_sessions":len(expected),"missing_dates":missing,
        "min_rows":min(v["rows"] for v in valuations),"max_rows":max(v["rows"] for v in valuations),
        "semantic_findings":[v for v in valuations if v["duplicate_tickers"]],
        "evidence":valuations,"limit":"Old handover did not name its3files; all2021 trading-session files now pass dual-market/schema/coverage/numeric checks.20210510has3092cross-market duplicate needing listing-transfer/source review, so not an all-data-clean verdict. No claim that all historical valuations were independently redownloaded.Official blank PE may be legitimate losses; nulls are preserved."}
    write(REPORT/"valuation_2021_closure.json",valuation)

    # Reconcile raw DATA dates by weekly bucket; never treat a Thursday as absent.
    observed=set()
    raw_dates=[]
    for path in sorted((ROOT/"集保分散").glob("tdcc_2*.csv")):
        date_values=pd.read_csv(path,usecols=[0],dtype=str).iloc[:,0].dropna().astype(str).unique()
        parsed=pd.to_datetime(pd.Series(date_values),format="mixed",errors="raise")
        assert len(parsed)==1, f"Mixed dates in {path.name}"
        actual=parsed.iloc[0]
        observed.add(str(actual.to_period("W-FRI")))
        raw_dates.append({"file":path.name,"actual_date":actual.date().isoformat(),"week":str(actual.to_period("W-FRI")),"sha256":sha(path)})
    old=json.loads((ROOT/"ml/reports/retrain02_tdcc_gap_impact_20260717.json").read_text(encoding="utf-8"))["global_missing_periods"]
    page=(STAGE/"raw/tdcc_history_page.txt").read_text(encoding="utf-8")
    published=re.findall(r'<option value="(\d{8})"',page)
    published_by_week={str(pd.Timestamp(d).to_period("W-FRI")):d for d in published}
    index_dates=pd.to_datetime(pd.read_csv(ROOT/"大盤指數/index_TWII.csv",usecols=["Date"]).Date)
    calendar=[]
    for week in old:
        period=pd.Period(week,freq="W-FRI")
        sessions=index_dates[(index_dates>=period.start_time)&(index_dates<=period.end_time)]
        within=period.end_time>=pd.Timestamp(min(published))
        category="PRESENT" if week in observed else "PUBLIC_DATED_QUERY_AVAILABLE" if week in published_by_week else "NO_OBSERVATION_IN_CURRENT_OFFICIAL_CALENDAR" if within and not len(sessions) else "OUTSIDE_PUBLIC_ONE_YEAR_WINDOW"
        calendar.append({"week":week,"raw_present":week in observed,"official_published_date":published_by_week.get(week),
            "twii_sessions":len(sessions),"twii_last_session":sessions.max().date().isoformat() if len(sessions) else None,
            "category":category,"caution":"TDCC uses last business/settlement day, not necessarily last trading day. Older no-trade weeks require archived official calendar proof before being called missing or closed."})
    tdcc={"status":"PARTIAL_ACCESSIBLE_HISTORY_NOT_YET_UNIVERSE_BACKFILLED","old43_by_actual_raw_bucket":calendar,
        "category_counts":dict(Counter(w["category"] for w in calendar)),"raw_files":raw_dates,
        "public_calendar_min":min(published),"public_calendar_max":max(published),
        "official_samples":"tdcc_public_samples.json:3dated2330observations prove free source exists; not eligible for whole-market raw replacement",
        "blocking_detail":"Legacy queryStockCustStat bulk endpoint returns HTTP404. Current public form supports one named security with session token. Full3-week model universe requires about6000dated security requests plus validation; not classified as paywall or source unavailable. No summary or feature changed in this bounded probe.",
        "official_sources":["https://www.tdcc.com.tw/portal/zh/smWeb/qryStock","https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear=115&response=html","https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69bde495d019c0d7f8f0100e2.pdf"]}
    write(REPORT/"tdcc_historical_gap_inventory.json",tdcc)

    # Identify official-looking unknowns without claiming that they are repaired.
    unknown_codes=Counter()
    unknown_by_column=Counter()
    affected_files=0
    for path in all_files:
        df=pd.read_csv(path,dtype={"Ticker":str})
        nums=df[["Issued_Shares","Foreign_Held","Foreign_Pct"]].apply(pd.to_numeric,errors="coerce")
        mask=nums.isna().any(axis=1)
        if mask.any():
            affected_files+=1
            unknown_codes.update(df.loc[mask,"Ticker"])
            unknown_by_column.update(nums.isna().sum().to_dict())
    result={"scope":"Research staging only; root owns atomic promotion and downstream release", "inventory":inventory,
        "foreign_validated_candidates":staged,"latest_group_ab":ab,
        "foreign_existing_unknowns":{"files":affected_files,"by_column":dict(unknown_by_column),"top_tickers":unknown_codes.most_common(20),"status":"NOT_IMPUTED_NOT_GLOBALLY_CLOSED"},
        "valuation_status":{k:v for k,v in valuation.items() if k!="evidence"},
        "tdcc_status":{k:v for k,v in tdcc.items() if k!="raw_files"},
        "source_urls":["https://www.twse.com.tw/zh/trading/foreign/mi-qfiis.html","https://www.tpex.org.tw/web/stock/3insti/qfii/qfii.php?l=zh-tw"]}
    write(REPORT/"historical_gap_repair.json",result)
    lines=["# 歷史資料缺口盤點與隔離回補 — 2026-09-06","",
        f"外資持股舊110失敗日已不是現況：盤點{inventory['foreign']['files']}檔，現有缺檔3日加單市場殘檔1日。已從TWSE+TPEx實抓並驗證4日共{sum(x['rows'] for x in staged):,}列，僅寫入staging，待root原子promotion。","",
        "| 日期 | 舊列數 | 官方驗證列數 | 結果 |","|---|---:|---:|---|"]
    lines += [f"| {x['date']} | {x['source_before_rows']} | {x['rows']} | 日期exact、雙市場、無重複、數值完整、股數/比率一致 |" for x in staged]
    lines += ["",f"9/4最新影響：{ab['tickers']}檔×{len(feature_columns)}項foreign/turnover特徵，共{ab['compared_cells']:,}格，A/B改變0、最大差0。相同完整as-of日K逐檔只讀一次，兩分支共用；唯一變因是4日ownership替代路徑。9/4latest檔與Issued_Shares/Foreign_Pct map逐值完全相同。", "",
        "foreign_cumsum 1/3/5/10/20來自法人買賣超流量，並非外資持股存量。V1/V2/T1周轉率及API持股比率只讀ownership最新檔。raw dataset cache仍會因歷史CSV mtime變動而在下次load時自動失效，不能把本次零差異說成已重跑完整預測；本輪未寫快取/預測。", "",
        f"2021估值舊3殘檔票：目前{valuation['files']}檔、{valuation['expected_2330_sessions']}個2330實際交易日、缺日期{len(missing)}；全數雙市場、至少{valuation['min_rows']}列，schema/數值/覆蓋檢查通過。另5/10有3092跨市場重複，需核對轉上市日期與來源；不能宣稱2021全無問題。舊報告未列3個檔名，故只結論舊partial缺陷目前不可重現，不宣稱全量官方逐值重抓。", "",
        "TDCC：以raw實際資料日期轉W-FRI檢查，週四/其他營業日均正確入週。原43格仍未觀測，但不可等同43個可補週。近一年官方日期表及實際2330查詢證明10/9、10/23、2/26免費可得；2/20全週休市且官方無該週觀測。其餘超出官方一年保存窗，舊春節無交易週還需保留『未觀測』與『真缺檔』的區分。", "",
        "旧bulk API返回404；現行官方逐券表單可用，3週全市場約需6000次限速/斷點請求，本輪只做3檔次可得性驗證，沒有把單券資料塞入全市場週檔，也未重建summary。這3週不能再標成必須付費或沒有免費來源。", "",
        f"另外既有外資資料{affected_files}日存在unknown，欄位計數={dict(unknown_by_column)}。來源未知值未填0、未宣稱全數修復；詳見JSON top ticker供另行來源查證。", "",
        "官方來源：[TWSE外資持股](https://www.twse.com.tw/zh/trading/foreign/mi-qfiis.html)、[TPEx外資持股](https://www.tpex.org.tw/web/stock/3insti/qfii/qfii.php?l=zh-tw)、[TDCC歷史表單與一年保存說明](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock)、[TWSE春節休市公告](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69bde495d019c0d7f8f0100e2.pdf)。", "",
        "可promotion清單及source_before/after SHA見foreign_candidates；最新group比較CSV、raw來源回應、TDCC單券HTML、2021逐檔SHA均保存。沒有訓練、變動策略/權重/門檻、操作production DB或帳本。"]
    (REPORT/"historical_gap_repair.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"foreign_candidate_days":len(staged),"feature_ab":ab,"valuation":{k:v for k,v in valuation.items() if k not in {"evidence","limit"}},"tdcc_categories":tdcc["category_counts"],"reports":str(REPORT)},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
