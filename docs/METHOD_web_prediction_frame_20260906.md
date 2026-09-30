# 2026-09-06 Web scoring frame 修復與四分支驗證

## 已保存的正式失敗

`final_web_acceptance_20260906.json/md` 保留第一輪 FAILED，完整 HTTP 在 ignored `output/data_layer_consistency_20260906/final_web_http`。Daily health 10 項、同一 ingest generation 五表、CSV 清單及模型證據通過，但 explain 報酬為 2603=2.48%、3605=-0.91%、2330=4.66%，與 CSV 2.35%/2.75%/0.48% 不同。服務 PID 46920 由根任務停止，未以改低 assertion 隱藏差異。

## 根因與邊界

1. `_get_prediction_context` 對已驗證 canonical frame 再套「最新季度覆寫」，結尾又 winsorize 全表。Canonical 已具有本次 source lineage 與 point-in-time 特徵，這個舊修補層會改變模型輸入。
2. 既有 base 與 pinned V2 均要求 cross-sectional z-score。Producer 先對完整 snapshot 按 Date 處理，再取模型欄位；Web 原本把 raw frame 直接送進模型。三檔正式 HTTP 錯值主要由這項 processor 遺漏造成。
3. CSV 的 V2 最終報酬已經過原 ATR cap；SHAP 分解的是模型原始值。6949 的原始報酬 0.5093228977184205、正式值 0.5072547122836113 是既有風險限制的差異，不能算成輸入修復失敗，也不能把 SHAP 稱為 cap 後報酬的原始分解。

修復僅在 `app.py`：保留完整 canonical raw frame，移除自動季度覆寫；用原 `_model_needs_zscore`/`apply_snapshot_zscore` 在篩 ticker **之前**建立模型輸入；raw 財務事實與顯示值繼續使用原 raw frame，不能拿 z-score 判斷獲利或虧損。`model_pred_return_20d` 保留真模型計算值；`pred_return_20d` 使用同一已驗證 CSV 的最終值，`prediction_postprocessed`/說明區分既有風險調整。

Explain 同時驗證整份記憶體 scoring frame 與 certificate。先比原 bind SHA；若讀取 Series 或 pickle roundtrip 因 pandas 內部狀態造成 bytes 不同，僅能讀 **檔案 SHA 等於 certificate.snapshot.sha256** 的原始 canonical bytes，再以 exact values/dtypes/index/columns/flags/attrs 比較。這個 SHA 已直接綁定推論輸入，不依賴可省略的 `source_manifest`；缺少原始 bytes 或資料有差即拒絕，不補證、不洗 snapshot、不放寬數值。

`_build_live_prediction_df` 也使用相同模型 processor，但它仍是舊的簡化 fallback，沒有完整 ranker/postprocess 產物鏈，**本報告不聲稱它與完整 production pipeline 等價**。正式 explain 要求同 run certified CSV，不會把該 fallback 當成正式 CSV 的證明。

另將 `/api/entry/canonical` 接上 `scripts.entry_artifact_lineage.load_entry_artifact`。缺檔或 stale certificate 回 503/data_error，不把資料失敗回成合法空名單；共用 helper/generator/renderer 由平行子任務交付。

最新名單檔案搜尋排除 `.manifest.json` sidecar，避免原 `entry_list_*.json` glob 把排序較後的證書當作名單。API fixture 同時覆蓋 payload 與 sidecar 共存、只有 sidecar 而缺 payload、stale proof 三個情境。

## 同一 snapshot、四分支

實體隔離副本以同一 E canonical 1,098×302 frame、相同 base/V2 weights/meta，78 個財報來源逐檔 SHA 對照 certificate。模型與來源都未改寫。逐 ticker+date 比對三機率、prob_edge、V2 原始與正式報酬。

| 分支 | 季度覆寫 | 原 z-score | 與 CSV 不同的 prob_edge 筆數 | 與 CSV 不同的 V2 最終報酬筆數 |
|---|---:|---:|---:|---:|
| A：舊 Web | 有 | 無 | 1,098 | 1,098 |
| B：只去覆寫 | 無 | 無 | 1,098 | 1,098 |
| C：只补 processor | 有 | 有 | 5 | 8 |
| D：修後 | 無 | 有 | **0** | **0** |

D 使用實際修後 app processor；final V2 另經原 `_apply_recommendation_rules`，沒有重寫 ATR 或其他規則。D 的 base 三機率、prob_edge、V2 final 所有 1,098 筆皆 exact 0 差。

完整 302 欄盤點：覆寫層實際改變 **16 欄／4,729 cells**，包含 EPS/BS 的浮點精度差，以及 `vol_ratio_5_20`、`atr_pct`、`obv_slope_20`、`cmf_20`、`vol_zscore`、`force_index_13`、`turnover_rate` 七個非財务欄的二次 winsorize。不是只檢查 log 所列的 24 欄。

數值 closure 用 CSV `float_precision='round_trip'` 保留 writer 的浮點值。原生 parser 與 roundtrip 最大差低於 1e-16，單獨列在報告；HTTP 的百分比仍遵循原生 parser 與 `round(ratio*100, 2)`，不更改 API 讀取算法。

## 證據與驗收

- `ml/reports/research/data_layer_consistency_20260906/web_prediction_frame_ab_20260906.json`：四分支摘要、模型 SHA、全部變動欄、各指標差異及三檔精確值。
- 同目錄 `web_prediction_frame_deltas_20260906.csv`：1,098 筆 ticker/date 分支明細；`web_prediction_frame_three_tickers_20260906.csv`：三檔 raw feature 差異。
- Runner `web_frame_ab_v3_20260906` PASS，原始所有 frame/來源副本在 ignored `output/pipeline_acceptance_20260906/workspace/output/web_frame_ab_20260906`。
- 第一個 AB 未計入 ATR cap，保留其 6949 assertion FAIL receipt；v2/v3 使用原 postprocess 後嚴格零差。未把該失敗刪掉或改成通過。
- `web_prediction_frame_v1_tests` 68 PASS。v2 正常 heterogeneous save/bind/publish/load fixture 抓到兩個 pickle bytes false-reject（73 PASS、2 FAIL），原 receipt 保留；補上原 snapshot bytes + exact frame 比較後 **v3 75 PASS**，包含值、dtype、index、processing attrs 或參照檔 bytes 改變必須拒絕。
- 最新 `web_prediction_frame_v4_tests` **75 PASS**，同一 API fixture 再加入 payload/sidecar 共存與 sidecar-only 情境，驗證新的正式 sidecar 不會被當成名單。

正式 HTTP 第二輪必須另存 `final_web_acceptance_v2_20260906.json/md` 與 `final_web_http_v2`。除了 final return，三檔也比較 `model_pred_return_20d`；6949 另外驗 raw/final 與 postprocessed flag，避免只把 CSV 值填回畫面而掩蓋 processor 問題。

第二輪 HTTP 9 GET 已完成並保留 **FAILED**：首頁、10 checks、CSV 清單及 canonical run proof 全通過，但四檔 explain 因可省略的 `source_manifest=null` 被錯誤拒絕。真 `_load_pipeline_snapshot` 唯讀重現 1,098×302 frame：載入時 SHA=8091e4ac…，讀 ticker Series/row 後重序列化 SHA=8a14a1a5…，全值與 dtype/index/flags/attrs 完全相同。原始檔案 SHA 仍等於證書 8091e4ac…；修復改以這個直接 bytes binding 比對，不要求不存在的 nested manifest。`web_snapshot_guard_repro_v3` 保留重現證據；其 v2 setup 因隔離來源路徑載入不到正式快照而失敗也保留。

修後 `web_prediction_frame_v5_tests` **77 PASS**（有／無 source_manifest 正常 publish roundtrip 均覆蓋）；`web_snapshot_guard_repro_v4` 真 formal frame → ticker selection → guard **PASS**。精簡原始失敗／修後證據為 `web_snapshot_binding_exact_20260906.json`。第三輪 HTTP 使用全新 `final_web_acceptance_v3_20260906.json/md` 與 `final_web_http_v3`，不覆寫前兩次失敗。

本次沒有改 `ml/predict.py`、provenance helper、模型 pin/selection、源資料、帳本或任何策略門檻；不需因這個 Web 修復重跑既有 20D/DataA/T1 推論。
