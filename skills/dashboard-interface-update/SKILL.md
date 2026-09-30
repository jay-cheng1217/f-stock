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
| 進場儀表板(Artifact) | **固定**,每平日 07:45 由 Claude Desktop 排程任務 `daily-entry-dashboard-republish` 重發同網址(任務檔在 `C:\Users\cheng\.claude\scheduled-tasks\`;需 Claude Desktop 開著才會跑,關著則下次開啟補跑) | 主要介面:名單+大盤+個股四大面向/模型/ChipK/短波 tag+帳本 |
| 遠端 Web(cloudflared tunnel) | 每晚 pipeline 重啟即換網址 | 備用:Workbench/Forecast 互動深入分析 |
| 本機 http://127.0.0.1:8001 | 固定(僅本機) | 同遠端 Web |

每日郵件(`send_web_link_email.py`)綠色主按鈕=固定儀表板,藍色=當日 tunnel。
