# 台股量化選股系統技術規格

> 最後更新：2026-04-19
> 文件版本：v2.5-p0-gates
> 系統狀態：V2 production、V1 已回滾並作為風險輔助、T+1 research-only

---

## 1. 系統概述

本系統以 LightGBM 機器學習模型為核心，對台灣上市櫃股票產生 20 交易日前瞻選股建議。系統涵蓋資料抓取、特徵工程、模型訓練、推論、紙上投資組合追蹤、Web API、Cloudflare 遠端連線與每日排程通知。

目前 production 決策主軸是：

1. V2 迴歸模型預測 20D 超額報酬。
2. V1 分類模型作為風險輔助訊號，不再允許失控式 hard veto。
3. Domain guardrails 負責流動性、處置股、基本面、籌碼背離與異常報酬防呆。
4. 紙上帳本與 replay monitor 持續驗證 live 行為是否偏離 backtest 口徑。

截至 2026-04-19，最新有效交易資料為 2026-04-17。2026-04-18 與 2026-04-19 為週末，沒有台股交易日資料缺口。

---

## 2. 交易口徑

這是 backtest、paper portfolio、live prediction 必須共用的唯一口徑。

| 項目 | 規格 |
| --- | --- |
| 訊號日 | t |
| 進場價 | `Open[t+1]`，訊號日隔一交易日開盤價 |
| 出場價 | `Close[t+20]`，進場後第 20 個交易日收盤價 |
| 持有週期 | 20 個交易日 |
| 摩擦成本 | 0.4% round-trip，含證交稅、手續費與滑價 buffer |
| 停損 | production 目前關閉，`ENABLE_STOP_LOSS = False` |
| 投組大小 | Top 30 |
| 產業上限 | 單一產業不超過 Top N 的 20%，Top 30 等於最多 6 檔 |

任何策略、模型或帳本若偏離此口徑，都不得與 production 績效直接比較。

---

## 3. 資料契約

### 3.1 主要資料來源

| 類別 | 來源或路徑 | 最新合理狀態 |
| --- | --- | --- |
| 日 K | `日K資料/*.csv`、`daily_k` | 最新交易日應為最近一個台股交易日 |
| 大盤與國際指數 | `indices` | Phase 2 會在 05:00 更新 |
| 法人買賣超 | `法人快取/` | 每日夜間更新 |
| 融資融券 | `原始融資資料/` | 每日夜間更新 |
| 外資持股 | `外資持股/` | 每日夜間更新 |
| 估值 | `估值資料/` | 每日夜間更新 |
| 月營收 | `月營收/`、`revenue` | 最新月營收，目前到 2026-03 |
| 財報 | `季報財務/`、`financials` | 最新季報，目前到 2025Q4 |
| 集保 | `集保分散/`、`tdcc` | 週更新，目前到 2026-04-17 |

### 3.2 DuckDB 主表

| 表 | 用途 |
| --- | --- |
| `daily_k` | 個股 OHLCV、法人、融資券與技術指標 |
| `indices` | 大盤與國際市場指數 |
| `revenue` | 月營收與 YoY/QoQ 衍生特徵 |
| `financials` | 季報基本面特徵 |
| `tdcc` | 集保分散、散戶與大戶占比 |
| `stock_list` | 股票清單、最近價格與成交量 |

資料新鮮度檢查以 `scripts.smart_update_auto._check_data_freshness()` 為準；正常狀態應回傳空清單。

---

## 4. 模型架構

### 4.1 V2 迴歸模型：production 主策略

| 項目 | 規格 |
| --- | --- |
| 模型 | LightGBM GBDT regression |
| Target | `trade_excess_return_20d = Close[t+20] / Open[t+1] - 1 - TWII_20d` |
| 特徵數 | 約 242 個 |
| 訓練樣本 | 約 1,613,830 筆 |
| 推論 universe | 約 1,107 檔 |
| 訓練區間 | 2020-01-02 至 2026-03-18 |
| 驗證方法 | Walk-forward，4 年訓練 + 1 個月驗證 + 20 交易日 embargo |
| 前處理 | Winsorization + 產業相對標準化 |
| 主要超參數 | `num_leaves=31`、`learning_rate=0.05`、`feature_fraction=0.6`、`lambda_l1=0.1`、`lambda_l2=1.0` |
| 目前模型 | `lgbm_v2_20260418_172126.txt` |

V2 是 production 選股排序的主模型。所有調整都必須先確認 target、進出場口徑、摩擦成本與回測帳本一致。

### 4.2 V1 分類模型：風險輔助

| 項目 | 規格 |
| --- | --- |
| 模型 | LightGBM multiclass classifier |
| Label | `DOWN / FLAT / UP`，以未來短期方向分類 |
| 角色 | 風險輔助與 dual-track 訊號，不得單獨癱瘓 V2 Top30 |
| production 模型 | `lgbm_20260404_131437.txt` |
| production processor | `cross_sectional_zscore = False` |
| rejected 模型 | `lgbm_20260409_125845.txt` |
| rejected 原因 | V1 z-score production drift incident |

V1 的 production 責任是協助識別方向性風險，而不是取代 V2 排名。任何 V1 promotion 都必須通過 same-snapshot A/B gate。

### 4.3 T+1 次日動能模型：research-only

| 項目 | 狀態 |
| --- | --- |
| 角色 | 次日動能研究與輔助排序觀察 |
| 特徵數 | 約 70 個 |
| 已知問題 | AUC 約 0.553，但 target 與實際交易進場口徑仍未證明可轉成 alpha |
| production 權限 | 不得作為 V2/V1 promotion 或 incident closure 的依據 |

T+1 可以出現在 UI 或報表中作為觀察層，但在完成獨立研究、回測與 promotion gate 前，不得接管 production 選股或 veto。

### 4.4 Shadow 與 V4 Rank15

| 元件 | 角色 |
| --- | --- |
| `predictions_shadow_v23_*.csv` | 使用 shadow 規則生成觀察組 |
| `paper_portfolio_shadow_v23.db` | Shadow 帳本 |
| `predictions_v4_rank15_*.csv` | V3 Top30 經 T+1 排序後的 Top15 觀察組 |
| `paper_portfolio_v4_rank15.db` | V4 Rank15 觀察帳本 |

Shadow 與 V4 Rank15 都屬於觀察軌，不得直接等同 production。

---

## 5. 特徵工程

| 類別 | 約略數量 | 範例 |
| --- | ---: | --- |
| 技術指標 | 100 | MA、RSI、KD、MACD、ATR、布林通道、波動率 |
| 籌碼法人 | 40 | 外資、投信、自營商、法人累積、法人占量 |
| 基本面 | 25 | 營收 YoY/QoQ、EPS、ROE、ROA、毛利率、負債比 |
| 估值 | 10 | PE、PB、殖利率、EPS-based valuation |
| 市場環境 | 15 | TWII、VIX、S&P、SOX、匯率或相關市場因子 |
| 產業特徵 | 15 | 產業資金流、產業營收中位數、產業動能 |
| 集保 | 15 | 散戶占比、大戶占比、持有人數變化 |
| K 線型態 | 20 | 趨勢、突破、距高低點、型態特徵 |

Feature importance 顯示 V2 主要依賴基本面品質、營收變化、籌碼累積與部分技術趨勢特徵。Top gain 特徵包含 `roa_annualized`、`debt_ratio_trend`、`revenue_qoq`、`inst_accumulation`、`book_value_per_share`。

---

## 6. Guardrails 與投組規則

### 6.1 單股 hard downgrade

以下條件會將候選標的降級或排除，不得靜默繞過。

| 類型 | 規則 |
| --- | --- |
| 流動性不足 | 平均成交金額過低，例如低於 500 萬 |
| 異常漲幅 | 20 日漲幅過高，例如大於 40% |
| 報酬異常 | `abs(pred_return_20d) > 30%` |
| ATR 不合理 | 預測報酬明顯超過 ATR 可承受範圍 |
| 均線乖離 | `price_vs_ma20 > 30%` 或 `< -30%` |
| 基本面警示 | `operating_margin < 0` 等營運品質警示 |
| 籌碼頂部背離 | `chip_diverge_bear >= 2.0` 強制降級，1.0 至 2.0 軟警示 |
| Crash rule | 單日或短期極端跌幅防呆 |

### 6.2 2026-04-19 hotfix 後規則

| 規則 | 說明 |
| --- | --- |
| `DUAL_TRACK_DISAGREE` | V1/V2 不一致改為 soft gate，不允許 V1 單方面癱瘓 V2 Top30 |
| V2 強訊號 override | `pred_return_20d >= 8%` 可降低 V1 veto 權重 |
| 處置股 | 預設仍保守處理，但需建立反事實研究軌 |
| 籌碼頂部背離 | `chip_diverge_bear >= 2.0` 維持嚴格限制 |
| 乖離限制 | 追價風險以 `price_vs_ma20 <= 30%` 為主要上限 |
| Loose mode | 若 Top50 可用標的過少或 dual-track fail rate 過高，啟動降級檢查 |

### 6.3 投組層規則

| 規則 | 說明 |
| --- | --- |
| Top N | Production 為 Top30 |
| 產業上限 | 單一產業最多 20% |
| Replay monitor | 每日輸出 `ml/reports/daily_replay_monitor_latest.json` |
| Strategy health API | Web 層提供策略健康度與 replay 狀態 |

---

## 7. Backtest 與模型品質

### 7.1 V3_D trade-aligned backtest

區間：2024-01 至 2026-02，共 26 個月。

| 指標 | 數值 |
| --- | ---: |
| 累積報酬 | +76.7% |
| 月均報酬 | +2.30% |
| MDD | -8.3% |
| Sharpe | 1.80 |
| Sortino | 5.28 |
| Calmar | 3.34 |
| 月勝率 | 65.4% |
| 月換手率 | 約 79% |

### 7.2 Walk-forward 品質

| 指標 | 數值 |
| --- | ---: |
| 平均 Spearman IC | +0.026 |
| 平均 Top30-Bottom30 spread | +3.19% / 月 |
| IC > 0 月份 | 17/27，約 63% |
| Top30 平均實際報酬 | +0.93% / 月 |
| Top30 勝率 | 39.3% |

### 7.3 重要診斷結論

| 主題 | 結論 |
| --- | --- |
| Alpha vs TWII | 以 TWII 當 benchmark 時累積 alpha 約 -19%，顯示策略仍吃到部分市場 beta |
| Alpha vs 等權市場 | 改用等權候選池 benchmark 後，累積 alpha 約 +17.8%，benchmark 定義需固定 |
| 報酬集中度 | Top 3 月份貢獻約 50%，Top 10 個股貢獻約 55.1% |
| Decile | D10 預測均值 +5.05%，實際均值 +2.53%，但底部 decile 仍有反常報酬 |
| 摩擦成本 | 0.4% round-trip 尚可承受；若升至 0.8%，績效會明顯受壓 |

---

## 8. Live 與紙上帳本

### 8.1 Production paper portfolio

| 項目 | 狀態 |
| --- | --- |
| DB | `paper_portfolio.db` |
| 區間 | 2026-03-11 至 2026-04-17 |
| 已建倉 | 約 600 筆 |
| 正常到期 | 約 11 筆，平均 +20.50% |
| 停損到期 | 約 153 筆，平均 -12.06% |
| 未實現 | 約 424 筆 |
| 帳面報酬 | 約 -9.87% |

Production 帳本在 2026-03 至 2026-04 明顯受 crash regime、舊停損設定與 V1/V2 mismatch 影響，不可直接用來否定 trade-aligned backtest，但必須作為 live drift 監控證據。

### 8.2 V4 Rank15 paper portfolio

| 項目 | 狀態 |
| --- | --- |
| DB | `paper_portfolio_v4_rank15.db` |
| 已建倉 | 約 63 筆 |
| 停損到期 | 約 10 筆，平均 -11.15% |
| 勝率 | 早期樣本不足，暫不下結論 |

---

## 9. V1 Z-score Incident Closure

### 9.1 事件摘要

事件期間：2026-04-08 至 2026-04-17。
結案日期：2026-04-19。
狀態：Closed。

Production V1 曾部署 `lgbm_20260409_125845.txt`，其 feature processor 啟用 `cross_sectional_zscore = True`。這造成 V1 對 V2 Top30 建議產生系統性 veto，UP 訊號大幅崩塌，交易管線的可用建議數下降至極低水位。

### 9.2 根因

根因是 Feature Processor / Model Contract Mismatch。

Same-snapshot A/B 測試確認：cross-sectional z-score 不是找出抗跌強者，而是把同一截面中的絕對強勢動能壓扁，同時把部分弱勢股往中性拉。這會破壞 V1 作為方向性風險輔助的原始合約。

### 9.3 關鍵證據

A/B 測試使用同一份 DuckDB raw feature snapshot，對 2026-04-10 與 2026-04-13 進行雙軌推論。

| 指標 | 結果 |
| --- | ---: |
| 比對 rows | 2,207 |
| 平均 `prob_edge_delta` | -0.038756 |
| Old UP -> New DOWN | 221 / 226，97.8% |
| Top old_edge decile edge loss | 約 0.154921 |
| Top old_edge decile flip-to-down | 約 96.38% |
| `delta > 0` survivors | 573 筆，但主要來自 FLAT/DOWN，不是真正 bullish amplification |

### 9.4 Resolution

| 項目 | 決策 |
| --- | --- |
| Production V1 | 回滾至 `lgbm_20260404_131437.txt` |
| Cross-sectional z-score | 禁止用於 V1 directional/risk-veto role |
| Incident model | `lgbm_20260409_125845.txt` 保留作 autopsy，標記為 rejected incident model |
| Closure artifacts | `ml/reports/v1_snapshot_ab_test_latest.*`、`ml/reports/v1_incident_closure_20260419.md` |

### 9.5 Mandatory Promotion Gate

未來 V1 promotion 必須跑 `scripts/v1_snapshot_ab_test.py`，且至少檢查：

1. Same-snapshot old vs new prediction join。
2. Old UP -> New DOWN flip rate。
3. Top old_edge decile edge loss。
4. New UP count 是否崩塌。
5. V2 Top30 是否被 V1 系統性 veto。
6. Signal distribution 是否變成 all-DOWN 或近似 all-DOWN。

建議初始硬性閾值：

```python
assert flip_rate_up_to_down < 0.30
assert top_decile_edge_loss < 0.05
assert new_up_count >= 50
```

---

## 10. 自動化與排程

| 排程 | 時間 | 動作 | 腳本 |
| --- | --- | --- | --- |
| `TW_Stock_Daily` | 每個交易日 05:00 | 更新指數、T+1 retrain、產生預測、同步帳本、監控、啟動 Web、啟動 Cloudflare、寄 email | `scripts/run_phase2_morning.bat` |
| `TW_Stock_Nightly` | 每個交易日 23:00 | 停 Web/tunnel、更新台股資料、估值、EPS、新聞、TDCC、匯入 DuckDB | `scripts/run_phase1_night.bat` |
| `TW_Stock_Weekly_Retrain` | 週六 10:00 | 週訓練、回測、報告與 shadow 更新 | `scripts/run_weekly_retrain.bat` |

Phase 2 成功後應：

1. 啟動 Web server，預設 port `8001`。
2. 啟動 Cloudflare tunnel。
3. 擷取遠端 URL。
4. 等待 tunnel 穩定。
5. 寄出每日 email，包含遠端連結。

`TW_Stock_Daily_Backup` 是舊排程，不屬於目前 production automation。

---

## 11. Repository Map

| 路徑 | 用途 |
| --- | --- |
| `twstock.py` | 台股資料抓取 |
| `stock.duckdb` | 主資料倉儲 |
| `backend/db/ingest.py` | 匯入 DuckDB |
| `ml/target.py` | Target 定義 |
| `ml/dataset.py` | 特徵與 dataset 建立 |
| `ml/predict.py` | Production prediction |
| `scripts/train_v2.py` | V2 訓練 |
| `scripts/v1_snapshot_ab_test.py` | V1 promotion A/B gate |
| `scripts/backtest_v4_overlay.py` | V4 overlay backtest |
| `scripts/update_paper_portfolio.py` | 紙上帳本同步 |
| `scripts/smart_update_auto.py` | Phase 1 / Phase 2 自動化 |
| `app.py`、`backend/` | FastAPI / Web API |
| `ml/models/` | 模型與每日預測產物 |
| `ml/reports/` | 回測、監控、incident closure artifacts |

---

## 12. Release 與 Incident SOP

### 12.1 模型 promotion 前必做

1. 固定 target 與交易口徑。
2. 使用同一份 raw feature snapshot 做 incumbent vs candidate A/B。
3. 檢查 signal distribution、flip ratio、top decile loss 與 Top30 veto。
4. 對照 backtest、paper portfolio、live prediction 口徑。
5. 產出可歸檔的 `md/json/csv` artifacts。
6. 在 SPEC 或 incident note 記錄 promotion 或 rejection 原因。

### 12.2 Incident closure 必做

1. 不在同一個 incident 同時修 V1、V2、T+1。
2. 先固定切面，再做同源輸入 A/B。
3. 將 rollback、rejected model、artifact path 與後續防線寫入 closure note。
4. 確認 production schedule 與資料 freshness 不受 incident 影響。
5. 完成後 commit closure artifacts。

---

## 13. 已知問題與待辦

### P0

| 項目 | 說明 |
| --- | --- |
| DuckDB lock | Weekly retrain 仍可能被 Web/API process 持有 DuckDB lock 影響，需要強化 read-only/fallback/retry |

### 已收束

| 項目 | 狀態 |
| --- | --- |
| V1 promotion gate | `scripts/v1_snapshot_ab_test.py --assert-gate` 已可用 exit code 擋下不合格候選模型 |
| Pipeline status 分離 | `/api/pipeline/status` 頂層改為 daily production health，weekly retrain 狀態獨立放在 `weekly_retrain` |

### P1

| 項目 | 說明 |
| --- | --- |
| T+1 獨立研究 | 不與 V1 incident 混在一起，獨立驗證 target、entry、alpha 與 live utility |
| Prediction tracking | 歷史追蹤需全面改為 trade-aligned `Open[t+1] -> Close[t+20]` |
| Guardrail counterfactual | 處置股與籌碼背離在 2026-03/04 曾有高報酬，需研究是否改為分層 soft gate |
| Benchmark | 固定使用 TWII、等權 universe 或雙 benchmark，避免 alpha 解讀漂移 |

### P2

| 項目 | 說明 |
| --- | --- |
| Decile calibration | 底部 decile 反常報酬需重查 label noise 與 regime effect |
| 參數穩定性 | 系統性測試 Top N、持有檔數、friction、訓練窗長度 |
| UI 標籤 | 清楚標示 production、shadow、research-only，避免 T+1 被誤解為正式策略 |

---

## 14. 版本歷程

| 日期 | 版本 | 變更 |
| --- | --- | --- |
| 2026-04-19 | v2.5-p0-gates | 落地 daily health / weekly retrain status 分離，並將 V1 same-snapshot A/B 升級為可 fail 的 promotion gate |
| 2026-04-19 | v2.4-spec-cleanup | 整理 SPEC 結構，加入 V1 z-score incident closure、promotion gate、排程責任與待辦優先級 |
| 2026-04-19 | v2.3-guardrail-observability | V1 回滾、dual-track soft gate、處置股/籌碼 guardrail、replay monitor、Strategy Health API、DuckDB lock fallback |
| 2026-04-18 | v2.2-trade-aligned | 統一 `Open[t+1] -> Close[t+20]` 與 0.4% friction 口徑 |
| 2026-04-11 | v3.1 | 15 檔觀察組與 V2.2 shadow |
| 2026-04-09 | v3.0 | Production V1 drift 事件開始暴露 |
| 2026-04-08 | v2.3-shadow | Shadow 觀察模式 |
| 2026-03-18 | v2.1-balanced | 紙上投資組合與 20% 產業上限 |
