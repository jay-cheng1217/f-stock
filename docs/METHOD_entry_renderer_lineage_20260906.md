# Entry renderer 來源憑證閉環方法鎖定

鎖定日期：2026-09-06。範圍是 L1 呈現讀者一致性修復；不更動型態 gate、rere 規則、模型、權重、門檻或版面。

現行 `scripts/entry_dashboard.py::build` 直接讀 entry JSON；`load_universe` 直接反序列化 snapshot 與讀取 DataA CSV；`load_model_tags` 直接讀 production CSV。日期標籤只能揭露日期差，無法拒絕同日期修源後的舊分數，也無法證明舊名單確由目前分數產生。獨立 renderer 排程因此可能繞過已加固的 canonical/P2 讀者。

唯一變因是讀取契約：使用 canonical plan 的 `load_entry_artifact`、snapshot 的 `load_snapshot`、prediction 的 `load_prediction_csv`。缺憑證、壞檔、來源或模型依賴改變，均明確失敗；不替舊產物補目前來源憑證，不退回 raw CSV/pickle，不把錯誤視為空名單。

建置前記錄來源集合與實際選取產物，建置結束重新驗證 plan、来源、prediction 依賴及產物位元組。中途改動即丟棄記憶體 HTML。既有 `OUT.write_text(build(path))` 的先計算後寫入順序保留，失敗不能覆蓋既有正式 HTML。讀者不訓練、不重算特徵、不寫帳本或 canonical、不寄信、不重啟服務。

`--date` 只是選取当前有效 canonical plan；歷史 plan 必須有真正 frozen source bundle 才能研究重現。此 renderer 仍讀 current raw sources，因此本次不提供歷史放寬模式，不以 latest sources 偽裝歷史 as-of。

驗收事前標準：

- 同日期 source 修正、缺 certificate、snapshot/CSV 位元組改動、舊 plan 配新 prediction 與建置中途來源改動，均拒絕並保留原 OUT。
- 正常憑證路径沿用原 pandas parser 與原數值處理；同一 fixture raw readers / certified readers 的完整 HTML 相等。
- 已驗正式視覺基準是 `ml/reports/entry_dashboard_latest.html`，SHA256 `2f42e26080846e6bf0691b1b1d49529e16b130025fca242214e99c8485c28113`，18 張卡、rere 6 張，資料 9/4、交易日 9/7。此階段不重建該檔；root 待新 certified canonical plan 完成後，才產正式 HTML 並複核 payload/卡片無變（若建置時間不同單獨列明）。
- 聚焦 fixture 為契約驗證，不宣稱新增模型績效或歷史回測證據。

此文件先於實作鎖定；實際結果另補驗收紀錄，不回寫通過標準。

## 實作後驗收紀錄

2026-09-06，`python -B -X utf8 -m pytest tests/test_entry_dashboard_lineage.py tests/test_entry_dashboard_cards.py tests/test_entry_dashboard_freshness.py -q`：**34 passed in 16.66s**。Fixture 使用臨時檔案的真正 snapshot、P2 與 canonical generation/publisher/loader，未替 production 產物補造證明。

已驗缺證、壞 snapshot、同日期來源修正、同日期成功重推但舊 plan、過期 production 檔、歷史 plan，以及建置途中來源/模型/新檔/展示資料改動；全部拒絕並保留既有 OUT。另測 production CSV 在大小與 mtime 不變時的內容替換，仍由消費檔案 SHA 檢查拒絕。canonical source 的大量檔案沿用既有 mtime_ns/size/集合快速驗證契約，沒有宣稱每次 renderer 都重算所有原始資料的 GB 級 SHA。

正常 fixture：保留 DataA 原 `pd.read_csv` 預設與 production 原 `low_memory=False`，明確覆寫 certified loader 的 ticker dtype 預設回原 parser；`check_exact=True` frame 比對通過。固定畫面產生時間後，完整 HTML 與原 raw readers 逐字相同。

額外只讀核對現行正式檔的三個 certified readers：snapshot **1098×302**、DataA **1098×4**、production **1098×202**；均接受有效憑證且與原 `pd.read_pickle` / `pd.read_csv` 解析結果逐值完全相同。本步驟未呼叫 `build` 或 `main`，不寫正式 HTML。

與實作前 HEAD 的 AST 核對：既有函式僅 `load_universe`、`load_model_tags`、`build`、`main` 改動，其他既有函式（含卡片/rere/evidence 判斷）全部相同；`HTML` 的完整 CSS/JS/版面常數相同。正式 HTML 仍為鎖定的 `2f42e26080846e6bf0691b1b1d49529e16b130025fca242214e99c8485c28113`。待 root 產新 certified plan 後，最後正式 HTML 對帳與發佈由 root 協調；此紀錄不宣稱新 Artifact 已上線。

## Web 實際入口閉環補充鎖定

獨立 review 確認 `frontend/index.html` 的 canonical 模組未檢查 API HTTP 狀態；遇到 503 `data_error` 或合法空 `rows`，會改讀 `/static/entry_canonical.json`，讓已由 API 拒絕的舊名單重新上畫面。root 授權最小修復：移除這個未驗證 static fallback，檢查 HTTP/payload 狀態與 rows 型別；驗證失敗清除名單並呈現明確錯誤，合法空名單保留原空名單提示，正常列內容/排序/樣式不變。

事前驗收：執行實際前端 JavaScript 模組，以 API 503、200 data_error、合法空列、正常 18 列與格式錯誤回應驗證。任何情況不得再請求 static canonical；失敗不留舊 rows，正常 18 列維持順序與原欄位。此項不動 renderer 範本/rere 規則/正式資料。

前端結果：`tests/test_entry_frontend_lineage.py` **7 passed in 0.50s**。直接抽出實際 HTML 的 canonical JavaScript 模組，以 Node VM 執行，僅 HTTP/DOM IO 使用可控替身；涵蓋 HTTP 503、200 data_error、錯誤 rows 型別、壞 JSON、網路錯誤、合法空列、正常 18 列/rere 6。全部沒有 static canonical 請求，錯誤清除舊列且文字跳脫，空列保留目前日期，正常列順序及原欄值不變。本項是實際 JavaScript 執行 fixture，沒有冒稱跨瀏覽器實測。

唯讀交叉 review 另確認原 canonical 允許同日 DataA 缺席時使用 production。嚴格 renderer 初版只准 DataA 的新防護會誤擋這個合法來源；經 root 明確授權改成依 plan `model_source` 精確名稱辨識 slot，驗證對應憑證與相同日期，保留原泛稱「20D模型」的顯示鍵。不回退其他日期或其他檔。真實臨時憑證 fixture 已證 production fallback 能完整 render；把 DataA 憑證移到 production 名稱則拒絕。

最終範圍測試：`python -B -X utf8 -m pytest tests/test_entry_dashboard_lineage.py tests/test_entry_dashboard_cards.py tests/test_entry_dashboard_freshness.py tests/test_entry_frontend_lineage.py -q`，**43 passed in 15.46s**。既有 DataA fixture 的原解析結果與完整 HTML 仍完全相同。renderer 最新檔選擇器使用完整日期解析，與 sidecar 共存时仍選 `entry_list_20260907.json`；CLI 已收盤日/次交易日政策與原 `main` 一致。所有正式建置與通知仍由 root 統一執行。
