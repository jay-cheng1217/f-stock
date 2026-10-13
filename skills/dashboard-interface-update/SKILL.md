# Dashboard Interface Update Notification

## When to use
任何改動使用者介面(UI/呈現層)後 —— 包含:
- `scripts/entry_dashboard.py`(進場儀表板 Artifact)
- `scripts/kol_dashboard.py`(KOL 追蹤儀表板 Artifact)
- `frontend/index.html` / backend routers 影響 web 畫面
- Artifact 重新發佈、版面/tag/欄位增減

## Rules(強制,使用者明確要求 2026-07-04)

1. **改完介面必須在回覆中直接貼出 URL**,不可只說「已更新」。
   - 進場儀表板固定網址:`https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b`
   - Artifact 一律用 `url` 參數原地重發(redeploy 同網址),**絕不另開新網址**;
     若不小心產生新網址,視為錯誤,回到固定網址重發並告知。
2. **必須寄信通知使用者**(best-effort,寄失敗不擋任務但要回報):
   ```bash
   python scripts/send_web_link_email.py --remote-url <最新 tunnel URL 或固定儀表板網址>
   ```
   - 最新 tunnel URL 從 `logs/smart_update_auto_YYYYMMDD.log` 搜 `Cloudflare tunnel:` 取得;
     取不到就用固定儀表板網址頂替 `--remote-url`。
   - 中文郵件規範見 `skills/email-utf8-delivery/SKILL.md`(UTF-8 base64,先 dry-run 驗編碼)。
3. 回覆中同時提醒:瀏覽器快取需 Ctrl+F5。

## 介面架構備忘(避免「兩種版本」混淆)

| 介面 | 網址性質 | 用途 |
|---|---|---|
| 進場儀表板(Artifact) | **固定**。每日 07:50 由雲端例行工作重發(唯一的自動更新者;見下方「固定網址由誰更新」) | 主要介面:名單+大盤+個股四大面向/模型/ChipK/短波 tag+帳本 |
| 遠端 Web(cloudflared tunnel) | 每晚 pipeline 重啟即換網址 | 備用:Workbench/Forecast 互動深入分析 |
| 本機 http://127.0.0.1:8001 | 固定(僅本機) | 同遠端 Web |

每日郵件(`send_web_link_email.py`)綠色主按鈕=固定儀表板,藍色=當日 tunnel。

## 固定網址由誰更新(2026-10-13 查證後補記)

1. **雲端例行工作「TW+KOL Dashboard 每日重發」(唯一的自動更新者)**:claude.ai routine,每日 07:50(台北)。
   它重發的是 GitHub `kol-watch` repo 根目錄的 `entry_dashboard_latest.html` 與 `kol_dashboard_latest.html` 原檔;
   這個 repo 由本機早上的 `scripts/run_kol_tracker_daily.py::_publish`(複製到 `F:\kol-watch` 後 commit + push)更新。
   所以本機當天儀表板建置失敗時,雲端仍會重發**前一份**檔案(頁面看起來有更新、內容是舊的)。
   查執行紀錄:`RemoteTrigger` 的 `list` / `list_runs`。
2. **Claude Desktop 排程 `daily-entry-dashboard-republish`(已停用,2026-10-13 PM 核可)**:原為平日 07:53 本機重建後重發同網址,
   需 Claude Desktop 開著。停用原因:與雲端那條重複(同一天的資料每天早上各發一版、多花一輪用量),而且它在
   2026-10-01~10-13 卡住兩週,頁面照常由雲端更新、沒有人察覺。卡住的原因是排程裡的 Claude 臨時寫了 PowerShell 指令,
   而本機只有 Bash 指令全部免核准、PowerShell 要字串完全相符才免核准,於是停在沒人按的核准視窗。
   任務沒有刪除,只是停用;指示已改成「只用 Bash、指令原樣執行」(舊指示備份在
   `logs/quarantine_dashboard_routine_20261013/SKILL.md.before`),10/13 晚上以新指示實跑成功一次。
   雲端那條出問題時可當備援:在 Claude Desktop 的排程清單重新啟用,或直接按「立即執行」。
   **停用後的代價**:雲端重發的是推到 `kol-watch` 的檔案,本機推送失敗或當天沒重建時,不再有第二條路補上。

**判斷固定網址有沒有更新,要看頁面本身,不看排程紀錄**:用 Artifact `read` 看線上版的版本 id(是 Unix 時間)
與頁面資料裡的 `meta.trade_date` / `meta.generated`。2026-10-13 曾只憑「本機排程自 10/01 沒跑」就說成
「固定網址停更 9 個交易日」,實際上雲端那條每天都在重發。
收盤後到隔天 07:00 Phase-2 之前,`entry_dashboard.py` 會因名單憑證非當前而拒絕重建(EntryArtifactLineageError),屬正常;
這段時間只能重發現有檔。
