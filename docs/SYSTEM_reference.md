# 系統參考(指令、架構、排程、資料口徑)

2026-10-18 自 `AGENTS.md` 逐字搬移(原本經 CLAUDE.md import 常駐載入,現在改為需要時再讀)。Claude 與 Codex 共用。

# Shared System Knowledge (Claude + Codex 共同遵守;single source, imported by CLAUDE.md)

## 常用指令

```bash
# 啟動 Web 伺服器（預設 port 8001）
start_web.bat
# 或直接
python app.py --port 8001 --reload   # 開發模式

# 每日資料更新 + 預測（非互動式，可排程）
python scripts/smart_update_auto.py
# 或手動指定步驟
python scripts/daily_pipeline.py
python scripts/daily_pipeline.py --predict-only
python scripts/daily_pipeline.py --retrain

# 訓練模型（v1/v2 分類/迴歸）
python run_train.py                   # 輸出寫 train_output.log

# T+1 次日動能模型訓練
python -m ml.train_t1                 # (或 scripts/train_t1_backtest.py)

# 參數掃描
python archive/scripts_retired_20261017/sweep_t1_params.py   # 2026-10-17 已封存(T+1 凍結為純研究),要用先搬回 scripts/

# 回測
python scripts/backtest_v4.py

# 資料回補（一次性）
python scripts/backfill_eps.py
python scripts/backfill_balance_sheet.py
python scripts/backfill_valuation.py
python scripts/backfill_tdcc.py
```

---

## 系統架構

### 資料流（三層）

```
Layer 1: 資料抓取  twstock.py / scripts/
    ↓   CSV 寫入日K資料/ 月營收/ 季報財務/ 法人快取/ 等目錄
Layer 2: 資料庫    backend/db/ingest.py → stock.duckdb
    ↓   DuckDB 表：daily_k, revenue, financial, tdcc, index_prices …
Layer 3: API/ML    app.py (FastAPI) + ml/ (LightGBM 預測)
```

### 目錄結構

| 路徑 | 說明 |
|------|------|
| `twstock.py` | 核心資料抓取腳本（日K/法人/融資/月營收/技術指標，Step 1–11）|
| `app.py` | FastAPI 入口，含 prediction_explain 邏輯、防呆層、快取 |
| `backend/` | FastAPI 子模組：routers、services、db |
| `backend/config.py` | 路徑常數、DuckDB 路徑、API 設定 |
| `backend/db/engine.py` | DuckDB 連線管理（thread-safe cursor pattern）|
| `backend/db/ingest.py` | CSV → DuckDB 匯入 |
| `backend/routers/` | `stocks` `market` `charts` `rankings` `scoring` `news` `t1` |
| `backend/features/` | 各面向特徵計算模組（technical/institutional/fundamental/…）|
| `ml/` | ML Pipeline |
| `ml/config.py` | 模型超參數、路徑、訓練窗口設定 |
| `ml/dataset.py` | `build_dataset()` / `build_latest_snapshot()` |
| `ml/dataset_t1.py` | T+1 次日動能資料集 |
| `ml/train.py` | Walk-forward LightGBM 訓練（v1 分類） |
| `ml/predict.py` | 載入最新模型 → 產生預測、組合防呆、SHAP 解釋 |
| `ml/predict_t1.py` | T+1 模型預測 |
| `ml/models/` | 訓練好的模型（`lgbm_*.txt` + `*_meta.json`）、快取 |
| `scripts/` | 自動化腳本：daily pipeline、回測、回補、發信 |
| `日K資料/` | 個股日K CSV（`{ticker}.csv`）|
| `stock.duckdb` | 主資料庫（DuckDB binary）|
| `paper_portfolio.db` | 紙上投資組合（SQLite）|

### 模型版本

- **v1**：多分類（DOWN/FLAT/UP），目標超額報酬 5 日，`ml/train.py`
- **v2**：迴歸（pred_return_20d），含風險調整分數，`ml/train.py`（同檔，依 config 切換）
- **T+1**：次日動能二元分類，獨立資料集 `ml/dataset_t1.py`，獨立訓練腳本

模型檔命名規則：`lgbm_{timestamp}.txt` / `lgbm_v2_{timestamp}.txt` / `lgbm_t1_{timestamp}.txt`。正式夜間流程與 Web 使用 `scripts/model_pin_registry.py` 驗證的 Champion defaults，依 `config/champion_pin_manifest.yaml`／registry 載入既有 pin；DataA 使用 tracker 指定的模型。裸 `ml/predict.py` loader 仍有最新檔案 fallback，不能把未套正式環境的獨立呼叫當成正式推論。正式推論、Web 解釋與儀表板須核對實際模型、來源快照及成功產物憑證，不能只看日期或檔名。

### 每日自動排程

`scripts/run_daily.bat` → `smart_update_auto.py` → `smart_update.py`
每個交易日 05:00 執行（Windows Task Scheduler），包含：資料更新、預測、紙上投資組合同步、驗證、發信。

**臨時休市（颱風假）自動處理**（2026-07-10 巴威颱風事故後建立）：
- 行事曆 = `scripts/taiwan_trading_calendar.py` 年度假日表 + `config/adhoc_market_closures.json` 動態覆蓋檔（單一真相源，可手動補登）。
- Phase-2 freshness gate 被擋時會先向 TWSE MI_INDEX 求證該日是否真無交易（stat=OK 但 data 全空 = 臨時休市），確認後自動寫入覆蓋檔並放行；求證失敗一律維持阻擋，不可把真資料缺口誤判成休市。
- `twstock.py::_drop_fake_flat_bars` 在日K寫入前擋掉 yfinance 休市日占位假棒（零量且四價全平）。
- TDCC 週頻遇週五休市：官方資料日期改落當週最後營業日（如 7/09 週四），系統按資料自帶日期存檔，無需特殊處理。
- **只有 Phase-2 會自動偵測休市**：颱風假若當天才公告（2026-10-23），早上 Phase-2 仍照舊曆產生當天名單，晚上 Phase-1 會把當天當交易日去抓，
  日K／估值／法人全部失敗並寄出資料缺口警報（誤報）。處理：先向 MI_INDEX 求證（當天 stat=OK 且所有表 data 皆空），再用
  `taiwan_trading_calendar.add_adhoc_closure(日期, 原因)` 登記；不需重跑 Phase-1（休市日沒有資料可抓），下一個交易日照常。
- **帳本遇休市日名單**：`entry_filter_tracker` 的主 lane、舊規則影子、MS 影子帳本只看名單當天 K 棒觸發，休市日的列由 `check` 自動標
  `void`（休市不計，原狀態寫進 exit_reason）；下一個交易日的名單用同一份資料另記一列，不漏算也不重複。
  rere 帳本依 source_date+ticker 去重、進場取訊號日之後第一根 K 棒收盤，休市日不需特別處理。

**財報完整性稽核**（2026Q1 殘檔凍結事故後建立，2026-07-16）：
- 事故：三種季報檔（eps_/financial_/bs_）在 5/15 申報截止**前**抓到少數早申報公司即被「檔案存在=完成」邏輯凍結，5/15~7/15 約 94% 股票的 EPS/PE/利潤率/PB 特徵失真；季度 EPS 與資產負債表原本**無任何排程更新**。
- 防護：`scripts/fundamentals_completeness_audit.py` 每晚 Phase-1 於 ingest 前執行（申報行事曆感知：月營收 10 日、Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31，含寬限），覆蓋率 < 前期 80% 即自動回補（heal）再告警，缺口併入 DATA GAPS 警報信。
- 三個抓取腳本（twstock step8、backfill_eps、backfill_balance_sheet）皆已加「列數 < 前季 80% 視為殘檔重抓」防護。

**TDCC 大戶/散戶口徑**（2026-07-16 六年錯置事故後 PM 核定，單一真相=`twstock.py::_summarize_tdcc`）：
- 級距為集保官方定義（1=1-999股 … 9=50-100張 … 15=1000張以上）；**散戶 = 級距 1-9（<100張）、大戶 = 級距 15（千張大戶）**，與 dashboard 大戶週增雷達、rere 側寫一致。
- 歷史教訓：持股分級曾被字串排序（1,10,11,…,2,3…）導致六年 summary 的大戶%實際加到 30-100 張級距（2330 顯示 2.63% vs 真值 85.01%）；分級欄位必須數值化後運算，彙整必須按欄名不可按欄位位置。
- TDCC 週特徵自 2026-07-17 起由 `ml/features/tdcc.py` 以 `W-FRI` 完整週格點計算：
  觀測到的週四假日資料保留實際生效日，真正缺週插入週五 NaN；`diff/pct_change` 不得跨缺口，
  `whale_acc_weeks` 遇缺口歸零，rolling slope 使用固定週位置。缺值保留給 LightGBM 處理，
  不得以 0 或跨 2-4 週差值假裝單週訊號。
- RETRAIN-02 影響報告為 `ml/reports/retrain02_tdcc_gap_impact_20260717.md`：43 個全市場缺週、
  1,893 檔訓練股票、530,182 個觀測週樣本；71.75% 至少一項 TDCC 特徵重算，
  309,134 個舊假數值轉為 unknown，7,438 個真實連續週前綴樣本逐值差異 0。

**除權息還原**（2026-07-15 原相除息誤判事故後建立）：
- 日曆 = `ml/data/ex_dividend_calendar.csv`（TWSE TWT49U + TPEx openapi 每日增量，Phase-2 步驟 `Update ex-dividend calendar`；歷史/上櫃補洞用 `--seed-finmind`）。
- 共用工具 `scripts/exdiv_utils.py`：持有期間報酬與停損判定必須用還原價（close + 累計權值息值），已接入 rere tracker 與 Champion 風險提醒信。
- **日K 仍保存官方未還原 OHLCV**；技術特徵以官方 `price_factor` 建立連續視圖，標籤以
  `label_factor` 計算經濟報酬，長停牌重啟會重置 rolling/lag/forward window。前置已完成，
  但 PM 明確核可前仍不得重訓。
- 訓練標籤自 2026-07-17 起由 `ml/corporate_actions.py` 用 `after_price / before_price`
  比例因子還原：close-based 視窗為 `(t, t+N]`，Open[t+1] 進場的 trade 視窗為
  `(t+1, t+20]`。V1/V2 excess 必須與官方 TWSE MFI94U 報酬指數同口徑；缺檔或缺交易日
  時 `build_dataset()` fail closed，不得單邊還原。官方指數於 Phase-1 與 Phase-2 均增量更新。
- RETRAIN-01 在 RETRAIN-03 source repair 後的最終影響報告為
  `ml/reports/retrain01_exdiv_label_impact_post_retrain03_20260717.md`：1,893 檔、
  1,477,036 個 20D 樣本，事件影響率 7.72%、平均修正 +3.66pp、無事件絕對標籤最大差異 0、
  V1 分類翻轉率 1.93%；事前估計比為 0.81x / 1.09x，未命中 >2x 停手條件。
- RETRAIN-03 正式稽核為 `ml/reports/retrain03_price_integrity_20260717.md`：2020 年後
  OHLC / 非正價格 / duplicate 違規均 0，公司行動日假跳幅 0，新未解釋跳幅 0。
