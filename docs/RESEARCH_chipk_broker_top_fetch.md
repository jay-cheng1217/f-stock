# RESEARCH：籌碼K 分點(broker-top)自動抓取 — 診斷與封存

- **狀態**：BLOCKED（半成品，需逆向工程；低優先）
- **建檔**：2026-07-01
- **目標**：自動抓「分點券商買賣超 + 短沖主力標記」，補策略目前最大籌碼盲點
  （系統只有主力總數，抓不到「是誰在買、是不是短沖」——原相 3227 的
  「Top買方全是短沖主力」只能靠人工截圖）。

## 一句話結論

分點抓取的程式（`ml/chipk_app_api.py` fun_id=2）**從頭到尾沒成功過**，不是最近壞掉。
登入是活的、API 通、解密通、表頭正確，**但伺服器永遠只回表頭、0 資料列**。
根因是**桌面版 app 的真實請求格式沒被逆向出來**，猜參數無效。

## 目前已知（實測 2026-07-01）

| 環節 | 狀態 |
|---|---|
| 登入 session（讀桌面版 CMoney AppViewer EBWebView cookie，DPAPI 解密） | ✅ 全綠有效（device/cm_at/idp_session/cm_rt 皆 true，cm_at 未過期） |
| API 呼叫 `POST https://datasv.cmoney.tw/ChipKAdaptor/api/ChipK` | ✅ 200、`state=1`、有 queryNumber |
| 解密 base64→zip（pwd `Get{queryNumber}CMoney`）→cp950 CSV（`^` 分隔） | ✅ 成功 |
| 回傳表頭（正確的分點 schema） | ✅ `排名 / 分點代號 / 分點名稱 / 買張 / 賣張 / 買金額 / 賣金額 / 區間損益` |
| **實際資料列** | ❌ **全 0**（連台積電 2330、各日期/period 都 0） |
| 歷史紀錄 `ml/data/chipk/app_api/*.json`（06-18、06-22） | ❌ `total_rows=0`，**當初就沒抓到** |

## 根因線索

- 用 device 類身分 → 回表頭、0 列（敷衍）。
- 用**真 token `cm_at`** → 回「**參數錯誤**」→ 代表 **FunParams 格式不對**。
- 現行格式 `chipk_broker_top_params()` = `{ticker4},{yyyymmdd},{period},1`。

## 已試無效（不用再試）

- FunParams 變體：period ∈ {1,5,10,20}；尾碼 ∈ {1,15,0,2}；多加欄位；前置市場旗標。
  → 全部只回表頭、0 列。
- 8 種 guid/auth 配對全試過；只有 `cm_at`/`cm_idt` 會回「參數錯誤」，其餘回空表頭。

## 為什麼卡住

要知道正確 FunParams + AppId/token 配對，**必須攔截桌面版 app 載入分點頁時實際發出的封包**
（mitmproxy / Fiddler 對 AppViewer）。這是逆向工程，且踩 CMoney ToS 邊緣、脆弱。

## 選項與建議

1. **（建議·短期）繼續用截圖**：分點/短沖用人工籌碼K截圖，我來判讀 veto（已證明有效，
   見 [[feedback-entry-list-data-sources]]）。
2. **（建議·中期替代源）改用公開分點資料**：TWSE/TPEX 有公開「券商分公司買賣日報表」，
   是 CMoney 分點的同源原始資料 → **可自建分點/短沖特徵，不必逆向 CMoney、無 ToS 風險**。
   這條比逆向 app 更值得做。
3. **（低優先）逆向 app 封包**：側錄桌面版請求還原 FunParams。不確定能成 + ToS 風險。

## 相關檔案 / 注意

- `ml/chipk_app_api.py`（broker-top 抓取，**header-only 未通**，建議加註警語避免誤用）
- `scripts/probe_chipk_app_api.py`（probe，非 production）
- **正常運作的主力總數路徑**：`scripts/import_cmoney_chipk.py`（手動匯出匯入），
  `chipk_main_force_*.csv` 更新到 0630——這條是好的，別跟 broker-top 混淆。
- 策略目前**完全沒用到分點欄位**（`build_unified_signals` / `ml/predict` / features 皆無）。

## Skill 對應

- 主要：（RD research）逆向/替代源評估 → `data-pipeline-spec`（若走 TWSE/TPEX 公開源，
  設計 ingest）→ `code-review-checklist`。
- 安全：逆向 app 前先過 `security-threat-model` + 確認 ToS。


## 2026-07-08 更新:FinMind 分點資料集可解鎖(取代截圖工作流)

- 實測 `api.finmindtrade.com/api/v4/data?dataset=TaiwanStockTradingDailyReport`:
  資料集存在,回應 "Your level is free. Please update your user level"
  → **贊助會員(Sponsor)可拉全市場分點日報**,來源=交易所買賣日報表,由 FinMind 整理。
- 判決變更:分點 auto-fetch 從「blocked(驗證碼牆),靠人工截圖」改為
  「**FinMind sponsor token 即解鎖**」。自爬 bsr.twse.com.tw+OCR 驗證碼列為備援不採用(ToS 灰色)。
- 待用戶提供 token 後的落地順序:
  1. 南茂/原相/擎亞 三檔 vs 籌碼K App 截圖逐位對帳(口徑校準,同 TDCC 流程)
  2. DuckDB `branch_flow` 表 + 凌晨全市場 ingest(限流下約 1-2 小時)
  3. 衍生指標:主力成本(Top15 分點買超加權)、分點集中度、隔日沖分點識別(known 名單)
  4. dashboard 籌碼區 + 進場健檢接入
- Token 只放本機 `.env`,不進 git(隱私剝離清單同步)。
