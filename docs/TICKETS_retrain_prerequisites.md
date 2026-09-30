# 工單：重訓前置三票（RETRAIN-01 ~ 03）

**總狀態（2026-07-17）**：✅ 三票實作與驗證全綠；⛔ 重訓仍鎖定，等待 PM 明確核可。

> **語意澄清（2026-09-23 審查核對後補記）**：此處「不得重訓」鎖定的是
> **啟動新一輪帶標籤修復的正式重訓 + 切換 production model selection / Champion pin**。
> 每週六 10:00 的 `TW_Stock_Weekly_Retrain` 排程（`daily_pipeline.py --retrain --retrain-v2-only`）
> 是**常設 artifact 產出工作**：只產新 `lgbm_v2_<date>` 檔供 shadow/研究比對，
> production 仍由 pin manifest / `model_selection.json` 鎖定，排程執行不構成違規。
> 9/12 排程重訓已核對：Champion 載入仍為 pinned v2.6 incident edition
> （stage1=20260508、ranker=20260509），`model_selection.json` 仍為
> legacy-v1-reference-20260516，**production 選型未被切換**。

**開單者**：PM（2026-07-17）
**執行者**：Codex（RD）
**背景**：原始工單開立時，除息標籤、TDCC 缺週與公司行動斷點都會把偏差重新烤進模型。
三票現已完成，但「前置完成」不等於「自動授權重訓」；下一步仍由 PM 決定啟動時機。
**規格依據**：`docs/SPEC_data_integrity_and_change_control.md`（變更分級、method lock、A/B 協定）

**共同要求**：
- 分級 **L2**（改資料語意，不碰 production 規則）→ 需：原始檔對帳 + 影響量化 + 測試 fixture
- 不得順手改 production guardrail / exit policy（那是 P0-A/P0-B，另票，需 PM 核可）
- 不得重訓（三票完成後由 PM 決定啟動時機）
- 每票獨立 commit；新知識同 commit 進 AGENTS.md 或 docs/

---

## RETRAIN-01：訓練標籤除息還原 🔴 最高優先

**狀態（2026-07-17）**：✅ 實作與驗證完成；影響量化 PASS。RETRAIN-03 修復後重算為
1,477,036 筆 20D 樣本、受影響 7.72%、平均位移 +3.66pp，分別為事前估計的 0.81x / 1.09x，
未命中 >2x 停手條件；無事件樣本逐筆不變。整合後全套 `483 passed`。

**問題**：`ml/target.py` 的 `forward_return` / `forward_return_20d` / `trade_return_20d`
全用未還原 close。量化結果（`ml/reports/exdiv_label_bias_20260715.md`）：9.5% 樣本的
20 日標籤窗跨除權息，平均低估 **-3.37pp**、45.2% 事件偏移 >3pp（足以翻轉 UP/DOWN 分類）、
62% 集中在 6-8 月。模型因此系統性學到「高殖利率股在除息季表現差」的假規律。

**Skill**：主要 = 無專屬（依 SPEC §2.1 L2 流程）；輔助 = `git-commit-after-change`；
完成後驗證 = 標籤分布對照報告 + pytest

### ⚠️ PM 已裁示的三個設計決策（RD 照做，不要自行改）

**決策 1：股利視窗邊界必須分兩種基準**

| 標籤 | 基準 | 可領到的股利視窗 | 理由 |
|---|---|---|---|
| `forward_return`(5d) / `forward_return_20d` | close[t] | **(t, t+N]** | 以 t 日收盤持有，t+1 起的除息都領得到 |
| `trade_return_20d` | Open[t+1] | **(t+1, t+20]** | ex-date 當天開盤已是除息參考價，**該日除息領不到** |

RD 不可用同一個視窗套兩者。邊界測試必須涵蓋「ex-date 恰為 t+1」與「ex-date 恰為 t+20」。

**決策 2：excess_return 的基準不對稱 → 採「雙邊還原」**

TWII 是**價格指數（未含息）**。若個股加回股利、TWII 不加 → 高殖利率股超額報酬被系統性
高估約當殖利率（而 V1 標籤正是 excess_return 的分類，會直接扭曲 V1）。

**PM 裁示**：`twii_close` 改用 **TAIEX 發行量加權股價報酬指數（含息，TWSE 官方有此指數）**，
與還原後的個股報酬同口徑比較。
- 若該指數取得受阻（來源不穩/歷史不足），**回報 PM，不可自行退回單邊還原**——
  單邊還原比兩邊都不還原更糟（引入新的系統性偏差）
- 過渡期允許：個股與 TWII **都不還原**（維持現狀）僅適用於 excess 類標籤，
  但 `trade_return_20d`（絕對報酬，無基準）必須還原

**決策 3：除權（配股）用比例還原，不可用加法**

日曆的 `stock_and_cache_dividend = before_price - after_price` 對**現金股利**是精確的，
對**配股**是加法近似。169 件 >15% 的大配股（如 3293 鈊象 2024-07-24 before=1465→after=715，
即 100% 配股被記成 750 元「股息」）用加法會產生數個百分點誤差。標籤是訓練根基，不可用近似。

**PM 裁示**：改用比例還原因子 `factor = after_price / before_price`，
還原報酬 = `close[t+N] × Π(factor of events in window)⁻¹ / close[t] - 1`
（或等價地把歷史價乘上累積 factor）。日曆已有 `before_price` / `after_price` 兩欄可直接算。
現金股利與配股用同一套比例法即可（現金股利的 factor 也是 after/before）。

### 驗收條件（缺一不可）

1. **零事件不變**：無除權息事件的股票，新舊標籤**逐筆完全相同**（浮點誤差 <1e-9）——
   這是最重要的迴歸測試，證明沒有誤傷 90.5% 的乾淨樣本
2. **邊界 fixture**：ex-date 恰為 t+1 / t+20 / t 當天各一個測試案例，斷言視窗歸屬正確
3. **大配股 fixture**：用 3293 鈊象 2024-07-24（100% 配股）驗比例還原正確、加法會錯多少
4. **影響量化報告**（存 `ml/reports/`）：新舊標籤的 UP/DOWN 翻轉率、受影響樣本佔比、
   平均標籤位移，與 `exdiv_label_bias_20260715.md` 的事前預測（9.5% / -3.37pp）對照——
   **若實測與預測差距 >2 倍，先停下回報 PM**（代表其中一份分析有錯）
5. `python -m pytest -q` 全綠

---

## RETRAIN-02：TDCC 特徵 gap-aware 週變化

**狀態（2026-07-17）**：✅ 實作與驗證完成；43 個全市場缺週已顯式 reindex，
530,182 筆樣本中 71.75% 位於需重新計算的週序列；無缺口前綴逐筆不變，資料完整性稽核全綠。
整合後全套 `483 passed`。

**問題**：TDCC 週檔有 **43 個缺口週**（假日移到週四出檔的週從未抓到；FinMind 歷史需付費，
PM 暫不採購）。`ml/features/tdcc.py` 的 `diff()` / `rolling(N)` 按**列位置**計算，
跨缺口時「單週變化」實際是 2-4 週變化，量級失真。這 14 個 TDCC 特徵正是本次重訓的理由之一，
不修等於帶病重訓。

**Skill**：主要 = 無專屬；輔助 = `git-commit-after-change`；完成後驗證 = 特徵分布對照

### PM 裁示的做法

**採 reindex 到完整週格點 + NaN**，而非「除以真實週數」：

1. 把每檔的 TDCC 序列 reindex 到**完整週格點**（缺口週填 NaN）
2. `diff()` 跨 NaN 自然產生 NaN（正確：缺資料就是不知道，不是「變化為 0」或「4 週當 1 週」）
3. `rolling(4/8/12, min_periods=...)` 自動按真實時間窗計算，`min_periods` 控制品質下限
4. `whale_acc_weeks`（連續累積週數）**遇缺口必須重置計數器**，不可跨缺口宣稱「連續」

**為什麼不用「除以真實週數」**：那是假設變化在缺口內線性攤平，等於捏造未觀測資料；
NaN 誠實表達「不知道」，且 LightGBM 原生處理 NaN。

### 驗收條件

1. **無缺口區段不變**：連續週的特徵值新舊完全相同（證明只影響缺口鄰域）
2. **缺口鄰域 fixture**：構造含缺口的假序列，斷言 `whale_pct_chg` 為 NaN 而非跨缺口差值、
   `whale_acc_weeks` 在缺口處重置
3. **影響量化**：受影響樣本佔比（預期 ≈ 43 週 × 鄰域 / 總週數）
4. 語意不變量檢查仍全綠（`fundamentals_completeness_audit.py`）

---

## RETRAIN-03：公司行動斷點還原

**狀態（2026-07-17）**：✅ 實作、source repair、下游 ingest 與驗證完成。

- 完整備份：`F:\stock_backups\日K資料_20260717_RETRAIN03`（2,002 檔；修復前已驗檔名覆蓋與抽樣 SHA-256）
- 官方真相源：TWSE / TPEx 歷史 OHLCV + 除權息、減資、面額變更與 ETF 分割官方參考價
- 修復後 source：2,002 檔、3,025,328 列；2020 年後 OHLC 不變量違規 0、非正價格 0、重複鍵 0
- 四個點名斷點：0050 / 8093 已還原到合理跳幅；5228 / 6546 的非正式市場舊列因官方 regular tape 不存在而移除
- 公司行動日調整後 >35% 假跳幅 0；相對 backup 新增的未解釋跳幅 0；未受事件樣本逐筆不變
- 長停牌 >30 日重新交易會重置 MA/RSI/ATR/lag 與 forward target，不跨停牌期捏造連續序列
- DuckDB 重匯：2,904,922 列、1,947 檔、重複鍵 0、OHLC 違規 0；Web 核心端點全數 200
- 稽核：`ml/reports/retrain03_price_integrity_20260717.md`；整合測試 `483 passed`
- 未啟動重訓、未改 model selection、未碰 protected model artifacts

**問題**：日K 有未還原的公司行動斷點，跨斷點的 MA/RSI/ATR/報酬**全部失真**，
同時污染特徵與標籤：
- **8093** 2025-10-16 +69.5%（減資，價格 ×5/3）— 個股，在訓練 universe 內
- **5228** 2021-02-22 +45.5%、**6546** 2020-11-09 +299.3% — 個股
- **0050** 2025-06-18 -74.8%（一拆四）— ETF，不在個股 universe，但 dashboard/benchmark 可能引用
- **8 檔 Close 落在 [Low, High] 區間外**（4741/6751/6762/5228/8093/4401/5223/4923，
  共約 105 列，2020-2024 舊還原價殘留，係數不一致）

**Skill**：主要 = 無專屬；輔助 = `git-commit-after-change`、`price-data-integrity-guard`
（記憶指標：日K 價格防護鏈，官方為真相源）；完成後驗證 = OHLC 不變量掃描

### PM 要求

1. **這票會改日K CSV（source of truth）→ 動手前先備份整個 `日K資料/`**（帶日期戳）
2. **以官方源為真相**：不可自行推算還原係數。TWSE/TPEx 有除權息與減資的官方參考價，
   `ml/data/ex_dividend_calendar.csv` 已有 before/after 價可算 factor；
   減資/分割若不在該日曆內，須另抓官方公告，**查不到就回報 PM，不可用價格跳幅反推**
3. **OHLC 不變量必須同時修**：還原後 `Low ≤ min(Open,Close) ≤ max(Open,Close) ≤ High`
   必須成立，8 檔區間外異常一併清除
4. **不可只修 Close**：O/H/L/C 必須用同一係數，否則製造新的不一致（這正是現有 8 檔的成因）

### 驗收條件

1. **全市場 OHLC 不變量掃描零違規**（含被修檔與未動檔）
2. **斷點消失**：4 個已知斷點的單日跳幅還原後落入合理範圍，且**未在別處製造新跳點**
3. **未受影響股票逐筆不變**
4. **下游重跑**：ingest → 特徵重算 → 抽樣比對技術指標（MA20/RSI/ATR）在斷點前後連續
5. 備份路徑寫進 commit message

---

## 完成後（PM 執行，RD 不要自己來）

三票已全綠 → **等待 PM 核可**啟動重訓 → **two-stage retrain gate**：
Stage 1/2 重訓 → CL3 → fixed-snapshot A/B → PM 驗收 → 才可升 production。
新模型**不自動升版**（`feedback_two_stage_retrain_gate`）。

## 執行順序

**01 → 02 → 03 已全部完成**。重訓仍須由 PM 明確解鎖；RD / agent 不得因本文件全綠自行啟動。
