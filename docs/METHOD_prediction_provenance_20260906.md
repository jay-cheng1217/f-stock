# 2026-09-06 實際預測成功證據

## 問題與邊界

Canonical snapshot 是模型計算前的資料產物。其成功更新不代表當日模型推論或 CSV 發布成功；原先 API 直接讀同日 `predictions_*.csv`，只核 ticker/date/close 的稽核也無法證明分數來自新來源。本修復在 L2/L3 交界補上實際成功推論的證據，與既有 snapshot manifest 分開。

不變更模型選擇規則、特徵計算公式、推薦門檻、rere 規則、權重或出場。V2/alpha/ranker 的路徑解析只抽成與原 loader 共用的函式；環境旗標、明確路徑優先及最後排序檔名的行為保留。長駐程序開始新推論時重新讀取 sector mapping 的來源 IO，避免輸出使用舊 module memo。

## 實際流程

1. 在模型及快照正式載入前，`capture_run` 記錄 canonical 全來源、prediction code/附加輸入、實際 metadata/weights 路徑及 SHA、slot 與允許列出的推論環境設定。只收 ChipK 的兩個已知市場資料檔，不收認證或 token。
2. 實際 loader 載入既有選定模型後，確認模型路徑與捕獲證據相同；來源或 artifact 改變即拒絕。
3. `bind_snapshot` 綁定本次實際使用的記憶體 pre-model frame SHA/列數及可選 canonical manifest。後续模型轉換與運算不變，不能把同一 run 改綁其他 frame。
4. `run_prediction` 只有在整段推論成功返回後，才以 `publish_prediction_csv` 原子替換 CSV 及其 `.manifest.json`。CSV 維持原本 `index=False, encoding='utf-8-sig'`；證書綁定完成時間、輸出 bytes SHA/列數。兩檔替換間中斷會留下不能通過 SHA 驗證的配對，不會誤認成功。
5. API 每次走 CSV 路徑都驗證證書，不能以同 feature key 的 `pred_df` 記憶體命中略過；來源、模型、檔案集合、CSV 或 slot 過期皆拒絕。讀取模型 metadata 後再檢查一次 run，涵蓋驗證與模型載入間的變更。無可信 CSV 時沿用既有即時運算路徑，不替舊 CSV 補證。

共用介面供 DataA producer/canonical reader/正式 audit 使用，DataA 的實作與驗證另見 `METHOD_dataa_prediction_provenance_20260906.md`。DataA slot 為 `dataA`，20D 正式 slot 為 `production`。Reader 支援原本 `read_csv_kwargs`，API 使用原生 float parser；audit 可保留 `float_precision='round_trip'`。

## 驗證

全部測試在實體隔離副本，經 `scripts/pipeline_acceptance.py run` 與 acceptance guard 執行，僅使用 temp fixtures。沒有執行正式模型、重訓、正式 snapshot/source/DB/帳本寫入。

- `prediction_provenance_v3_tests`：2026-09-06 22:39:30，48 PASS。新 provenance 27 項 + canonical lineage/P1 cache 既有 21 項。
- 成功 CSV 與原 `to_csv` 輸出逐 byte 相同；可選 reader float parser 保留。
- 缺證書、同日來源變更、model 相同 size/mtime 但內容變更、metadata 變更、CSV 變更、錯 slot、缺 weights 證據均拒絕。
- source 在序列化途中變更時保留原 CSV/manifest bytes；兩檔交換中斷時讀取失敗封鎖。
- 真正 `run_prediction` 函式的推論失敗無新 CSV/證書；成功只發布本次 `predict_all` 回傳的 run。
- 真正 `predict_all` 建構前段驗證 model load/snapshot build 途中來源變更會失敗，穩定來源綁定的 frame SHA 相同。
- V2/alpha/ranker 的 10 組 fixture 涵蓋無檔、停用、自動最新及明確 override，保留原選擇規則與 ranker 整數設定。
- API 已有舊 `pred_df`，但 CSV 證書不可信時，必須清掉該記憶體值並要求新 context。

原始 receipt：`output/pipeline_acceptance_20260906/prediction_provenance_v3_tests.json` / `.log`。前一個 v1 runner 因自檢將 guard object 誤當 dict，在任何測試前退出；v2 改正為 guard.root 後 14 PASS，再增加契約測試為 v3 的 48 PASS。保留原失敗 receipt，不歸因於 production 功能。

## 證據的實際限制

發布時完整來源 SHA 會保存；常態來源重用檢查採與 canonical snapshot 一致的完整 file-set、size 與 ns mtime，避免每次 API 重讀數 GB。模型 metadata/weights 及 CSV 另外逐次核 SHA。故本機人工蓄意修改 raw source 並還原原 size/mtime，不屬這個快速重用契約能偵測的情形；正式 source promotion 必須保留既有更新 mtime/原子替換契約。

這是成功推論證據與各層一致性驗證，不是策略回測，也不替代根任務接下來的正式推論與完整跨層驗收。T1/unified 仍依本輪另列的操作依賴 hash receipt 驗收。
