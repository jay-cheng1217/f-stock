# KOL 觀點追蹤系統(KOL Opinion Tracker)規劃書

> 立案 2026-07-02。發起:用戶(PM)。範本參考:Serenity Watch(capafy)/ trackserenity.com。
> 狀態:**規劃已核定方向,T0 資料保全先行,T1-T6 待 PM 逐張放行**。

---

## 一、PM 視角

### 1.1 問題陳述

用戶(台股裁量交易者)需要系統化追蹤「可信 KOL 的選股觀點演變」:
- 現況:rere 的觀點靠人工轉述+截圖,無時間軸、無歷史對照、散落在對話裡。
- Serenity(美股 AI 供應鏈)的觀點有第三方 tracker,但無「觀點歷史+多空標注」的自有資料集,
  且與台股研究流程脫節。
- 痛點本質:**KOL 觀點是有價值的 watchlist 雷達,但沒有結構化就無法回測、無法驗證其 hit rate、
  無法察覺觀點反轉**。

### 1.2 用戶決策紀錄(2026-07-02 四項拍板)

| 決策點 | 裁示 |
|---|---|
| 追蹤對象 | **台股 KOL 與 Serenity 都要,分開追蹤**(兩條獨立 track,不混池) |
| 用途 | **先自用,架構預留對外** |
| Serenity 資料源 | GitHub skill(框架/歷史語料索引)+ trackserenity.com(即時 feed) |
| 產出形式 | **獨立 HTML dashboard** 優先 |

### 1.3 定位鐵律(繼承自現有系統判決,不可違反)

1. **KOL 觀點 = 雷達層,永遠不是扳機**(「投信連買=已被看見」教訓的直接推廣;
   任何 KOL mention 要變成進場條件,必須先過回測 + 5 步進場紀律)。
2. 台股 track 的候選若要進名單,一律走 `generate_entry_candidates.py` canonical 流程。
3. 來源白名單制(新聞 overlay 鐵律的推廣):只收公開、可溯源(附原文連結+時間戳)的內容。
4. 多空標注是 AI 推斷,dashboard 必須標示「AI-inferred,可能有誤」,每則附原文連結可驗證。

### 1.4 MVP 範圍(自用)

- ✅ Serenity track:hourly 收集 → 累積資料集 → 標的擷取+多空分類 → HTML dashboard(日/週/月/季)
- ✅ 台股 KOL track:手動貼文/截圖文字 ingest 工具 → 同一 schema → 同一 dashboard(分頁)
- ✅ per-stock 詳情頁:stance 時間軸 + 原文連結 + 首次提及以來價格
- ❌ 非目標(MVP 不做):訂閱/金流/帳號、hourly AI Q&A、自動進場整合、X API 直連

### 1.5 風險與法遵

| 風險 | 評估 | 對策 |
|---|---|---|
| trackserenity.com 停站/改版 | 中;個人專案隨時可能消失 | adapter 隔離 + 每日累積本地化(資料在我們手上);斷源後可換 X API Basic(~US$200/月)或其他鏡像 |
| feed 只有 rolling 80 則 | **確認**(2026-07-02 實測) | **T0 立即開始輪詢累積,晚一天=掉一天資料** |
| 對外商用 | 內容權利(轉錄他人推文)+台灣投顧法(出售分析)敏感 | 自用階段不觸發;若對外,需重新立案評估,非工程問題 |
| AI 分類錯標 | 必然發生 | 每則附原文連結;stance 允許人工 override 欄位 |
| 台股 KOL 內容取得 | 分點/私群內容不可自動抓 | 只收公開內容+用戶手動轉貼;沿用 ChipK 截圖既有流程 |

### 1.6 成功指標(自用)

- 30 天後:Serenity 資料集 ≥ 500 則不重複貼文、標的覆蓋 ≥ 50 檔、dashboard 日常有在看。
- 60 天後:能回答「X 股票她的觀點演變」且每步可溯源;台股 track 至少 1 位 KOL 穩定累積。
- 90 天後(研究題):KOL mention 後 N 日報酬統計(= 她的 hit rate 可量化,決定雷達權重)。

---

## 二、SA 視角

### 2.1 架構(五層,adapter 隔離來源風險)

```
[Ingestion adapters]        [Normalize]      [Classify]         [Store]           [Render]
trackserenity poller  ──┐
GitHub skill 語料(一次性)├─→ 統一 post schema → AI 標的擷取+多空 → kol_tracker.db → HTML dashboard
台股KOL 手動 ingest   ──┘                      (pluggable)        (SQLite)          (自包含,可分享)
(未來: X API / RSS)                                                                (預留: FastAPI router)
```

- **兩條 track 分開**:`source_group` 欄位(`serenity` / `tw_kol`),dashboard 分頁,絕不混池。
- **預留對外**:schema 天生多來源多帳號;renderer 吃 DB 不吃 adapter;加帳號=加一列 sources。

### 2.2 資料模型(SQLite:`kol_tracker.db`,與 paper_portfolio.db 同 pattern)

```sql
sources(id, group, platform, username, nickname, added_at)
posts(id PK, source_id, text, url, posted_at, collected_at, raw_json)
mentions(id, post_id, symbol, market,           -- US / TW
         stance,                                 -- bullish/bearish/neutral/unclear
         stance_source,                          -- ai / manual_override
         confidence, reason_summary, classified_at)
-- 觀點時間軸 = mentions 按 symbol 聚合,不需另表
```

### 2.3 Serenity track 技術路線(已實測驗證 2026-07-02)

- `GET https://www.trackserenity.com/data/signals.json`:公開、無認證、robots.txt Allow all,
  ~57KB,含最近 **80 則**推文(id/全文/url/createdAt/cashtags)。**hourly 輪詢、以 id dedup 累積**。
- 該站 `/api/stocks` 只是 Finnhub 報價代理 → 不依賴;美股報價自接 Finnhub(repo 已有使用經驗)。
- GitHub skill `references/` 提供框架與案例原型(AXTI/SIVE 等),當 Q&A/分類的 prompt 背景,
  不含原始推文全文(歷史語料無法回補,更凸顯 T0 急迫)。
- 台股價格:`日K資料/`;美股價格:Finnhub(免費 tier 夠用)。

### 2.4 分類器(pluggable)

- MVP:cashtag 直接擷取($AXTI)+ 無 cashtag 時用公司名對照表;stance 用 Claude API
  (haiku 級,每日 ~20-50 則,成本可忽略)單則分類:{symbol, stance, confidence, reason}。
- 中文 KOL:同一介面、不同 prompt(台股代號/俗名對照,如「台積」→2330)。
- 設計成可換人工:`stance_source=manual_override` 優先於 ai。

### 2.5 Dashboard(自包含 HTML,參考 Serenity Watch 四視圖)

- 單檔 HTML + 內嵌 JSON(無外部依賴,可寄可分享);日/週/月/季四切換;
- 首頁:當期被提及標的卡片(stance 色標+提及次數+最新一句);
- 個股頁:stance 時間軸(bullish/bearish 色帶)+ 每則原文連結 + 首次提及以來價格線;
- 產出掛每日 pipeline(`ml/reports/kol_dashboard_latest.html`),Serenity/台股分頁。

### 2.6 SA 待決(派工前要定)

- Claude API key 管理(env);失敗 fallback = 標 unclear 不阻塞收集。
- 台股 KOL 首發對象與內容格式(純文字?截圖 OCR 先不做,貼文字)。

---

## 三、RD 視角(工單拆解)

| # | 工單 | 內容 | 驗收 | 估時 | 狀態 |
|---|---|---|---|---|---|
| T0 | **資料保全收集器**(先行) | `scripts/serenity_feed_collector.py`:輪詢 signals.json → SQLite dedup 累積;Windows 排程 hourly | 連跑 24h 無重複無遺漏;斷網自動下次補 | 0.5d | **已上線 2026-07-02** |
| T1 | Schema + 台股手動 ingest | 建三表;`scripts/kol_ingest.py --group tw_kol --user <名> -f post.txt` | 貼文入庫可查 | 0.5d | 待放行 |
| T2 | AI 分類器 | cashtag/名稱擷取 + Claude stance 分類,寫 mentions | 抽 20 則人工對照,方向錯誤 ≤2 | 1d | 待放行 |
| T3 | HTML dashboard v1 | 四視圖 + 個股 stance 時間軸 + 原文連結 | 開瀏覽器可用,無外部資源 | 1.5d | 待放行 |
| T4 | 價格整合 | 個股頁價格線(美 Finnhub/台 日K) + 首次提及標記 | 圖與價對得上 | 0.5d | 待放行 |
| T5 | 掛每日 pipeline | dashboard 重產 + 摘要進日報 email 一節 | 排程隔日自動產出 | 0.5d | 待放行 |
| T6 | (研究)mention 後報酬統計 | KOL hit rate 量化,決定雷達權重 | 報告 + scoreboard 註冊 | 1d | 排隊 |

依賴:T0 → T2 → T3 → T4/T5;T1 平行;T6 需 ≥30 天資料。總估 ~5 人日(不含 T6 等資料)。

### Skill 對應(開單規範)

- 主要:本規劃書;輔助:`email-utf8-delivery`(日報節)、`git-commit-after-change`;
- 完成後驗證:T2 人工抽測、T3 `verify` 實際開頁、T6 進 `strategy_scoreboard.py` 月度重評。
