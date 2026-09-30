"""Build a guarded, source-only TDCC review package after complete collection.

No production files are written. Official no-data requires two independently
preserved exact responses; it is not treated as proof of business eligibility.
The resulting promotion plan still requires the integration owner's review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("F:/stock"))
    parser.add_argument("--normalized-stage", default="tdcc_normalized_complete_2134")
    parser.add_argument("--ab-stage", default="tdcc_feature_ab_2031")
    args = parser.parse_args()
    assert ROOT == Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0, str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED and _INSTALLED.root == ROOT
    from scripts.backfill_tdcc_public_history import BASE, atomic_json, digest, exact_no_data

    research = ROOT / "output/historical_gaps_20260906"
    normalized = (research / args.normalized_stage).resolve()
    ab = (research / args.ab_stage).resolve()
    assert normalized.is_relative_to(research) and ab.is_relative_to(research)
    original = args.source_root.resolve()
    destination = ROOT / "ml/reports/research/data_layer_consistency_20260906"
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "tdcc_history_completion.json"
    assert not report_path.exists(), "Preserve prior final evidence"
    plan = json.loads((normalized / "plan_snapshot.json").read_text(encoding="utf-8"))
    progress = json.loads((normalized / "progress_snapshot.json").read_text(encoding="utf-8"))
    assert plan == json.loads((BASE / "plan.json").read_text(encoding="utf-8"))
    assert progress == json.loads((BASE / "progress.json").read_text(encoding="utf-8"))
    result_path = ab / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["whole_plan_terminal"] and not result["priority_only"]
    assert result["tickers"] == 2031 and result["existing_summary_cells_exact"]
    assert digest(ab / "summary_before.csv") == result["summary_before_sha256"]
    assert digest(ab / "summary_after.csv") == result["summary_after_sha256"]
    assert digest(original / "集保分散/tdcc_summary.csv") == result["summary_before_sha256"]
    assert digest(ab / "daily_last.csv") == result["daily_frame_sha256"]
    assert digest(original / "ml/features/tdcc.py") == result["feature_code_sha256"]

    weeks, absence, files = [], [], []
    total_valid = 0
    for week in plan["weeks"]:
        day = week["date"]
        states = progress[day]
        assert set(states) == set(week["tickers"])
        counts = Counter(item["status"] for item in states.values())
        assert set(counts) <= {"VALIDATED", "SOURCE_NO_DATA"}
        meta_path = normalized / f"{day}_manifest.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        valid = {ticker for ticker, item in states.items() if item["status"] == "VALIDATED"}
        assert valid == {item["ticker"] for item in meta["source_manifest"]}
        assert meta["observed_tickers"] == len(valid)
        assert digest(Path(meta["path"])) == meta["sha256"]
        raw = ab / "new_raw" / f"tdcc_{day}.csv"
        assert digest(raw) == meta["sha256"]
        target = original / "集保分散" / raw.name
        assert not target.exists(), "A formerly missing source week changed concurrently"
        files.append({"source": str(raw), "target": f"集保分散/{raw.name}",
                      "before_sha256": None, "after_sha256": meta["sha256"]})
        for ticker, item in states.items():
            if item["status"] != "SOURCE_NO_DATA":
                continue
            assert "confirmed independently" in item.get("classification", "")
            confirmed = []
            for attempt in item["attempts"]:
                if attempt.get("http_status") != 200:
                    continue
                source = Path(attempt["raw_path"])
                assert digest(source) == attempt["raw_sha256"]
                # Early collector versions logged exact empty responses as
                # unresolved parser errors. Revalidate the original bytes;
                # retain the original status instead of rewriting its audit.
                if not exact_no_data(source.read_text(encoding="utf-8"), day, ticker):
                    continue
                confirmed.append({"path": str(source), "sha256": attempt["raw_sha256"],
                                  "queried_at": attempt["queried_at"], "original_logged_status": attempt["status"]})
            assert len({x["path"] for x in confirmed}) >= 2
            absence.append({"date": day, "ticker": ticker, "status": "OFFICIAL_NO_DATA_CONFIRMED_TWICE",
                            "responses": confirmed,
                            "eligibility": "Not inferred; exact-date issuer eligibility remains unproven"})
        levels = Counter(tuple(x["canonical_observed_levels"]) for x in meta["source_manifest"])
        weeks.append({"date": day, "planned": len(states), "validated": len(valid),
                      "official_no_data_confirmed_twice": counts["SOURCE_NO_DATA"],
                      "unresolved_acquisition": 0, "remaining_unattempted": 0,
                      "raw_rows": meta["observed_rows"], "raw_sha256": meta["sha256"],
                      "raw_manifest_path": str(meta_path), "raw_manifest_sha256": digest(meta_path),
                      "observed_level_patterns": [{"levels": list(k), "tickers": v} for k, v in levels.items()]})
        total_valid += len(valid)
    assert total_valid == result["summary_added_rows"]
    assert len(absence) == len(result["official_no_data"])
    files.append({"source": str(ab / "summary_after.csv"), "target": "集保分散/tdcc_summary.csv",
                  "before_sha256": result["summary_before_sha256"], "after_sha256": result["summary_after_sha256"]})
    reviews = [BASE / "targeted_recheck_2134.json", BASE / "independent_no_data_confirmation_2134.json"]
    recoveries = []
    for review in reviews:
        record = json.loads(review.read_text(encoding="utf-8"))
        recoveries.extend({key: item[key] for key in ("date", "ticker", "before_status", "status", "prior_raw_sha256", "raw_sha256")}
                          for item in record["outcomes"] if item["status"] == "VALIDATED")
    reference = research / "tdcc_normalized/reference.json"
    reference_record = json.loads(reference.read_text(encoding="utf-8"))
    assert reference_record["canonical_summary_exact"] and reference_record["common_normal_and_total_cells_exact"]
    import numpy as np
    import pandas as pd
    latest = pd.read_csv(ab / "latest_ab.csv", dtype={"ticker": str}, float_precision="round_trip").set_index("ticker")
    assert len(latest) == 2031 and latest.index.is_unique
    assert int(latest["changed_cells"].sum()) == result["changed_cells"]
    for source in reference_record["summary"]:
        ticker = source["Ticker"]
        assert latest.loc[ticker, "date"] == "2026-09-04"
        for column, field in (("Whale_Pct", "whale_pct"), ("Retail_Pct", "retail_pct")):
            expected = float(np.float32(source[column]))
            assert np.isfinite(expected)
            assert latest.loc[ticker, f"before_{field}"] == expected
            assert latest.loc[ticker, f"after_{field}"] == expected
    backend = destination / "tdcc_normalization_backend.json"
    backend_record = json.loads(backend.read_text(encoding="utf-8"))
    assert backend_record["status"] == "PASS_REAL_SOURCE_EXACT_AND_FIXTURES"
    report = {
        "status": "SOURCE_REVIEW_PACKAGE_COMPLETE_NOT_PROMOTED",
        "completed_at": datetime.now().astimezone().isoformat(), "weeks": weeks,
        "fixed_plan_sha256": digest(BASE / "plan.json"),
        "progress_snapshot_sha256": digest(normalized / "progress_snapshot.json"),
        "planned_queries": sum(w["planned"] for w in weeks), "validated_queries": total_valid,
        "official_no_data_count": len(absence), "unresolved_acquisition_count": 0,
        "official_no_data": absence, "bounded_recheck_recoveries": recoveries,
        "latest_feature_ab": {key: result[key] for key in (
            "tickers", "features", "compared_cells", "changed_cells", "changes",
            "summary_before_rows", "summary_added_rows", "summary_after_rows", "existing_summary_cells_exact",
            "summary_before_sha256", "summary_after_sha256", "feature_code_sha256", "daily_frame_sha256")},
        "requires_separate_prediction_D_review": bool(result["changed_cells"]),
        "raw_contract_reference": {"path": str(reference), "sha256": digest(reference)},
        "parser_backend_equivalence": {"path": str(backend), "sha256": digest(backend), "actual_samples": backend_record["samples"]},
        "latest_base_features_match_four_official_reference_tickers": True,
        "limits": [
            "Complete traversal covers a frozen adjacent-week ticker union, not an independently certified exact-date market census.",
            "Fifty no-data outcomes were independently rechecked after two transient false-empty cases were proven. Two no-data replies do not establish issuer eligibility.",
            "Normal levels 1-15 and actual total17 are retained; source adjustment16 is retained only if present. Never synthesize an absent zero adjustment or issuer.",
            "Latest comparison uses all2031actual source rows including inactive tickers at their own latest dates and new84classic-source overlay; full historical TDCC prefix is preserved.",
            "This is source/feature reconciliation, not a strategy backtest or a claim that historical predictions are unchanged.",
            "Older unobserved buckets remain35with trading days plus1settlement-only week;4full-closure weeks are not fabricated. No paid access was purchased."]}
    atomic_json(report_path, report)
    evidence = [report_path, result_path, reference, backend, *reviews,
                normalized / "plan_snapshot.json", normalized / "progress_snapshot.json"]
    evidence.extend(normalized / f"{week['date']}_manifest.json" for week in weeks)
    promotion = {"label": "tdcc_history_three_weeks_20260906",
                 "evidence": [{"path": str(path), "sha256": digest(path)} for path in evidence], "files": files}
    atomic_json(destination / "tdcc_history_promotion_plan.json", promotion)
    rows = "\n".join(f"| {w['date']} | {w['planned']:,} | {w['validated']:,} | {w['official_no_data_confirmed_twice']} | {w['raw_rows']:,} |" for w in weeks)
    change_text = "\n".join(f"- {x['ticker']} / {x['date']} / {x['feature']}: {x['before']} → {x['after']}" for x in result["changes"]) or "全部比較格點逐值相同（NaN 與 NaN 視為相同）。"
    document = f"""# TDCC 三期歷史來源完成驗證

共完成 {report['planned_queries']:,} 個固定計畫查詢；{total_valid:,} 筆有完整官方分級，{len(absence)} 筆保留兩次精確查詢的官方無資料回應，未解抓取／解析錯誤 0。這是鄰週券碼聯集的完整走訪，不能宣稱已獨立證明當期全市場券碼清冊。

| 官方資料日 | 計畫券數 | 完整分級券數 | 兩次官方無資料 | canonical raw 列數 |
|---|---:|---:|---:|---:|
{rows}

有理由的複查找回 6 筆：四個先前 HTTP／錯日失敗，以及 20251009/2347、20251023/000815 兩個暫時假空。其餘無資料各自僅另確認一次，所有原始 attempt 保留，沒有循環重試或填零。無資料的法律／券種資格原因不能只由查無結果推定。

raw 正常 1–15 級、實際調整股份 16、實際合計 17 依欄意處理。省略的調整列不補成零；實際負調整與空白人數保留。已另以同一期官方 bulk 和真實公開 HTML 四券逐值核對正常級距與合計，canonical Retail_Pct / Whale_Pct / Total_Holders 完全一致；第16列來源格式差異如實保留。

本機解析效能改進另以 {backend_record['samples']} 個真實回應（包含三期均勻樣本及所有負調整）核對原雙次html.parser與單次lxml，完整rows及metadata逐值零差，18個focused fixtures通過。已完成第一週的manifest及source SHA驗證後resume，保留原輸出bytes；沒有增加網路請求或放寬資料檢查。

正式 `_summarize_tdcc` 處理全部新增 raw，保留原 {result['summary_before_rows']:,} 筆所有既有鍵值，新加 {result['summary_added_rows']:,} 筆後共 {result['summary_after_rows']:,} 筆。使用同一 2,031 檔日K最新實際日期（含停止更新股票各自日期與新84來源），完整 summary 前綴、相同 production feature bytes，比較 {result['compared_cells']:,} 個 TDCC 特徵格點；差異 {result['changed_cells']} 格。

{change_text}

這是資料及特徵對帳，沒有重訓、沒有改 rere 門檻，並非策略回測。最新有任何差異須另作 D 推論驗證；歷史回測特徵會受到補週影響，不能由最新零差推定歷史零差。此包尚未發布正式來源、未匯入正式 DB，也沒有動帳本。

公開歷史 bulk 查證：已檢查 [TDCC 公開查詢表單](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock)、[官方 OpenAPI 文件](https://openapi.tdcc.com.tw/tdcc-opendata-api-docs)、[政府資料集11452](https://data.gov.tw/dataset/11452)，未找到有文件支持的按日期全市場 bulk。既有 FinMind 授權 token 的歷史 bulk 實測 HTTP/API400、register層級權限不足；沒有購買或變更帳號。這不代表未公布端點已被窮盡排除。

較舊的缺觀測仍有35個含交易日週與1個只有結算日週，另4個全週休市為合法無觀測。詳見 tdcc_calendar_reconciliation.json；不得把43全部稱為缺資料，也不應把只修本次三週稱六年全補齊。

防重演：目前排程 fetch_tdcc_weekly.py 對空 API fail closed、每次重驗同週已存來源；舊 twstock Step9 曾在已存在 raw 時跳過、抓取失敗後仍重算舊摘要，此風險已交由同輪獨立修復。80%覆蓋只是最低殘檔界線，並非精確市場完整性證明。本次歷史 SOURCE_NO_DATA 的完成包另外強制兩次獨立 HTML/date/ticker/SHA 證據。

完整變動清單、每筆無資料證據、檔案SHA見 tdcc_history_completion.json；root可審核 tdcc_history_promotion_plan.json 的3個新raw與1個summary CAS計畫後發布。
"""
    (destination / "tdcc_history_completion.md").write_text(document, encoding="utf-8")
    print(json.dumps({"status": report["status"], "weeks": weeks, "changed_cells": result["changed_cells"],
                      "report": str(report_path), "plan": str(destination / "tdcc_history_promotion_plan.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
