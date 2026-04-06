# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工作流程

1. **每次收到需求，先 commit 目前所有未提交的修改到 git**，避免當機後遺漏進度。
2. 操作過程中產生的新代碼或資料，也要在適當節點 commit。
3. 長時間任務（回補資料、訓練模型等）要定期回報進度。
4. 不要問 yes/no 問題，直接做。只在真正需要用戶決策時才問。
5. 遇到錯誤或超時自動重試，不要叫用戶手動執行。

## 溝通

- 使用繁體中文
- 簡潔回報，不囉嗦

---

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
python scripts/sweep_t1_params.py

# 回測
python scripts/backtest_v4.py
python scripts/backtest_ml_model.py
python scripts/backtest_recommendation.py

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

模型檔命名規則：`lgbm_{timestamp}.txt` / `lgbm_v2_{timestamp}.txt` / `lgbm_t1_{timestamp}.txt`，最新時間戳自動被 `ml/predict.py` 挑選。

### 每日自動排程

`scripts/run_daily.bat` → `smart_update_auto.py` → `smart_update.py`
每個交易日 05:00 執行（Windows Task Scheduler），包含：資料更新、預測、紙上投資組合同步、驗證、發信。

---

## ML 預測解釋 — 財務常識防呆規則

修改 `app.py` 的 `prediction_explain` 或相關解釋邏輯時，**必須遵守三層架構**：

### 第一層：模型計算層
- SHAP / pred_contrib 照常算，不動數值

### 第二層：財務常識防呆層（Domain Logic Guardrail）

**核心原則：事實的多空分類必須基於財務常識，不可被 SHAP 方向翻轉。**

強制規則：

1. **本業虧損原則上是利空，但要辨識轉機修復**
   - `operating_margin < 0` → 預設分類為 negative
   - 若同時滿足明確修復訊號（如 `eps_qoq > 0`、`revenue_yoy_latest > 15%`、`margin_trend > 0.03` 中至少 2 項），且 `operating_margin > -8%`
     → 應表述為「本業仍虧損，但處於修復期」，保留 negative 同時可補充 turnaround positive
   - 不可把單純虧損包裝為「低基期反轉空間」

2. **估值虛胖辨識**
   - `PE > 50` 且 `operating_margin < 0` → EPS 分母趨近零導致，分類為 negative
   - `PE > 50` 且 `EPS TTM < 1` 且本業虧損 → 同上，不可說「反映高成長預期」
   - 高 PE 只有在營收/營業利益同步高成長時才可能是真正的成長溢價

2b. **PE 虛胖豁免條款**（僅限 `operating_margin > 0` 的股票）
   - 當 `PE > 50` 且 `0 < EPS TTM < 1` 但本業有賺錢時，依序檢查：
     - 豁免 1：`revenue_yoy_3m_avg > 30%` → 營收動能豁免，標記轉機訊號
     - 豁免 2：`gross_margin > 20%` 且 `operating_margin > 0` → 本業獲利結構豁免
     - 豁免 3：`PB < 1.5` → 資產保護傘豁免，下檔有撐
     - 無豁免：維持原始「估值虛胖」警告
   - **本業虧損（operating_margin < 0）永遠不可豁免**

3. **低基期/業外收入過濾**
   - `EPS YoY > 500%` → 低基期效應，分類為 negative（非實質成長）
   - `operating_margin < 0` 且 `EPS YoY > 0` → 預設視為獲利來源可疑（業外收入），強制從利多移到利空
   - 但若 turnaround 訊號成立，應改寫為「虧損收斂 / 轉機修復」，不可直接當作實質獲利成長
   - eps_momentum 在本業虧損下不可直接稱為「成長動能加速」；turnaround 成立時可稱為「修復動能加速」

4. **利潤率下滑永遠是利空**
   - `margin_trend < -0.03` → 分類為 negative
   - 不可包裝為「跌勢接近尾聲」

5. **缺失資料不是投資理由**
   - NaN 特徵一律不顯示，不可用「模型根據歷史缺失模式判斷」當理由

6. **技術指標例外**
   - 技術面指標（RSI/MACD/KD 等）方向不固定，可由 SHAP 決定
   - 籌碼面買賣超有固有方向（買超=positive, 賣超=negative）

7. **流動性不足永遠是風險（硬擋）**
   - 近日成交量 < 500 張（500,000 股）→ 推薦**強制降級**為「觀望」
   - 流動性不足 = 執行風險，買賣價差大、不易出場

8. **異常暴跌不是買進理由（硬擋）**
   - 20 日跌幅 > 40% → 推薦**強制降級**為「觀望」
   - 模型看到的「超跌」可能只是反映暴跌事件，不代表反轉

9. **極端預測值代表模型失準（硬擋）**
    - `|pred_return_20d| > 30%` → 推薦**強制降級**為「觀望」
    - 20 天漲跌超過 30% 已極罕見，說明模型被異常資料誤導

9b. **ATR 報酬天花板（硬擋）**
    - 預測報酬不得超過 `ATR% × 10`（最低 10%）
    - 物理限制：股票的短期報酬受波動能力約束

10. **極端乖離率是回歸均值風險（硬擋）**
   - `price_vs_ma20 > +30%` 或 `< -30%` → 推薦**強制降級**為「觀望」
   - 回測驗證：乖離硬擋整體有效，移除後 Sortino 下降

11. **本業虧損（預設硬擋，轉機股例外）**
   - `operating_margin < 0` 且 turnaround 不成立 → 推薦**強制降級**為「觀望」
   - `operating_margin < 0` 但 turnaround 成立 → 不可列為「強力買進」，但可保留「建議買進」並加註修復型風險標籤
   - 回測驗證：對一般虧損股維持硬擋仍有效，轉機股則需交由修復訊號額外辨識

12. **產業集中度上限（組合防呆）**
   - 單一產業佔 Top N 不得超過 20%（2026-04 實戰驗證後從 30% 收緊）
   - 超過上限時，從該產業排名最低的開始替換為次佳不同產業標的
   - 回測驗證：MDD 改善 0.5%、Calmar +0.421，月均報酬僅少 0.04%
   - 實戰教訓：30% 上限時電子類佔 70% 持倉，空頭集體崩潰

14. **波段強制停損 -10%（硬擋）**
   - 持倉收盤報酬跌破 -10% → 強制平倉（status = stopped_out）
   - 不再等待 20 天到期，提前止血
   - 實戰教訓：270 檔持倉中 94 檔跌超 -10%，最深 -35%，無停損機制導致虧損失控

13. **籌碼頂部背離（硬擋）**
   - `chip_diverge_bear >= 1.0` → 推薦**強制降級**為「觀望（籌碼頂部背離）」
   - 股價漲 + 法人倒貨 + 散戶融資追買 = 主力出貨格局，追高風險極大
   - 底部籌碼背離（`chip_diverge_bull >= 1.0`）為純提示（💡），不降級

### 第三層：衝突報告層

- 當某面向的 SHAP group_dir > 0（模型看多）但個別事實 negative > positive 時 → 標註「⚠️ 訊號矛盾」
- `domain_warnings` 欄位彙整所有被防呆層攔截的矛盾
- **絕對不可以「先射箭再畫靶」**：不可以為了讓文字與模型結論一致，而把利空事實包裝成利多

---

## 開發規範

Commit 格式：`<type>(<scope>): <summary>`
- type: `feat` / `fix` / `chore` / `refactor` / `docs` / `test`
- 範例：`feat(ml): add t1 momentum predictor`、`fix(batch): handle empty csv`

過濾器 / 防呆層改動前，必須跑回測驗證效果，不可憑直覺調整。
