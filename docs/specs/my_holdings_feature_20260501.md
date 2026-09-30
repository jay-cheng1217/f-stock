# 「我的持股」功能 — 需求單

- **發起人**: PM
- **發起日**: 2026-05-01（2026-05-02 修訂 ETF 支援範圍）
- **目標使用者**: 系統擁有者本人（單帳號、本地端）
- **MVP 預估**: 4.5-5.5 天（ETF 基本支援已納入）
- **不阻塞主線**: W18 主線仍是 #5 V2 sector bias，本功能排 W19/W20

---

## Background

目前系統只有 **auto-generated paper portfolio**（V2 Champion / Shadow / V4 Rank-15），沒有「使用者真實持股」的管理介面。實戰交易時必須手動對照 V2 訊號 + 自己 broker 持倉，效率低且容易漏掉 exit / 加碼訊號。

需要一個介面：
1. 手動輸入個股或 ETF 持倉（張數、成本、損益兩平價）
2. 每天自動套用既有 14 防呆規則 + V2 訊號 → 產出該檔當日 review（出場/加碼/觀望）
3. 跨持倉做組合層級風控提示

---

## 票 #7 — 我的持股 MVP (P1, 3-4 day)

### Why

讓系統從「研究產出」變成「實戰決策輔助」。重複利用既有 prediction_explain、market_regime、14 防呆規則，邊際開發成本低但價值大。

### Scope (P0 — MVP)

#### 1. 資料儲存
- 新增 SQLite 檔 `my_holdings.db`（**加入 .gitignore，不可入庫**）
- 表 `holdings`:
  | 欄位 | 型別 | 說明 |
  |---|---|---|
  | id | INTEGER PK | |
  | ticker | TEXT | 4 位股號或 ETF 代號 |
  | asset_type | TEXT | `stock` / `etf` |
  | shares | REAL | 張數（支援零股） |
  | avg_cost | REAL | 加權平均成本 |
  | breakeven_price | REAL | 含手續費/稅後損益兩平（可手動或自動） |
  | entry_date | TEXT | 進場日（可選） |
  | note | TEXT | 自由備註 |
  | created_at / updated_at | TEXT | |
- 表 `holding_transactions`（買賣記錄，未來算 realized 用）:
  | 欄位 | 型別 |
  |---|---|
  | id, holding_id, action(buy/sell), shares, price, fee, tax, transaction_date, note | |

#### 2. Backend
- 新 `backend/routers/my_holdings.py`，提供 CRUD：
  - `POST /api/my_holdings` 新增
  - `GET /api/my_holdings` 列表（含當日 close、市值、未實現損益）
  - `PATCH /api/my_holdings/{id}`
  - `DELETE /api/my_holdings/{id}`
  - `POST /api/my_holdings/{id}/transaction` 加買/減倉記錄（自動重算 avg_cost）
  - `GET /api/my_holdings/{id}/review` **每日訊號** — 套用既有 prediction_explain + 14 防呆規則 + market_regime
- 訊號分類:
  - 🔴 **EXIT** — 觸發 14 條硬擋任一 / MA5_BREAK / ATR stop / 跌破 breakeven
  - 🟡 **TRIM** — 高乖離 / 籌碼頂部背離 / 流動性下降
  - 🟢 **ADD** — V2 模型仍正報酬 + 持有獲利 + 未觸發任一 EXIT 訊號
  - ⚪ **HOLD** — 中性

#### 3. Frontend
- 新增 tab「我的持股」
- 表格欄: 代號 / 名稱 / 類型 / 張數 / 成本 / 現價 / 市值 / 未實現損益% / 訊號燈號 / 操作
- 單筆 detail panel: 完整 prediction_explain + 觸發的防呆規則 + 建議動作
- 新增/編輯/刪除 modal
- 響應式：桌面表格、手機卡片

### 驗收條件 (MVP)

- [ ] 可手動新增/編輯/刪除個股持倉，資料持久化
- [ ] 列表頁顯示當日收盤價、市值、未實現損益（誤差 < 0.1%）
- [ ] 每日 review 對單一個股能產出至少 EXIT/ADD/HOLD 三種訊號之一，且訊號內容引用既有 prediction_explain
- [ ] 14 防呆硬擋（流動性、暴跌、極端乖離、本業虧損、籌碼頂部背離）對使用者持股仍能正確觸發
- [ ] `my_holdings.db` 在 .gitignore，且不會被 auto-commit hook 帶入

---

## ETF 支援（修正版 — MVP 內基本支援）

### 驗證結果（2026-05-02 PM 修正）

先前需求單把 ETF 全部推到 #8 是**過度保守**。實際驗證：

1. **`twstock.py:219` 用 `.isdigit()` 過濾**，4-5 位數 ETF 代號（0050、00878、00919）都會通過，**沒有結構性限制**
2. **TWSE STOCK_DAY API 本來就支援 ETF**，沒抓只是因為 ticker 清單沒包含
3. 既然 ETF 在台股零售投資組合中佔比高（散戶常 30-50%+ 部位是 ETF），把它推給「未支援」等於 MVP 對使用者真實組合無感

### MVP 內 ETF 必做（票 #7 scope 擴充）

1. **資料補抓** (預估 0.5d) — 擴充 ticker 清單，新增主流 ETF：
   - 大盤型: `0050`, `006208`, `00692`, `00850`
   - 高股息: `0056`, `00878`, `00919`, `00929`, `00940`, `00713`, `00733`
   - 主題型: `00757`(NASDAQ), `00646`(SP500), `00662`(NASDAQ100), `00893`(電動車)
   - 槓桿/反向: `00631L`, `00632R`（**標記高風險，UI 警示**）
2. **ETF 訊號池 = 個股訊號池 − 財務面**:

| 類別 | 個股有 | ETF 適用？ |
|---|---|---|
| 流動性硬擋 (rule 7) | ✓ | ✓ 適用 |
| 暴跌硬擋 (rule 8) | ✓ | ✓ 適用 |
| 極端預測值 (rule 9) | ✓ | ✓ 適用 |
| ATR 報酬天花板 (rule 9b) | ✓ | ✓ 適用 |
| 極端乖離 (rule 10) | ✓ | ✓ 適用 |
| 波段停損 -10% (rule 14) | ✓ | ✓ 適用（槓桿型可考慮收緊） |
| 本業虧損 (rule 1, 11) | ✓ | ❌ **不適用**（ETF 無營業利益） |
| 估值虛胖 (rule 2) | ✓ | ❌ **不適用** |
| 低基期/業外收入 (rule 3) | ✓ | ❌ **不適用** |
| 利潤率下滑 (rule 4) | ✓ | ❌ **不適用** |
| 法人買賣超 / 籌碼背離 (rule 6, 13) | ✓ | ⚠️ 部分適用（ETF 也有外資買賣超，但解讀不同） |
| 技術指標 RSI/MACD/KD | ✓ | ✓ 適用 |

3. **UI 明確標示**:
   - ETF 詳情頁顯示「**訊號池: 技術 + 流動性子集**」
   - 不適用的防呆規則用灰色字標註「ETF 不適用」，不可隱藏

### MVP 後再做（票 #8，降級為 P3）

ETF-specific 進階訊號（**需要新資料源，非 MVP 必要**）:
1. **折溢價**（NAV vs 市價）— 需從投信公會抓淨值
2. **規模變化**（資金流向）— 需從投信公會抓 AUM
3. **追蹤誤差** — 需與底層指數比對
4. **配息調整 breakeven** — 需 ETF 配息行事曆

這些做完才稱得上「ETF 完整支援」，但 MVP 用個股子集規則已經能對 ETF 提供有用訊號（流動性、暴跌、乖離、停損），不會誤導。

### 風險：V2 模型對 ETF 的 score 解讀

V2 模型訓練時 universe **不含 ETF**（CLAUDE.md 提到 universe 是個股）。即使把 ETF 餵進 inference pipeline，**`pred_return_20d` 對 ETF 的可信度未驗證**。

MVP 處理方式：
- ETF 不顯示 V2 model `pred_return_20d` / `up_prob` / SHAP 解釋
- 只顯示：技術面訊號、防呆規則觸發、市場 regime、價格事件（暴跌/漲停/跌停）
- 在 UI 標註「ETF 不適用 V2 alpha 模型」

這樣使用者拿到的 ETF 訊號是「規則型」而非「模型型」，誠實但仍有用。

---

## 進階 Companion Features（建議優先級）

### 🔵 P1 — 對實戰決策直接有用

| # | 功能 | 為什麼建議 | 估時 |
|---|---|---|---|
| **9** | **組合層級風控提示** | 套用 sector cap 概念到個人組合（單一產業 > 30% 警示）、現金佔比、Beta 暴露 | 0.5d |
| **10** | **News alert per holding** | 重用既有 news scoring，當持股有重大新聞自動標記 | 0.5d |
| **11** | **持股 vs TWII attribution** | 同 V2 Champion 那套 Brinson — 個人組合每週 alpha 分解，知道是 selection 還是 allocation 賺賠 | 1d |

### 🟣 P2 — Nice to have

| # | 功能 | 為什麼建議 | 估時 |
|---|---|---|---|
| 12 | 配息追蹤 + 自動調整 breakeven | 重用既有 EPS / 現金股利資料，年化殖利率與成本回收率 | 1d |
| 13 | What-if 模擬 | 「賣 N 張會怎樣 / 加碼 M 張會怎樣」，含手續費/稅 | 0.5d |
| 14 | CSV 匯入 | 從券商月對帳單批次匯入持倉 | 0.5d |
| 15 | 損益歷程曲線 | 個人組合每日權益曲線 vs TWII，跟 Champion 帳本平行 | 0.5d |

### 🟡 P3 — 未來迭代再評估

| # | 功能 | 備註 |
|---|---|---|
| 16 | 多帳戶（自有/家人/公司） | 目前單帳戶就夠 |
| 17 | 即時報價 | 系統是日頻訊號，realtime 不是核心需求 |
| 18 | 自動下單（broker integration） | 法規與安全性風險高，明確不做 |
| 19 | 歷史交易回測重玩 | paper_portfolio_v2_champion 已涵蓋類似需求 |

---

## 明確不在範圍內

- ❌ **券商整合自動下單** — 風險過大，明確排除
- ❌ **即時 tick 報價** — 系統定位日頻
- ❌ **多使用者帳號** — 個人工具
- ❌ **複委託 / 美股** — 系統 universe 是台股
- ❌ MVP 階段不處理融資融券持倉（個股現股優先）

---

## 依賴與風險

### 依賴
- 既有 [backend/routers/portfolio.py](../../backend/routers/portfolio.py) — 借鑑但不改
- 既有 prediction_explain 邏輯（位於 app.py）— **必須驗證對任意 ticker 健壯**，不只 V2 Top30 picks
- 既有 [ml/reports/market_regime_latest.json](../../ml/reports/market_regime_latest.json) — 直接讀取
- 14 防呆規則（in CLAUDE.md）— 直接套用

### 風險
1. **prediction_explain 對非 V2 picks 的健壯性未驗證**：之前主要餵 V2 Top30，使用者可能持有冷門股或長尾股，需先做 smoke test
2. **ETF 完全未支援**（見 #8 票），MVP 必須誠實標註，不可假訊號
3. **個人持股資料隱私**：`my_holdings.db` 必須 .gitignore，且每次 commit 前確認無外洩
4. **功能 scope creep**：companion features 多達 13 個，**只做 MVP + #9/#10/#11 先收斂**，其餘等使用後再評估

---

## 排程

| 階段 | 內容 | 何時 |
|---|---|---|
| **MVP (#7)** | 核心 CRUD + 訊號 + 14 防呆 + **ETF 基本支援** | W19 (5/11-5/15) 接 #5 結論之後 |
| Companion P1 | #9 組合風控 / #10 news alert / #11 attribution | W20 (5/18-5/22) |
| **#8 ETF 進階訊號** | 折溢價 / 規模 / 追蹤誤差 / 配息（降 P3） | 視使用回饋再排 |
| Companion P2 | #12-#15 視使用回饋決定 | 開放排程 |

## Memory 更新需求

MVP 完成後，新增 memory: `project_my_holdings_feature.md`，記錄訊號分類規則、ETF 處理現狀、隱私約束。

---

## 對 PM 自己的提醒

- **不要再多塞 feature**。這份單已經 13 個 companion，半數是 nice-to-have，使用者要的是「每天看一眼知道要不要動」，不是 Bloomberg Terminal。
- MVP 跑兩週後讓**真實使用紀錄**告訴我們哪個 P1/P2 真的有用，再排優先級。
- ETF 資料補抓是真議題，但 W18 不該分心去做。
