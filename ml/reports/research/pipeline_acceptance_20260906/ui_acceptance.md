# 隔離 UI／郵件驗收 — 2026-09-06

14:15部署補驗：使用者結束舊Web後，新PID13804已啟動且真HTTP健康8項全ok；
先前「Web待管理員重啟」的限制已解除，見web_live_deployment.json。
下列UI截圖／測試仍是原隔離驗收紀錄；資金集中與Artifact待同步的限制保留。

結果：**PASS_WITH_DECLARED_BOUNDARIES**。本報告驗證隔離真實流程的正式來源產物；早期 fixture 只補足未知資料、舊籌碼與卡片狀態的邊界案例，沒有以 fixture 取代最後 18 檔實際名單。

## 最終來源與範圍

- Workspace：`F:\stock\output\pipeline_acceptance_20260906\workspace`。
- canonical：`logs/entry_list_20260907.json`，觀察日 2026-09-07，型態／模型資料截至 2026-09-04，共 18 檔；不是 18 檔 production 買單。
- Dashboard：`F:\stock\output\pipeline_acceptance_20260906\workspace\ml\reports\entry_dashboard_latest.html`，9,363,885 bytes，SHA256 `60719d3441c4c980849e313e6ddf5e52a74e583b55db400677f09bd079a37d4e`。
- 最終郵件：`F:\stock\output\pipeline_acceptance_20260906\workspace\logs\acceptance_email.html`，22,352 bytes，SHA256 `4157930ff71c158279be29f2b144825d65330d8b334d6f9acbe485f2f53d2c00`。
- 此處 SHA 均以實際 file bytes 計算；先前 receipt 的 UTF-8 universal-newline 正規化 SHA 是另一範圍，不代表競爭改寫。

實際 browser 截圖使用的隔離 preview SHA256 為 `acda91fdd6c15dff74a72525a3a9cd3d174affe24cf7a9b6118be75e7e6b5926`（同為 9,363,885 bytes）。root 隨後透過 `dashboard_banner_verified` 在 13:46:42.244778 重建、耗時 22.08 秒，最新隔離產物 SHA 如上。逐字比較確認只有 `meta.generated` 從 13:41 改為 13:46，替換該時間欄位後所有 HTML 字元完全相同；沒有名單、文字、價格或邏輯差異。JSON 同時保存 QA snapshot 與最新 runner 產物的 hash 及 receipt。

## Dashboard 驗收

1440×1080 與 390×844 均無水平溢出；18 張卡片全部是「觀察・待盤中確認」，型態／模型日期均為 9/4。rere 六檔依序為 3605、3189、3066、2364、3176、6472，保留 60 交易日觀察與模型不作否決；主策略保留 20D。卡片清楚標明系統代理篩選，並非 KOL 本人推薦。

頁首將 model guard 表達為「模型風險檢查警示」，明確將 Champion 正式放行交由 unified。實際 unified 本次只有 2603 OPEN，因此不能把 prediction guard 解讀為所有正式訊號關閉。7/3 大戶雷達顯示過舊，未用於封鎖現行 rere 卡片。上次收盤有日期，區間內也不視為盤中守穩或已成交。details 預設收合，實際展開、收合皆通過。最終 dashboard console 0 errors／0 warnings。

## 郵件呈現與語意

「訊號配置上限」保留 99.9% 原值，清楚說明空帳本單一訊號可能預留接近全部資金，並非已成交或目前持倉比例。這項呈現修復沒有解決資金集中政策風險：真實空帳本測試可將 99.85% 配到 pending；既有 Champion 帳本狀態應另看 allocation/portfolio acceptance 報告。

動能研究與 production、canonical 觀察名單分開，附資料日 9/4；日K回顧不能證明盤中守穩。退休 ChipK 來源明示已退役，不再聲稱已更新。

最後版面變更僅針對 `sec-entry-candidates`：每檔六欄主列，結論、理由、符合／缺少證據移到全寬說明列；手機欄名用真 HTML 文字置於值上方。逐列比對證明原 5 股（2465、2464、6530、6640、6224）的 7 欄內容、數字、順序、日期與判斷全部不變；其他 sections 的 innerHTML 完全相同。

| 實測項目 | 修前 | 最終 |
|---|---:|---:|
| 390px 郵件表格高度 | 7041.875px | 3598.656px（減少 48.9%） |
| 390px 頁面寬／scrollWidth | 390／390 | 390／390 |
| 390px 結論列寬度 | 七欄擠壓、每行常僅 2–3 字 | 346px，整行可讀 |
| 390px 可見 HTML 欄名 | 表頭 | 30 個（5 股×6 欄） |
| 1440px 頁面寬／scrollWidth | — | 1440／1440 |
| 1440px 表格高度／結論寬 | — | 1392.172px／725px |

最終郵件沒有 script；最後載入 console 0 errors／0 warnings。早期 preview 曾有 favicon.ico 404，僅本機 preview 圖示請求，不是應用 JS 錯誤。

已核對 [Gmail 官方 CSS 支援文件](https://developers.google.com/workspace/gmail/design/css)（2026-07-22 更新）：其列出 class／id／element、螢幕寬度 media query 與 display、word-wrap。工程上以真 HTML 欄名、簡單顯示樣式與換行 fallback，避免新增依賴 pseudo-element／absolute 定位。**這仍是 Chromium 瀏覽器驗收，沒有聲稱已在 Gmail、Outlook 或原生郵件 App 實際收信測過。**

## 建置與保留證據

Dashboard 於隔離 cwd／`STOCK_BASE_DIR` 下執行：

```text
C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe -B -X utf8 scripts/entry_dashboard.py --date 20260907
```

session 20385、exit 0，stdout 證實來源 entry_list_20260907.json、輸出 9144 KB。hash marker 13:41:35.926466+08 → HTML mtime 13:42:02.790707+08，26.864 秒僅為前標記至輸出的推導區間，未記錄精確 Stopwatch 執行耗時。`main` 只讀既有 JSON 並寫 OUT HTML。canonical、watchlist、entry_filter、rere、shadow_dataA 五份檔案前後 SHA 全不變；完整 hash 證據見 JSON 的 build_audit。

最後郵件由 guarded runner 執行 `pipeline_acceptance.py run --label email_observation_html_labels --timeout 120 -- scripts/pipeline_acceptance_checks.py email`，13:58:11.620984 起、2.25 秒、exit 0，真實 composer dry run／MIME UTF-8 base64 通過。只用 dummy .invalid 設定，SMTP 未執行。

聚焦測試：`test_entry_dashboard_cards.py`、`test_send_daily_email_encoding.py`、`test_ai_summary_context.py`、`test_email_observation_semantics.py`，**32 passed in 1.81s**。涵蓋卡片日期、stale／unknown、模型／正式訊號來源界線、日K與成交、配置上限、退役來源及跨列資料保存。

## 截圖（絕對路徑）

- `F:\stock\output\playwright\pipeline_acceptance_dashboard_desktop_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_dashboard_mobile_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_dashboard_mobile_card_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_email_desktop_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_email_desktop_table_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_email_mobile_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_email_mobile_table_20260906.png`
- `F:\stock\output\playwright\pipeline_acceptance_email_mobile_table_before_20260906.png`

## 未執行／仍有界線

本 subagent 沒有寄信、發布、修改排程、重啟服務或獨立 commit；root 負責整合與發布。固定 Claude Artifact 的原網址更新仍無可用路徑。Windows 拒絕 root 重啟舊 Web 程序後，現行服務仍需管理員完成重啟；隔離 ASGI 測試不能證明舊服務載入新程式。資金集中政策未在此改動。
