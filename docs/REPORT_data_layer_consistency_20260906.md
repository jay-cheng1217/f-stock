# 資料補齊與跨層一致性驗收（2026-09-06）

**本輪資料修復與跨層驗收完成（2026-09-07 00:09）：來源、資料庫、20D／DataA／T1、名單、HTML 與實際 Web 均通過。固定 Claude Artifact 尚未重發。** 本輪沿用正式模型及 rere KOL 方法，沒有重訓、調整 pin、策略門檻、權重、資金或出場規則。固定截面 A/B 量化資料修復影響，不是歷史獲利回測。

## 正式資料修復

| 項目 | 結果與邊界 |
|---|---|
| 外資持股 | 四日官方雙市場資料增加 7,222 列；1,618 檔、29,557 格 parser 修復，其中 21,252 格真零、8,305 格正持股但官方比例 0.00。未知沒有全面補零。最新 1,901 × 14 特徵零差。 |
| 公司來源 | 補 84 家普通公司、16,024 筆官方日 K 及相關來源，39 筆產業對應。原 Step7 classic 指標與 ML 技術特徵 2,083,120 格一致。4 個 TDR 另列；3718 不冒充 5371 舊歷史；合法較舊成交日不補假棒。 |
| 融資 | 2026-03-20 官方 2,142 券回補。歷史 1,622 日／3,244 份原始資料重對，修復前導零丟失造成 ETF 與股票碰撞；總表 3,025,294 列，34 個股檔 53,099 格修正。8 家普通股票最新 46 特徵零差；22 家 ETF 最新有真實變動且不在目前模型 universe。 |
| Q2 財務／EPS | 財務 1,825→1,937、EPS 1,832→1,974 列；核對指定期別、官方內容及合法保留券。 |
| 資產負債 | 26 季、48,498 列均有官方三大總額，資產＝負債＋權益違規 0、未驗證保留列 0。依欄名及表頭跨度解析。原 1,076 列已知違規、1,398 列未知分開處理。BVPS 只採官方批次值。 |
| 估值 | 修復 2021-05-10 指定日；另 21 組、57 日期家族對照雙市場官方原始回應，正式更新 37 日、90,070 數值格、增加 1,415／移除 508 鍵。20 個僅名稱差異日期保留歷史名稱。最新 2,031 × 5 特徵零差。 |
| TDCC 三週 | 2025-10-09、10-23、2026-02-26 共 11,659 計畫查詢，11,609 券週成功、50 次兩次確認官方無資料、未解決錯誤 0。彙總 899,102→910,711 列，所有原有鍵值不變；保留官方級距及實際調整列。 |
| TDCC 雷達 | 重建 8/28→9/4 raw→snapshot／delta／status，2,960 四碼券配對、14 個符合原條件的具名訊號；新出現或消失券不捏造週差。 |

八份來源 promotion 及 DB 收據見 `ml/reports/research/data_layer_consistency_20260906/source_and_db_delivery.json`；外資證據另由同目錄來源報告追溯。每次 promotion 保留計畫 hash、逐檔 SHA、替換前備份及原子發布收據。失敗與重試原始紀錄保留在本機 output，沒有覆寫成只留成功。

TDCC 原 43 未觀測週中，4 個為整週合法休市、3 個本輪恢復；仍有 35 個較舊交易週及 1 個僅交割週未知。現有 FinMind register 權限無法取得完整舊資料；50 個官方無資料回應也不能推定為完整市場母體證明。缺口保留 unknown。

## 一致性與正式推論

正式 ingest 於 22:07 完成，五表共用世代 `313405adc97749b5aab935e979ef617c`。22:08 的 13 項 DB 驗收全部通過：2,031 個來源／stock_list 身分唯一，日 K 2,990,060 列、重複 0、逐股最新 OHLCV 差異 0；revenue 61,854、financials 47,331、TDCC 910,711、indices 10,037。1,994 券最新 9/4，37 券保留自己的較舊日期。

來源橋接驗證 7,426 個檔案，所有變更有 promotion 或逐值證據，未解釋變更 0。22:23 正式 snapshot 為 1,098 × 302，與 A/B 的 E 快照逐值、索引及 dtype 相同。

22:44 套用既有夜間 Champion pin 的正式 20D 推論完成：1,098 檔、全部共同數值欄與 E 完全一致、推薦差異 0；CSV SHA `850b627d22431b48ad8fab04b2179df426d141cc929cd42a351fa83f882c4624`，逐 byte 相同。這是實際正式推論，沒有複製研究分數；snapshot 與橋接 manifest bytes 未變。

第一次獨立指令漏套夜間 defaults，走原 loader 的最新 V2 fallback，365 筆推薦差異被比對攔下。該次 CSV／憑證與 FAILED 收據另外備份，未上線。套用現有 defaults 第二次零差通過，沒有改 pin 迎合結果。收據為 `output/data_layer_consistency_20260906/formal_20d_inference_pinned_20260906.json`。同型 Web 獨立啟動缺口亦列入修復驗收。

## 流程防護

1. 必要來源／匯入失敗到達 failed ledger／非零 exit，衍生預測及帳本依賴失敗即阻擋。
2. 財報、營收與 TDCC 驗內容、雙市場、期別及完整性；TDCC Step9 統一使用已驗證的 refresh。
3. 逐表交易中任一必要表或 stock_list 失敗使整次 ingest 失敗；health 核對真實列數、身分與共同世代。
4. Snapshot 與預測各有獨立證據；20D／DataA 必須由成功推論發布 CSV／憑證，不能替舊分數補證。
5. API 記憶體快取不可跨來源轉換沿用；同時重建與失敗不會留下新 key 配舊 frame。Canonical／audit 明確拒絕無可信證據的分數。
6. 根任務合跑 220 項相關測試通過；最後 Web binding 修正另跑 77 項相關測試通過（與前者重疊，不相加）；另有 2 項 tracker sidecar 暫存檔測試通過，以及來源 parser、跨層及 A/B 收據。完整 generate、rere gate、型態與 DataA scoring 核心函式 AST 不變。

新增名單自己的成功生成憑證，綁定實際選用分數、snapshot、原始來源及額外依賴；產生器只執行一次，正常 watchlist 維護保留前後寫入證據。本次有界重建不維護 watchlist。Renderer 及 API 拒絕無證／過期名單；前端不可在 API 拒絕後退回舊 static 名單。合法空名單與資料失敗分開呈現。

憑證常態重用核完整檔案集合、size、ns mtime，發布保存全 SHA，模型與 CSV 讀取另核 SHA。人工故意改 raw 並還原 size／mtime 超出快速重用契約；來源發布須沿用原子替換與 mtime 契約。

## 模型理解與後續

| 固定截面比較 | 結果 |
|---|---|
| A→B 補公司 | 合格 universe 1,080→1,098；原有股票 193 筆推薦變更，prob_edge 最大 10.60pp。全市場標準化使既有股票亦受影響。 |
| B→C Q2 | 479 筆推薦變更，prob_edge 最大 26.60pp。原銀行欄位錯置令 BVPS 最大 127,886,472，修正後 1,464.41，曾扭曲整個截面標準化。 |
| C→E 歷史 BS | 12 筆推薦變更，prob_edge 最大 1.30pp；各步數量不能相加為唯一受影響股票數。 |
| F／D／G | 最終保留 BS、三週 TDCC、估值修復對 E 合格股票相應特徵零差；未入選股票的真實修正仍完整揭露。 |
| Canonical | E 與正式名單均為 18 張卡，rere 六檔 3605、3189、3066、2364、3176、6472 在各研究階段及最後正式生成相同。 |

**一致性改善不代表主模型校準完善。** 財務修復對預測影響很大，不應沿用舊資料績效，宣稱新資料仍有相同勝率。下一階段應以修正資料、固定模型及交易成本，驗證嚴格時間切分與 point-in-time 可得性；重訓或改 model selection 仍需 PM 核可。rere 原方法獨立驗證，不因主模型負分或營益率缺值新增 veto。

Sector strength 已按原 producer 重建 33 產業，bytes 與 B 比較來源一致。既有計算使用各股票 raw Close 最近／第 21 筆，檔案無共同 as-of 欄位，也不是公司行動還原績效；此限制明列，未偷偷改策略定義。

## 下游與 Web 驗證

既有下游實際執行完成：T1 為 1,094 個最新日期樣本，4 個較舊公司日期樣本不混入 T1；DataA 的 1,098 筆分數與營益率和 E 相同；原夜間 Champion unified 設定為 1 檔、gate OPEN，T1 不混入此策略。Canonical 的 18 檔順序、型態、區間、停損、分數及 rere 六檔與 E 相同。未執行任何帳本 record/check/sync。

第一個 HTML CLI 執行缺少 repo import path 而失敗，尚未覆寫舊 HTML；修復直接 CLI 啟動後，以獨立 resume receipt 接續 HTML 與唯讀 audit，22 項 outputs check 全通過。原 run 的 FAILED 保留。桌面 1440×1000、手機 390×844 的真 Chromium 驗收亦通過：18 卡、6 rere、來源 9/4／計畫 9/7、無頁面水平溢出、無 JS／console error，詳情展開正常。取消 skip 卡整張半透明，風險文字保持可讀；未改卡片判斷。

第一輪真 HTTP 驗收發現 explain 與正式 CSV 不同，服務立即停止。根因有二：已驗證的 canonical frame 又被套最新季度覆寫及二次 winsorize；Web 漏用模型 metadata 要求的完整截面 z-score。四分支實測保留原始 frame、模型及 source SHA：只去覆寫或只補 processor 都不足；完整修復後 1,098 檔的三類機率、prob_edge、經原風險規則處理的最終報酬均 exact 0 差。覆寫實際改變 16 欄／4,729 格，其中 7 欄為非財務特徵。

Explain 的 raw 財務事實與 SHAP 模型輸入分開；原始模型估計另列 `model_pred_return_20d`。6949 原始 50.9323% 與正式 50.7255% 的差異來自既有 ATR cap，不能把 SHAP 說成 cap 後值的精確分解。新欄位與說明揭露這點。舊簡化 live fallback 尚無完整 ranker／postprocess 鏈，本輪不聲稱它等同正式 pipeline；正式清單及 explain 要求同 run 成功推論證據。

第二輪 HTTP 的清單、10 項 health、canonical payload 與前端 SHA 通過，explain 則被新的快照檢查誤擋。實測只是取 ticker Series／row 後，pandas 的再序列化 bytes 會改變，而所有數值、dtype、index、flags、attrs 均相同。正式證書的 bound SHA 與磁碟原始 snapshot bytes 完全一致，但額外 `source_manifest` 為 null；不能把此欄位當成原始證據的必要條件。修復以證書直接綁定的原始 bytes SHA 核對參照檔，再逐值精確比較 frame；沒有替分數補證或放寬數值。第二輪錯誤回應沒有顯示錯值，FAILED 收據保留，第三輪另外驗收。

23:58 的新正式名單／HTML run 全 PASS：包含新增 `entry_plan_lineage` 的 **23 項** outputs check 通過，原模型產物與來源未變。新 run ID `6fd8edfce9f7400581242a7436714d6e`，18 檔／rere 6 檔相對 E 的順序及語意差異皆 0。新 HTML 相對 23:04 版本只有 `meta.generated` 時間變動，其餘 payload 與整份版型相同。

**最終 HTTP v4：PASS，9 GET／51 項比對／0 failures。** 00:08:01–00:08:20 在 PID 15672 實測：首頁 bytes 與目前前端相同，無 static canonical fallback；daily health 10 項、五表 ingest 世代、正式清單、四檔 explain 的原始模型值／最終值／推薦及 canonical 全 payload 都通過。20 個 before/after 檔案與 explain disk cache 未變。四檔 explain 2.55–3.92 秒；第三輪冷啟動首檔曾需 28.90 秒，尚不能宣稱載入效能已完善。第三輪其實全部 51 項比對通過，但驗收腳本複製檔名時誤把 `is_v2` 改成 `is_v3`，保留 FAILED 與原 HTTP，第四輪只修回既有欄位契約重驗；沒有再改服務迎合檢查。

相關詳細證據：`docs/METHOD_web_prediction_frame_20260906.md`、`docs/METHOD_entry_artifact_lineage_20260906.md`、`docs/METHOD_entry_renderer_lineage_20260906.md`。新名單憑證與每輪 HTTP 使用獨立收據，失敗不覆寫。

## 保護範圍與部署

基準為 14:40:45、HEAD `b74d7cc9` 的 74 檔 SHA。最終 00:09 稽核的 66 個模型／pin／selection／registry／帳本均與基準相同，原有 dirty 帳本未變，5 個核心策略函式 AST 相同。其他是授權衍生產物與 canonical reader。證據為 `ml/reports/research/data_layer_consistency_20260906/protected_artifact_final_acceptance_20260906.json`。

[目前 Web](https://humanities-interests-kerry-routes.trycloudflare.com) 已恢復，服務 PID **15672**；00:06 外部 HTTP 200、前端 SHA 完全一致、no-store，00:08 本機 API 全通過。重新載入請按 **Ctrl+F5**。手機／桌面 HTML 再驗通過，最終 HTML SHA `c951a38444bd5ac1ee24e1c2863528787faa27cb258d2b6d7a9102d4089787a9`。

[固定 Artifact](https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b) **仍待原網址重發**。產生本機 HTML 或推送私人 GitHub 鏡像不會更新該靜態 Artifact，本輪也未推送鏡像。現有工具無原網址 redeploy 能力。外部 Claude 的 `daily-entry-dashboard-republish` 排程 skill 仍有 renderer 失敗後沿用舊 HTML 的路徑，本輪未修改該外部排程；不得據此宣稱日後每天都會發布最新結果。重發須使用本輪已驗收檔案與原 Artifact URL。

UTF-8 更新通知於 00:10 寄送成功，預覽／回執在 `logs/data_integrity_completion_20260907_001004.html` 與 `output/data_layer_consistency_20260906/completion_email_20260907_001004.json`。信中明列固定 Artifact 尚未更新及模型校準／TDCC 缺口，不把本輪一致性驗證當作策略績效證明。

總驗收索引為 `ml/reports/research/data_layer_consistency_20260906/final_data_layer_delivery_20260906.json/md`；操作序列見 `docs/RUNBOOK_formal_post_ingest_outputs_20260906.md`。下表列出本輪至產物交付的全部 commit／分類；最終文件 commit 另於交付訊息報告。前輪至基準紀錄見 `docs/REPORT_pipeline_acceptance_20260906.md`。

## 本輪完整提交分類

| Commit | 訊息 | 白名單／分類 |
|---|---|---|
| 8ed1a5e9 | docs(data): lock data consistency repair scope and acceptance | 方法鎖定／研究治理 |
| 77765548 | fix(pipeline): block derived output after source failures | pipeline 失敗傳遞 |
| d9d2685c | fix(data): validate fundamental content and reporting periods | 財務來源內容與期別驗證 |
| 9b4efabc | fix(data): synchronize TDCC raw weeks radar and consumers | TDCC 來源與雷達一致性 |
| 46a4dc0c | fix(pipeline): enforce artifact dependencies before publication | 發布依賴與失敗封鎖 |
| d5a879f4 | fix(health): distinguish missing bars from official trading stops | 官方交易狀態／health |
| 5efe9dc6 | docs(data): lock missing-company source repair comparison | 公司來源 A/B 方法 |
| 354a33e6 | fix(ingest): expose partial imports and enforce stock identity consistency | ingest／stock_list／health |
| 33cb5a9a | fix(data): discover listed companies and restore observed foreign ownership values | 公司探索／外資 parser／來源證據 |
| 877fec07 | chore(data): add verified source promotion with immutable backups | 來源 promotion／不可變備份 |
| a4606564 | docs(data): record repair progress and complete change categories | 中途狀態與完整分類 |
| 8a96ca39 | fix(data): preserve margin security codes and verify historical source repair | 融資代碼 parser／官方修復 |
| 270faa8d | docs(data): lock quarterly source reconciliation and model comparison | 季度來源方法鎖定 |
| ed8293e6 | chore(data): allow verified quarterly source promotion with backups | 季度來源 promotion 範圍 |
| 2505f25a | fix(data): refresh quarterly filings and map balance-sheet fields by name | 季報 refresh／BS 欄名解析 |
| 53e8681d | fix(data): validate valuation response dates and official market calendars | 估值指定日／市場行事曆 |
| 9de5079a | fix(data): reconcile historical margin identifiers and preserve source evidence | 歷史融資來源對帳與修復證據 |
| 95b91a8b | fix(cache): require current source lineage for prediction snapshots | canonical snapshot 來源憑證 |
| 34e87c2a | fix(cache): include feature availability and verify live cache boundaries | 快照 feature availability／API 邊界 |
| 9f1e03da | fix(data): delegate TDCC step to validated weekly refresh | TDCC Step9 使用既有驗證 producer |
| e7687715 | chore(data): verify historical balance sheets and retained filings | 歷史 BS／保留券官方研究證據 |
| 20807985 | fix(data): reconcile wrong-day valuation source families | 估值錯日期來源家族修復 |
| 6a071d84 | test(data): add strict formal layer consistency acceptance | 唯讀跨層嚴格驗收工具 |
| 5670e0dc | docs(strategy): record source repair sensitivity and validation limits | 主策略資料敏感度與限制文件 |
| 833dc897 | chore(data): reconcile TDCC historical weeks with source evidence | TDCC 三週官方研究證據 |
| 3462694f | fix(audit): recognize DataA source date without masking conflicts | audit 日期語意修復 |
| 9e9eb0aa | chore(data): publish verified company sources and record database acceptance | 正式公司 metadata／來源與 DB 收據 |
| 97ea609f | docs(pipeline): document bounded post-ingest output rebuild | 有界下游重建 runbook |
| 880c88d7 | fix(cache): prevent stale snapshots during source transitions | API 併發／失敗快取修復 |
| e822a9b7 | chore(data): verify source sensitivity and publish proven snapshot lineage | 隔離 A/B／正式來源橋接／snapshot 證據 |
| 30b12971 | fix(pipeline): certify completed predictions and reject stale scores | 成功推論憑證／API 與 canonical 過期拒讀 |
| a445e411 | fix(web): align startup and explanations with certified production models | Web 啟動沿用既有 Champion pin／模型配對 |
| 52ba9b15 | fix(dashboard): publish verified sources and keep risk cards readable | 衍生來源／HTML／CLI 修復及階段驗收；不代表固定 Artifact 已發布 |
| 719b7112 | fix(pipeline): bind canonical plans to successful generation inputs | 單次名單生成憑證／原子發布／嚴格 audit |
| 7f45bd6d | fix(web): match certified scoring frames and production preprocessing | Web 原始快照／正式 processor／SHAP 與最終值區分 |
| 94a99a17 | fix(dashboard): require certified plans across renderer and frontend | renderer／實際前端讀取一致性及錯誤呈現 |
| b45b932f | fix(pipeline): resolve certified entry dates with the live calendar | 新憑證 wrapper 預設日期相容既有 CLI；原策略 AST 不變 |
| adc30aa0 | fix(tracker): exclude entry certificate sidecars from backfill | 新 sidecar 檔名相容；record/check 交易規則不變 |
| 1021f628 | fix(web): validate semantic snapshots against original bound bytes | Web 原始 bytes 憑證與語意等價快照相容／保留第二輪失敗證據 |
| 18947af5 | chore(data): record certified publication and final live acceptance | 正式 canonical／HTML 產物、UI／HTTP／模型帳本保護最終證據 |
