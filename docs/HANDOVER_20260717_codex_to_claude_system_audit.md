# 交接文件：Codex → Claude 全系統審查續跑（2026-07-17）

**對象**：Claude / 下一個 Codex session（SA + RD）  
**上游交接**：`docs/HANDOVER_20260716_system_audit.md`  
**狀態**：P0-C 與重訓前置三票已關閉；KOL stale publish 與 entry plan/model 日期錯配已修；重訓仍待 PM 授權；P0-A / P0-B 仍須 method lock + 同快照 A/B。
**最後更新**：2026-07-17

接手前必讀 `AGENTS.md`、`skills/git-commit-after-change/SKILL.md`，再依任務讀專用 skill。本文件是 2026-07-16 交接的增量續篇；遇到狀態衝突時，以本文件較新的驗證結果為準。

## 0. 一頁狀態

| 區塊 | 狀態 | 接手動作 |
|---|---|---|
| P0-C EPS / valuation / margin / quarterly fundamentals | **CLOSED** | 不需再重寫；保留 fixture 與 fail-closed 行為 |
| P0-C 舊日 K 讀取失敗覆蓋歷史 | **DISPROVED** | 現行程式已保留舊歷史；回歸測試已鎖定 |
| KOL 每日排程失敗後仍發布舊頁 | **FIXED，待下一次 07:00 實戰觀察** | 看 `logs/kol_tracker.log` 與 Task Scheduler Last Result |
| Entry plan 舊模型 / 新標籤混用 | **FIXED，待下一次 Phase-2 實戰觀察** | 驗 `model_source_date`、freshness banner 與遠端 entry dashboard 更新 |
| P0-A exit policy 除權息一致性 | **HOLD** | 先 method lock + fixed-snapshot A/B，未核可前不改 production |
| P0-B `ml/predict.py` guardrails | **HOLD** | 先 method lock + fixed-snapshot A/B，未核可前不改 production |
| P1 entry dashboard freshness / latest-glob | **CLOSED** | 保留 exact-date selection、缺資料明示與來源日期 banner |
| P1 industry train/serve mapping、KOL timezone/XSS | **OPEN** | 逐項對抗驗證，不可從代理候選直接判 bug |
| P1/P2 歷史資料缺口 | **OPEN** | 依本文件 §6 排序 |
| RETRAIN-01 / 02 / 03 前置票 | **CLOSED** | 報 PM 驗收；不得把全綠等同自動重訓 |
| 模型重訓 / model selection | **NOT AUTHORIZED** | 三票雖全綠，仍等 PM 明確核可 |

## 1. 本輪完成 commit

| Commit | 分類 | 關閉內容 |
|---|---|---|
| `2ee0843e` | production data pipeline / EPS | 回補時保留完整季度；申報截止日加寬限；雙市場與 80% replacement gate；原子寫入 |
| `3bacb5a9` | production data pipeline / valuation | 單一市場 partial response 不再覆蓋整日估值；雙市場與最低列數檢查；失敗回傳非零 |
| `83df27ec` | production data pipeline / margin | `twstock.py` 融資刷新要求 TWSE + TPEx 完整來源；歷史補洞會重新合併；原子輸出 |
| `09bfa9d3` | production data pipeline regression repair | 修正前一 commit 插入 helper 時誤傷 `_detect_margin_last_date` 的控制流；新增回歸測試 |
| `093296fc` | production data pipeline / fundamentals | Step 8 與 balance sheet 回補改為雙市場、期別、80% 與 atomic replacement；鎖定日 K 歷史保留 |
| `fd05ea00` | scheduler / publication reliability | KOL 四步驟 fail-fast、拒絕 stale HTML、原子發布、只提交兩份 dashboard、真實 exit code 回傳排程 |
| `60508f1a` | production-adjacent pipeline / dashboard freshness | entry plan 鎖 snapshot exact date；Phase-2 產生並發布；KOL 07:00 不再發布 entry；拒絕 stale fallback；dashboard 顯示來源新鮮度 |

本輪未修改：`ml/models/lgbm*`、model selection、registry、pin manifest、training resource、production threshold、order gate 或 DB schema。

## 1.1 重訓前置三票（2026-07-17）

| Ticket / Commit | 分類 | 結果 |
|---|---|---|
| RETRAIN-01 / `7f849bc5` | training-label integrity | 個股與官方 TAIEX 報酬指數雙邊同口徑；label/trade 視窗分離；配股用比例因子；修復後受影響 7.72%、平均 +3.66pp，無事件逐筆不變 |
| RETRAIN-02 / `6822f9f3` | feature integrity | TDCC 完整週格點 + NaN；gap 不再跨週 diff/rolling；連續週數遇缺口重置；無缺口前綴逐筆不變 |
| RETRAIN-03 / 本交接同一 commit | production data / feature integrity | 官方 TWSE/TPEx OHLCV 重建、雙口徑公司行動因子、長停牌分段、日常官方 bar verifier 與公司行動排程接線 |

RETRAIN-03 備份：`F:\stock_backups\日K資料_20260717_RETRAIN03`。正式稽核：
`ml/reports/retrain03_price_integrity_20260717.md`。結果為 2,002 檔、3,025,328 source rows，
2020 年後 OHLC / 非正價格 / duplicate 違規均 0，公司行動日假跳幅 0，新未解釋跳幅 0；
四個工單點名斷點皆 resolved 或因官方 regular tape 不存在而移除。DuckDB ingest 後為
2,904,922 rows / 1,947 tickers，Web 核心端點全數 200。全套 `483 passed`，財報稽核全綠。

唯一下一步：**回 PM 驗收並等待明確 retrain unlock**。在 PM 核可前，任何 agent 都不得執行
weekly retrain、改 `model_selection.json`、升版模型或改 protected artifacts。

## 2. P0-C 關閉證據

### 2.1 EPS

`scripts/backfill_eps.py` 現在：

- 在法定申報截止日後保留 3 天寬限。
- 舊檔 partial 時不以「檔案存在」視為完成。
- TWSE / TPEx 任一來源失敗即拒絕 replacement。
- 新季度列數低於前季 80% 時保留舊資料並明確失敗。
- 以暫存檔 + `os.replace` 原子落地。

### 2.2 Valuation

`scripts/backfill_valuation.py` 現在：

- 明確追蹤兩市場結果，不把空/partial response 當成功。
- 合併結果低於 1,500 列時不覆蓋正式檔。
- partial source 會回傳非零，讓排程可見。
- 寫入採 atomic replacement。

### 2.3 Margin

正式單一真相是 `twstock.py --step 3` 抓取、`twstock.py --step 5` 清理合併：

- 雙市場 raw file 都存在且通過內容檢查才算該日完整。
- 任一市場失敗即拒絕當日 replacement。
- 發現歷史補洞時會重跑 merge，不再只相信舊 watermark。
- clean CSV 與 `margin_merge_status.json` 採 atomic replacement。

`scripts/download_margin_data.py` 是未被排程引用的 legacy second implementation，仍硬編碼舊根目錄 `F:\股票分析`。不要把它當 production canonical path；若要移除，另開 repo hygiene 票，不要混入資料修復。

Production 實跑：

- `python -X utf8 twstock.py --step 3`：補到 2026-07-16，TWSE + TPEx 成功。
- `python -X utf8 twstock.py --step 5`：更新 1,867 個日 K 檔，跳過 135 個，融資合併到 2026-07-16。
- clean margin 共 2,937,295 列。
- `清理後資料/margin_merge_status.json` 的 `last_merged_date` 為 `2026-07-16`。

### 2.4 Quarterly fundamentals

`twstock.py` Step 8 與 `scripts/backfill_balance_sheet.py` 現在：

- 申報截止日後 3 天寬限。
- TWSE / TPEx 任一來源 partial 即拒絕 replacement。
- 期別、必要內容與前季 80% coverage gate。
- 保留 partial 舊檔，通過驗證後才 atomic replacement。

Production 實跑：

- `python -X utf8 twstock.py --step 8`：0 個 pending，成功。
- `python -X utf8 scripts/backfill_balance_sheet.py`：至 2026Q1，0 個 pending，成功。
- `python scripts/fundamentals_completeness_audit.py`：月營收、EPS、損益表、資產負債表全綠。

### 2.5 日 K 舊歷史覆蓋候選為誤報

對抗閱讀確認 `twstock.py` 自 2026-03 起在舊檔不可讀時會跳過該 ticker，不會用短片段覆蓋正式歷史；正常更新則合併舊資料與新資料後 atomic replacement。`tests/test_daily_k_history_preservation.py` 已鎖定此契約。

## 3. KOL 排程修復

### 根因

`scripts/run_kol_tracker_daily.bat` 把 `kol_classify.py` stdout/stderr 重導向 `logs/kol_tracker.log`；分類器本身結尾又開啟同一檔案寫 log。Windows 對該 redirection handle 的分享模式造成 `PermissionError`。原批次檔未檢查 errorlevel，所以 2026-07-15、07-16 都在分類器失敗後繼續產生/發布頁面，Task Scheduler Last Result 仍為 0。

### 修正

新增 `scripts/run_kol_tracker_daily.py`：

1. 預設 07:00 排程只執行 classify 與 KOL dashboard；entry tracker/dashboard 已移交 Phase-2，避免早於新模型發布舊頁。
2. child output 完成後才 append log，避免同檔案鎖衝突。
3. 任一步非零或 timeout 立即停止。
4. 目標 HTML 必須存在、至少 1 KB、mtime 屬於本輪，否則不複製、不 commit、不 push。
5. 以 atomic copy 發布，只提交本模式的明確 dashboard 檔案；`--entry-only` 僅供 Phase-2 呼叫。
6. 即使本輪內容沒變也會 `git push`，讓前一次暫時網路失敗能在下一輪恢復。
7. batch wrapper 用 `exit /b %EXIT_CODE%` 將真實狀態交回 Windows Task Scheduler。

已驗：scheduler focused tests 全綠。未手動執行完整 live publish，避免審查期間額外呼叫分類 LLM、改 KOL DB 與推遠端；下一次 `\KolTrackerDaily` 07:00 是第一場 production 驗證。該 Windows task 仍是 `Interactive only`，這是既有 reliability 限制，本輪未改憑證/登入模式。

實戰檢查：

```powershell
schtasks /Query /TN "\KolTrackerDaily" /FO LIST /V
Get-Content logs\kol_tracker.log -Tail 120
git -C F:\kol-watch status --short --branch
git -C F:\kol-watch log -3 --oneline
```

成功標準：log 有 KOL 模式的兩個 `[step]` 與 `kol daily done`、無 `PermissionError` / `FAILED`；Task Last Result = 0；遠端 KOL dashboard commit 日期為當日。失敗時應看到 `kol daily FAILED` 且 Task Last Result 非 0，並且不得出現新的 stale dashboard commit。

## 3.1 Entry plan / dashboard 日期一致性修復

### 根因

1. 晚間 canonical entry list 可能在較新的 dataA shadow output 寫完前幾秒完成，因而保存舊模型來源。
2. 早晨 Phase-2 原本只更新 continuation backtest，沒有重跑 canonical `generate_entry_candidates.py`。
3. 07:00 KOL 排程又會發布 entry dashboard，造成較舊 entry plan 先被發布。
4. dashboard 隨後用 latest glob 載入較新的 model tags / dataA，形成同一卡片「GO 計畫 + sell/DOWN 標籤」的自相矛盾。

### 修正後契約

1. canonical generator 只能使用與 `snapshot_cache.pkl` as-of date **完全相同**的 dataA；若無 exact-date source，CLI 以 exit code 2 拒絕發布，不准向前回退舊檔。
2. entry JSON 必須記錄 `model_source` 與 `model_source_date`。
3. Phase-1 先完成 dataA shadow，再產 canonical entry plan。
4. Phase-2 在新 prediction 完成後依序刷新 dataA shadow、momentum、canonical entry plan，再用 `--entry-only` 發布 entry dashboard。
5. entry dashboard 只讀 plan 指定日期的模型與標籤；找不到就明示缺資料，不再混 latest artifact。
6. 頁面顯示 daily/weekly source freshness；舊資料必須可見，不得用 UI 隱藏。
7. canonical entry refresh 失敗時，不得寄出正常完成信；必須走 degraded/failure 路徑。

目前本機 2026-07-17 頁面刻意顯示舊來源，不應手動發布覆蓋遠端。這不是新 bug，而是修正後不再掩飾既有 stale data；等待下一次正常 Phase-2 產生同日對齊 artifact。

首場 Phase-2 實戰成功標準：

- `logs/entry_list_<today>.json` 存在，`model_source_date` 等於預期的前一個台股交易日。
- entry dashboard freshness banner 的 daily source 為 green/新鮮，沒有 sell/DOWN 與 GO 計畫跨日期混用。
- `F:\kol-watch\entry_dashboard_latest.html` mtime 與 git commit 都是本輪 Phase-2。
- 任一步失敗時，使用者收到 degraded/failure 通知，不是正常投資總結。

## 4. 回歸結果

- 全套（entry 修復後）：`452 passed, 502 warnings in 74.22s`。
- Entry/scheduler focused：`56 passed`。
- P0-C focused：37 passed。
- Scheduler focused：4 passed。
- 修改的 Python 模組均通過 `py_compile`。
- `git diff --check` 通過（Windows batch 只有預期的 LF→CRLF 提示）。
- Production 資料實跑後 repo 未產生追蹤檔變更。

Warnings 主要是既有 pandas fragmentation/downcasting 與 FastAPI/Starlette deprecation，不是本輪新增失敗；可另開 performance/dependency hygiene 票，不能混進 production gate 修改。

## 5. 不得直接修改的 P0

### P0-A：exit policy 除權息一致性

`scripts/exit_policies.py` 的 MA5 / high-water-mark production 預設仍可能使用未還原 close。下一步不是直接改，而是：

1. 寫 method lock，鎖現行與候選算法、窗口、樣本、metrics、kill criteria。
2. 兩分支使用同一 DuckDB as-of raw frame。
3. 以 ticker/date join，回報 status flips、return、MDD、turnover、實際除權息樣本。
4. PM 核可後才改 production default。

### P0-B：`ml/predict.py` guardrail 完整性

待驗候選包括必要欄位缺失、`operating_margin` NaN、極端預測檢查與 ATR clipping 順序、overheat/chip divergence/turnaround 文件漂移。下一步同樣先 method lock + fixed-snapshot A/B；不可僅依文件敘述改 threshold 或 production order。

## 6. 建議接手順序

1. **驗收 KOL 下一次 07:00 production run**：只讀 task/log/remote commit；有問題先修 scheduler，不碰選股模型。
2. **驗收下一次 Phase-2 entry run**：依 §3.1 四項成功標準；不得用手動 stale publish 假裝通過。
3. **P0-A method lock + A/B**：只產研究 artifact，PM 簽前不改 default。
4. **P0-B method lock + A/B**：同上。
5. **P1 industry mapping**：驗 train/serve mapping 是否一致，再決定是否修。
6. **P1 KOL timezone / escaping / XSS**：建立惡意/邊界 fixture 後修。
7. **資料補齊**：外資持股 110 個失敗日、TDCC 43 缺週（付費決策）、2021 valuation 3 個殘檔。
8. **P2**：金融股 balance sheet parser、EPS TTM 缺季不得借第五季；公司行動與配股比例已關閉。
9. **P3 retrain package**：前置三票已全綠，只有 PM 明確核可才啟動，且不得自動升 production。

## 7. 接手鐵律

1. 每個獨立完成項單獨 commit，格式 `type(scope): summary`。
2. 模型、feature processor、guardrail、filter、threshold、production exit 改動前必須 method lock + A/B / 對應回測。
3. 不動 protected model artifacts，除非已讀 model-artifact-protection skill 且取得核可。
4. 資料修復要同時驗來源覆蓋、內容、列數、必要欄、日期/期別與 semantic invariant。
5. 測試不得寫 production DB、ledger 或 static artifact。
6. journal/agent finding 只是候選；必須以程式碼、fixture 與可重現證據驗證。
7. 不再使用 `scripts/download_margin_data.py` 判 production margin 狀態。
8. 不把 KOL / ChipK diagnosis 當 final entry list；entry task 仍須遵守 canonical generator 與 stock-entry skill。

## 8. 快速驗證指令

```powershell
python scripts/fundamentals_completeness_audit.py
python -m pytest -q
python -m pytest -q tests/test_run_kol_tracker_daily.py
python -m pytest -q tests/test_generate_entry_candidates.py tests/test_entry_dashboard.py tests/test_smart_update_auto.py
schtasks /Query /TN "\KolTrackerDaily" /FO LIST /V
Get-Content logs\kol_tracker.log -Tail 120
git status --short --branch
```

## 9. 結案邊界

可以宣稱：P0-C 資料凍結路徑完成、KOL stale publish 與 entry plan/model 日期錯配的程式修正完成。
不可宣稱：全系統審查全數 CLOSED、P0-A/P0-B 已修、KOL/Phase-2 production 實戰已驗、歷史資料缺口已補完、模型已重訓或升版。
