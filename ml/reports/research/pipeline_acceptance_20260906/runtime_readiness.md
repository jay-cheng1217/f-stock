# Runtime readiness — 2026-09-06

**14:15補驗：現行Web修正版載入已PASS。** 使用者結束舊PID21164後，14:14新版PID13804成功啟動，
實際健康API的8項檢查全ok，首頁／股票API均200；隧道3624保持原程序。
61db15ca修復健康查詢與pipeline exit，19a92fe4修復AgentArena共用DB reader。
以下為13:10–13:19的原始唯讀查核快照，保留當時NOT_VERIFIED與阻擋背景；
最新部署結果見web_live_deployment.json、health_deployment.json及總驗收報告。

查驗時間：2026-09-06T13:10:43.4820882+08:00；更新：2026-09-06T13:19:42.190693+08:00（Asia/Taipei）。營運設定已讀取、Web 可達；健康 reader 已修復且 fixture 通過，現行服務是否載入修復仍未驗證。完整 Windows 端到端結果為 PARTIAL_RUNTIME_EVIDENCE。

| 排程 | 實際週期／時間 | Action | 最近執行 | Last result | 下次執行 |
|---|---|---|---|---|---|
| TW_Stock_Nightly | Monday,Tuesday,Wednesday,Thursday,Friday 19:30 | F:\stock\scripts\run_phase1_night.bat | 2026-09-04T19:30:00.0000000+08:00 | 0x00000000 | 2026-09-07T19:30:00.0000000+08:00 |
| TW_Stock_Daily | Monday,Tuesday,Wednesday,Thursday,Friday 07:00 | F:\stock\scripts\run_phase2_morning.bat | 2026-09-04T07:00:00.0000000+08:00 | 0x00000000 | 2026-09-07T07:00:00.0000000+08:00 |
| TW_Stock_Daily_Backup | Monday,Tuesday,Wednesday,Thursday,Friday 07:02 | F:\stock\scripts\run_daily.bat | 2026-09-04T07:02:00.0000000+08:00 | 0x00000000 | 2026-09-07T07:02:00.0000000+08:00 |
| TW_Stock_Weekly_Retrain | Saturday 10:00 | F:\stock\scripts\run_weekly_retrain.bat | 2026-09-05T10:00:00.0000000+08:00 | 0x00000000 | 2026-09-12T10:00:00.0000000+08:00 |
| TW_Stock_Weekly_Retrain_Backup | Saturday 10:05 | F:\stock\scripts\run_weekly_retrain.bat | 2026-09-05T10:05:00.0000000+08:00 | 0x00000000 | 2026-09-12T10:05:00.0000000+08:00 |

5 個排程皆 Enabled / Ready、missed runs=0；Action 檔存在，batch 已 cd /d F:\stock。Phase-1 的 batch 註解仍寫 23:00、Phase-2 寫 05:00，實際以 Scheduler 的 19:30／07:00 為準。每週主／備重訓仍啟用，下次為 9/12 10:00／10:05；這是既有設定，不等於本輪授權重訓。

| 本機 GET | HTTP | 證據／限制 |
|---|---|---|
| /api/pipeline/status（匿名） | 401 | 正常認證限制；後續使用既有 helper |
| /api/pipeline/status（既有認證） | 200 | 端點回 daily health=fail，原因是 DuckDB connection configuration mismatch |
| /api/health_check | 200 | 既有 replay 報告產生於 9/5 12:34:56，latest prediction=9/4；不是即時完整健康 |
| /api/stocks/2330/daily?days=3 | 200 | 9/2、9/3、9/4 三列，9/4 close=2410；僅證明該股票近期資料可讀 |

健康誤判根因：app.py 的 health reader 另開 read_only=True 連線，與同 process 中 engine 既有 RW handle 不相容。端點回傳 "Can't open a connection to same database file with a different configuration than existing connections"，所以最新日K／筆數未取得，進而標 daily_k_fresh、daily_k_snapshot_size 為 error。一般股票 API 走共用 engine cursor，確實可讀到 9/4。因此這次 failure 是健康查詢不可得，不是已證明全市場資料 stale。

最小修復：health reader 改用 `get_conn(read_only=True).cursor()`，finally 只關閉本次 cursor。未改 DB engine、ML、策略或連線重啟規則。`python -B -X utf8 -m pytest tests/test_pipeline_health_connection.py -q`：**3 passed in 0.86s**。測試用真實 temp DuckDB 重現舊 RW／RO 衝突，驗新 reader 正常、真 stale 仍 fail、例外後共用連線仍可用；不是 mock 出成功回應。

weekly 是已完成流程中的實際局部失敗：pipeline_status.json 寫入時間 9/5 12:35:00，elapsed_min=155.0，all_ok=false，只有 AgentArena=failed；retrain log 於 12:35:01 記錄 Web reconnect。AgentArena 開只讀 DB 失敗後複製 snapshot 也遇 PermissionError，轉成 "DuckDB is locked by the web process"。狀態檔並非尚未 finalized。

Windows exit 0 不代表全階段通過：daily_pipeline.run_step 捕捉錯誤後回 False；main 將 all_ok=false 寫入檔案，卻仍正常結束、沒有非零 exit code，故 wrapper 可回 0。此輪只定位並記錄 weekly 原因，沒有重跑、重訓或改其流程。

Web listener PID=21164，python.exe，本身無 --reload；parent PID=37252 已查無 process，無法重建最初啟動參數，因此不能宣稱已確認整組無 reload。沒有記錄完整 commandline。現行服務載入修復版本：NOT_VERIFIED，交由 root 整合驗收。

NOT_RUN：Windows 實際觸發、Web／tunnel 重啟、DB release/reconnect、模型訓練、帳本寫入、寄信、發布或推送。本輪只改 app.py、focused tests 及本報告，所有現行服務 GET 均只讀；沒有將 HTTP 200 或排程 exit 0 包裝為全流程通過。

證據：Get-ScheduledTask / Get-ScheduledTaskInfo / Export-ScheduledTask；app.py:204、1325、2389、3561；backend/db/engine.py:78；backend/routers/stocks.py:87；scripts/daily_pipeline.py:116、515；scripts/agent_arena.py:202；logs/retrain_20260905.log:228、364。JSON 留存觸發設定、白名單健康欄位與 response SHA256。使用 repo 既有 `_admin_api_headers` 正常認證，未顯示／保存 headers、密鑰、完整環境或 principal。
