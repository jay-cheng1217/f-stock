# 2026-05-17 PM 決策單 → SA

- **發起人**: PM
- **收件人**: SA（系統分析）
- **發起日**: 2026-05-17
- **後續流程**: SA 收單 → 出設計文件 → PM 簽核 → SA 派工給 RD → RD 實作 → PM 驗收
- **PM 角色約束**: 本單只列「業務問題 + 邊界 + 驗收成功標準」，不指定技術方案；技術方案由 SA 決策
- **背景**:
  - INFRA-001 / RD-005 / RD-002 Stage A 已完工（commits 98cc7fd0 / aa66cb08 / 9d719101）
  - Stage1 pinned binary 重建後 sanity check 仍 FAIL，以 v2.6 incident edition 為 active baseline
  - 5/11~5/15 NAV residual：execution +1.07pp / exit policy +0.43pp / **unexplained -5.64pp**
  - RD-002 Stage A oracle: slippage-only upper bound +15.97pp，遠高於 Stage B kill switch +0.50pp
  - `my_holdings.db` 已可用，12 筆真實持股

---

## 暫停項 — INFRA-001 異地備份

- **狀態**: 暫停，等 user 提供 `MODEL_PIN_BACKUP_DIR`
- **禁止**: 不可用本機臨時目錄充當 offsite
- **重開**: user 給路徑 → 直接補跑既有 hook，不需 SA 介入

---

## 決策單 #1 → SA：Stage B ex-ante slippage proxy 路徑決策 (P1)

### 業務問題

Stage A oracle 證明 realized slippage 上界 +15.97pp，Stage B 現行 kill switch +0.50pp 在當前資料下「不該觸發 kill」。但 production 仍缺一個事前可觀測的 proxy 用來判斷單筆下單是否該降量 / 延後 / 跳過。在 baseline 重建尚未完成的窗口內，PM 不能等 baseline 也不能盲衝。

### PM 要 SA 決策的事項

1. **技術方案路線**：純 rule-based proxy / ML-based proxy / hybrid，三選一含理由
2. **候選 proxy 集合**：至少 3 個技術上可比的候選方案，含資料可得性評估
3. **評估指標**：相關係數 / MAE / tail-percentile error / 其他，由 SA 給統計學上合理組合
4. **採用條件**：在多少 sample 數、什麼樣的指標門檻下，才可從 design 推到 shadow，再從 shadow 推到 production
5. **與 baseline 依賴關係**：Stage B v2 是否必須等 Stage1 binary 穩定？若 baseline 半年內不穩定，是否有降級採用方案
6. **失敗路徑**：所有候選 proxy 都無法收斂時，SA 要建議「擴觀測欄位」還是「永久封存 Stage B」

### PM 邊界（SA 不可越線）

- 不改 Stage B production threshold +0.50pp（除非 SA 設計通過 PM 簽核 + RD 完整實作 + PM 驗收三關）
- 不擴張到 Stage C（exit-side slippage）
- 不直接 promote / shadow flip，本單只負責設計與離線評估

### 驗收成功標準（SA 交付物）

- [ ] 一份 `docs/designs/stage_b_slippage_proxy_v2_<asof>.md`，包含上述 6 項決策
- [ ] PM 讀完能回答：「為什麼選這條路線、什麼條件下會 promote、什麼條件下會放棄」
- [ ] 設計文件附 RD-ready 工作拆解（含 timebox 與依賴），但 RD 動工前需 PM 簽核

### Timebox

- SA 設計階段: 2 day
- SA 不可同時自己改 production code；如需離線驗證可請 RD 配合資料拉取，但決策權留在 SA

### Skill 對應（SA 參考）

| 階段 | Skill | 用途 |
|---|---|---|
| SA 設計 | 無 | 純架構決策 |
| SA 派工後 RD 實作 | `simplify` | RD 自我 review |
| PM 驗收前 | `champion-audit` | 確認設計不衝突 production gate |

---

## 決策單 #2 → SA：REQ-034-v3 持股風險清單資料源與分類架構決策 (P1)

### 業務問題

REQ-034-v3 原規劃 CSV MVP 過渡。`my_holdings.db` 已確認有 12 筆真實持股，user 決策跳過 CSV MVP 直接接 SQLite。但接 SQLite 涉及多個技術決策不該由 RD 自行決定，先由 SA 拍板：

PM 要的最終效果：**每日 06:30 寄出真實持股的 🔴🟡🟢 三類分類提醒**，重用既有 prediction_explain 與 14 防呆規則，不改閾值。

### PM 要 SA 決策的事項

1. **模組擺放位置**：新建 `scripts/daily_risk_cut_list.py` / 擴充既有 `smart_update_auto.py` / 擴充既有 `my_holdings` router，三選一含理由
2. **ETF 分類規則**：14 防呆是個股規則，ETF 不適用。SA 要決策 ETF 的最小子集規則（流動性、跌幅、產業上限等哪些適用）
3. **資料缺漏 fallback 政策**：當日 prediction CSV / unified_signals / DuckDB 任一缺漏時，是「跳過今日 + log」、「重試 N 次」、「降級為純價格訊號」，由 SA 決策
4. **Email 失效降級**：寄信失敗時是 retry / 寫 fallback file / Slack webhook，由 SA 決策
5. **與既有「我的持股 MVP」(票 #7) 邊界**：本單只負責每日 batch 提醒，不做 web review；SA 須畫清楚 backend 是否共用 `/api/my_holdings/{id}/review` 邏輯

### PM 邊界（SA 不可越線）

- **不改 14 防呆閾值**（依 [feedback_threshold_calibration_pm_approval](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_threshold_calibration_pm_approval.md)，調整 ±5pp 須 PM 簽核）
- 不擴張 `my_holdings.db` schema
- 不做自動下單
- 不發空信、不發假訊號

### 派工前強制要求（依 [feedback_spec_dry_run_before_dispatch](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_spec_dry_run_before_dispatch.md)）

SA 設計完成後、派工給 RD 之前，**必須先請 RD 用 5/16 收盤資料對 12 筆真實持股 dry-run expected output**，並由 PM 確認 dry-run 涵蓋：

- 至少 1 筆 🔴 觸發樣本
- 至少 1 筆 🟡 觸發樣本
- 持股 ticker 不在 prediction universe 的 fallback
- asset_type=etf 的處理結果

dry-run 不過 → SA 退回重設計，不進入 RD 實作階段。

### 驗收成功標準（SA 交付物）

- [ ] 一份 SA 設計文件，含上述 5 項決策 + RD-ready 工作拆解
- [ ] PM 讀完能回答：「每天 06:30 寄什麼、缺資料怎麼辦、ETF 怎麼分」
- [ ] dry-run output 經 PM 簽核

### Timebox

- SA 設計階段: 1 day
- dry-run 階段: 0.5 day
- RD 實作 by SA 派工後另計，本單不含

### Skill 對應（SA 參考）

| 階段 | Skill | 用途 |
|---|---|---|
| SA 設計 | 無 | 純架構決策 |
| dry-run | 無 | RD 出資料，PM 看結果 |
| RD 實作 | `simplify` | 完工前自我 review |
| PM 驗收 | `review` | PR 階段 |

---

## 決策單 #3 → SA：NAV unexplained residual -5.64pp 分析方法決策 (P0)

### 業務問題

RD-005 NAV residual 已拆出 execution +1.07pp / exit policy +0.43pp / **unexplained -5.64pp**。unexplained 大於已知兩塊加總，是目前最大未解項。依 [feedback_nav_attribution_required](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_nav_attribution_required.md)，這塊不解開所有後續 alpha 缺口判斷會被汙染。

User 已給優先順序：**factor exposure → timing → 樣本外波動**。PM 不指定統計方法，請 SA 拍板分析架構。

### PM 要 SA 決策的事項

1. **Factor model 選型**：單 beta CAPM / 多 factor / 產業 dummy regression，含選擇理由（樣本只有 5 個交易日是關鍵限制）
2. **樣本不足對策**：5 個交易日 NAV 自由度極低，SA 要決策是否：
   - 拉長窗口（例如 4/23 起，但要說明可比性）
   - 用 portfolio-level 還是 holding-level regression
   - 是否需要 bootstrap / 其他補強
3. **Timing 拆分方法**：intraday / overnight / rebalance 三類的拆法是否可行，反事實 baseline 怎麼定
4. **OOS 信賴區間建立法**：用哪個窗口的 OOS、bootstrap / 解析解 / 蒙地卡羅，由 SA 拍板
5. **「unexplained = noise」門檻**：在什麼條件下 SA 可下此結論（CL3 等級）；什麼條件下必須回到階段 A/B 找未捕捉結構
6. **依賴排序**：階段 A 是否真的須先做？或是 SA 認為 timing 影響更大可先動

### PM 邊界（SA 不可越線）

- 不改 production model / production gate / attribution buckets schema
- 不重訓 Champion（依 [feedback_post_incident_out_of_scope_lock](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_post_incident_out_of_scope_lock.md)，fresh retrain 仍 out of scope）
- 不基於本研究結果直接調 sector cap / 14 防呆閾值
- 不下「unexplained = noise」結論除非有統計支撐

### 派工前強制要求（依 [feedback_validate_with_existing_data_first](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_validate_with_existing_data_first.md)）

SA 派工 RD 跑分析前，先用 4/23~4/29 資料 mock 一次 SA 選定的 factor regression，確認：

- 資料齊全（factor return series 抓得到）
- 自由度足夠（樣本數 vs 參數數）
- 顯著性可區辨

mock 失敗 → SA 改方法或回 PM 報「current data insufficient」+ 補資料提案，不可硬上 production analysis。

### 驗收成功標準（SA 交付物）

- [ ] 一份 SA 設計文件，含上述 6 項決策 + RD-ready 分析拆解
- [ ] mock 結果經 PM 確認後才派工 RD 跑正式 analysis
- [ ] PM 讀完能回答：「為什麼用這套 factor model、樣本不足怎麼處理、unexplained 何時可標 noise」

### Timebox

- SA 設計階段: 1 day
- mock 階段: 0.5 day
- RD 實作 by SA 派工後另計

### Kill criteria（依 [feedback_bounded_experiment_kill_criteria](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_bounded_experiment_kill_criteria.md)）

- SA mock 後若所有 factor 都無解釋力 → SA 須回 PM 報「需擴觀測欄位 / 拉長窗口」，不可硬上
- 階段 A 完成後 `residual_after_factor` 絕對值仍 ≥ 5.0pp → SA 須回 PM 確認是否要繼續 B/C 階段

### Skill 對應（SA 參考）

| 階段 | Skill | 用途 |
|---|---|---|
| SA 設計 | 無 | 純方法論決策 |
| mock 驗證 | 無 | RD 出 mock，PM 看結果 |
| RD 跑 analysis | `simplify` | code review |
| PM 驗收 | `champion-audit` | 確認 attribution 不衝突 production baseline |

---

## SA 處理順序建議（PM 角度）

| 排序 | 決策單 | 為什麼 |
|---:|---|---|
| 1 | #3 | unexplained -5.64pp 是最大未解項，SA 設計先行；mock 卡住才回頭 |
| 2 | #2 | SA 設計可並行，dry-run 後等 PM 簽 |
| 3 | #1 | design only，不卡 production；可最後排 |

SA 可依自身工作量重排，但需在收單後 24 小時內回 PM 排程確認。

## 明確排除（任何一單都不可碰）

- 14 防呆閾值
- Stage B kill switch +0.50pp
- Champion fresh retrain
- 14 防呆 schema / attribution buckets schema
- 用本機臨時目錄假裝 INFRA-001 offsite copy 完成
- 在 #3 unexplained 結論不清楚前，調整 production allocation

## Memory 更新需求（PM 自行追蹤，不派給 SA/RD）

- 任一單 SA 設計通過 PM 簽核時 → 更新 [project_post_incident_active_tickets](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_post_incident_active_tickets.md) 加上 SA 設計階段狀態
- 任一單 RD 完工驗收後 → 更新對應 project memory
- INFRA-001 offsite backup 補完 → 從 active 移到 done

## 流程備註

- 本單為 **PM → SA** 決策請求，不是 PM → RD 的實作 spec
- SA 收單後出設計文件回 PM，PM 簽核後 SA 才可派工 RD
- RD 動工前若涉及 dry-run，必須先取得 PM 簽核
- 任何決策若 SA 認為超出本單邊界，須先回 PM 開新單，不可自行擴張

---

## 2026-05-17 PM 簽核回函（SA 提交 mock / dry-run 後）

### 簽核 #3 RD-005 mock validation — **PASS，允許進正式 closure-window analysis**

SA 提交內容：
- Mock 窗口: 4/23~4/29，effective holding-day obs = 95
- 參數數 6 / 自由度上限 19（符合 PM 派工單「自由度足夠」要求）
- Factor rows 無缺漏
- Residual after factor = **-11.25%**
- Bootstrap 90% interval = **-17.31% ~ -5.85%**（不跨零）
- Sign stability = 100%
- SA 判讀：方法可跑、資料足夠、殘差非 noise-like

PM 簽核判斷：
- 派工單三項硬要求全部達標：資料齊全 ✓、自由度足夠 ✓（95 obs vs 6 param）、顯著性可區辨 ✓（CI 不跨零）
- Bootstrap CI 完全在負值側 + sign stability 100% → 殘差非偶發
- Mock 殘差 -11.25% 與 5/11~5/15 目標窗口 -5.64% 量級不同屬正常（不同週、不同 composition），mock 驗證的是「方法 + 資料」而非「重現特定殘差值」

**PM 簽核條件**（SA 派 RD 跑正式 closure-window analysis 必須遵守）：

1. **方法論一致性鎖死**：正式 run 必須使用 mock 完全相同的 factor model spec / regression form / bootstrap 程序，不可中途換 spec；如 SA 認為需調整，先回 PM 加開單
2. **Pre-registration**：SA 在派工 RD 前，先把 mock 用的 factor 清單、回歸式、bootstrap iterations 寫進 `docs/designs/rd005_residual_deepdive_method_lock_<asof>.md`，PM 留底
3. **窗口擴張禁令**：正式 run 範圍限定 5/11~5/15 closure window，不可順手擴到 5/4~5/15 之類「順便看一下」；要擴窗口先回 PM 開新單
4. **結果交付規格**：產出至少包含 `residual_after_factor`、bootstrap CI、sign stability、每個 factor 的 contribution pp 與 active exposure
5. **不下行動結論**：本次 RD 交付只負責出數字，不得在報告中建議調整 production weight / sector cap / 防呆閾值；後續行動 PM 另開單

→ **SA 可派工 RD 跑 5/11~5/15 正式 closure-window analysis。**

階段 B/C 仍照原派工單 kill criteria 處理（A 後 residual_after_factor 絕對值 ≥ 5.0pp 時 SA 須回 PM 確認是否續做）。

---

### 簽核 #2 REQ-034-v3 dry-run — **CONDITIONAL PASS，允許進正式 batch/email 實作**

SA 提交內容：
- 12 筆真實持股 dry-run 結果：red 6 / yellow 0 / green 6
- 未寄信 / 未改 DB / 隱私欄位（成本、股數）已省略
- 真實資料未覆蓋：yellow、ETF、prediction fallback
- RD 補 synthetic boundary appendix，明確不算入 real holdings counts
- SA 詢問 synthetic boundary 是否足以通過 dry-run gate

PM 簽核判斷：
- 派工單原驗收條件要求覆蓋 red / yellow / ETF / fallback 四種 case
- 真實資料先天無法覆蓋 ETF（user 持股組合不含 ETF）與 fallback（當日 prediction 完整時不會觸發）
- 強制真實覆蓋是不合理要求（user 不會為了驗收去買 ETF），但完全放掉等於沒驗 yellow/ETF/fallback 的分支
- Synthetic boundary appendix 在「明確標註 + 與真實數字分開列」的前提下是合理替代

**PM 簽核條件**：

1. **Synthetic appendix 命名與隔離規範**：synthetic 部分必須在獨立檔案或獨立 section，檔名 / section 名含 `synthetic` 字樣，且每個 synthetic case 標註對應的真實覆蓋缺口（例：`synthetic_case_etf_0050: 真實資料缺 ETF 覆蓋`）
2. **Real backfill 強制條件**：未來任一日真實 12 筆持股自然觸發 yellow / ETF / fallback 時，該觀測必須被保留為 backfill validation log（路徑：`logs/req034_real_coverage_backfill.log`），PM 每月抽看一次
3. **red 6/12 觀察項**：當下真實 6 筆紅燈占比 50%，數字偏高。SA 派工 RD 實作前，先補一份「6 筆紅燈逐筆觸發規則 list」（不含成本/股數，只列 ticker + 觸發規則名），由 PM 確認非規則過敏；此 list 不阻塞實作，但須在 RD 動工同步交付
4. **隱私延伸**：正式 email artifact 必須延續 dry-run 的隱私約束（不寄成本、不寄股數），只寄 ticker / 訊號燈號 / 觸發規則名 / 簡述
5. **降級 fallback 必須含「全部為 synthetic 未驗」說明**：上線首日的 email 開頭加註「fallback 路徑為 synthetic-tested，首次真實觸發後將回填驗證」，user 知情下使用
6. **不改 14 防呆閾值**（依 [feedback_threshold_calibration_pm_approval](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_threshold_calibration_pm_approval.md)）

→ **SA 可派工 RD 做正式 batch/email artifact 實作；上線後第一個月為 real-coverage backfill 觀察期。**

---

### 派工順序更新（簽核後）

| 排序 | 工項 | 負責 |
|---:|---|---|
| 1 | RD-005 method lock doc | SA（PM 簽核後派 RD） |
| 2 | REQ-034-v3 紅燈逐筆 list | RD（同步 SA 派工） |
| 3 | RD-005 5/11~5/15 正式 analysis | RD（method lock 簽核後） |
| 4 | REQ-034-v3 batch/email 實作 | RD（紅燈 list 同步交付後） |

#1 Stage B proxy 仍 P1，SA 收到設計交付後另行簽核，不在本回函內。

### 跨單原則重申

- 任一單 RD 在實作中發現結果與 mock/dry-run 嚴重偏離，必須停手回 SA → PM，不得自行擴張 scope 或調參掩蓋
- INFRA-001 offsite backup 仍暫停等 `MODEL_PIN_BACKUP_DIR`

---

## 2026-05-17 PM 驗收回函（SA 第二輪交付）

### 驗收 REQ-034-v3 三件式交付 — **全面通過，可上線**

交付物：
- [ml/reports/req034_v3_red_trigger_list_20260517.md](../../ml/reports/req034_v3_red_trigger_list_20260517.md)
- [ml/reports/req034_v3_synthetic_boundary_20260517.md](../../ml/reports/req034_v3_synthetic_boundary_20260517.md)
- [scripts/send_my_holdings_risk_cut_report.py](../../scripts/send_my_holdings_risk_cut_report.py)

PM 條件逐項驗收：

| 條件 | 結果 | 證據 |
|---|---|---|
| 1. synthetic 獨立檔 + 命名含 `synthetic` 字樣 | ✓ | 獨立 .md，三筆 case 都以 `synthetic_case_*` 開頭，且 appendix 明文「Synthetic rows are not real holdings」「not included in red/yellow/green counts」 |
| 2. Real backfill 強制條件 | ✓ | [send_my_holdings_risk_cut_report.py:155-186](../../scripts/send_my_holdings_risk_cut_report.py#L155-L186) `append_real_coverage_backfill()` 覆蓋 yellow / ETF / fallback 三類，寫入 `logs/req034_real_coverage_backfill.log` |
| 3. 紅燈逐筆規則 list（PM 確認非過敏） | ✓ | 6 筆紅燈以 BELOW_BREAKEVEN（4 筆）/ LOW_LIQUIDITY（3 筆）/ STOP_LOSS_10PCT / PREDICTION_EXPLAIN_HARD_GUARD 為主，符合 my_holdings spec 「跌破 breakeven / 流動性不足 / 14 硬擋」原則，**非規則過敏** |
| 4. 隱私延伸（不寄成本/股數） | ✓ | [send_my_holdings_risk_cut_report.py:88](../../scripts/send_my_holdings_risk_cut_report.py#L88) HTML footer 明列「Private cost/share fields are omitted」；trigger list 只列 ticker + rules |
| 5. Synthetic-tested 首日提示 | ✓ | [send_my_holdings_risk_cut_report.py:29-32](../../scripts/send_my_holdings_risk_cut_report.py#L29-L32) `SYNTHETIC_NOTE` 為 default，僅在 PM 明確下 `--no-synthetic-note` 才撤除 |
| 6. 不改 14 防呆閾值 | ✓ | HTML footer 明列「14 guardrail thresholds unchanged」，且 script 只 read-only 載入 dry-run summary |

PM 額外觀察（非阻塞，記錄於此）：
- **8341 三紅同發**（STOP_LOSS_10PCT + BELOW_BREAKEVEN + LOW_LIQUIDITY）是當前持股最危急；REQ-034-v3 上線後第一封信即會點名，符合預期
- **紅燈占比 50%（6/12）**：與當前 V2 Champion 在中小型股流動性收縮環境下的結構性現金等待期一致（見 [feedback_large_cap_bull_market_behavior](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_large_cap_bull_market_behavior.md)），不視為過敏
- **send_email 例外處理**：[send_my_holdings_risk_cut_report.py:189-197](../../scripts/send_my_holdings_risk_cut_report.py#L189-L197) artifact 先寫入再嘗試寄信，SMTP 缺設定時走 graceful skip，符合「不發空信」原則；SMTP 寄信失敗（連線/認證錯誤）會 raise，但這是 loud failure，user 看 log 會知道，可接受

→ **REQ-034-v3 可上線。** 排程加進 06:30 排程。第一個月為 real-coverage backfill 觀察期；PM 每月第一個工作日抽看 `logs/req034_real_coverage_backfill.log`。

---

### 驗收 RD-005 formal closure-window analysis — **方法達標但放行 B/C 不通過，需補 reconciliation**

交付物：[ml/reports/rd005_residual_deepdive_20260517.md](../../ml/reports/rd005_residual_deepdive_20260517.md)

PM 鎖死條件逐項驗收：

| 鎖死條件 | 結果 | 證據 |
|---|---|---|
| 1. 方法論一致性（factor spec / regression / bootstrap 不可變） | ✓ | 與 mock 同 factor 集（market_0_5_load + sector_total_0_5_load + sector groups），同 bootstrap 程序 |
| 2. Pre-registration method lock doc | ⚠️ 未見 | PM 簽核時要求 `docs/designs/rd005_residual_deepdive_method_lock_<asof>.md` 留底；本次未交付，**列為 follow-up 缺項** |
| 3. 窗口擴張禁令（5/11~5/15） | ✓ | 報告 line 4: Window 2026-05-11 ~ 2026-05-15 |
| 4. 結果交付規格 | ✓ | residual_after_factor / bootstrap CI / sign stability / 每 factor contribution + active exposure 全達 |
| 5. 不下行動結論 | ✓ | 報告 line 8: 「Action conclusion: none; PM must open a separate ticket for any production action」 |

統計結果：
- residual_after_factor = **+6.04%**，bootstrap CI +2.98% ~ +9.58%（不跨零），sign stability 100%

**Kill criteria 命中**：|+6.04%| ≥ 5.0pp，依派工單條件 SA 不可自動續跑 B/C，已正確回 PM。

PM 不放行 B/C 的理由（**新發現的盲點**）：

1. **Reconciliation 缺口**：原始 NAV unexplained 為 **-5.64pp**（5/11~5/15）；本次 holding-level residual_after_factor 為 **+6.04%**（同窗口）。**兩個數字符號相反、量級相近**，但 SA 未在報告中說明兩者關係。
   - 可能解釋：兩個指標衡量不同東西（NAV 層 vs holding-day 層、含 cash 層 vs 不含 cash 層）
   - 也可能：portfolio 層 idiosyncratic alpha 是正的 +6.04%，但 NAV 層因 allocation / cash drag / 進出場時點等因素變成 -5.64%
   - **無 reconciliation 即進 timing B/C，等於不知道在解釋哪個指標**

2. **單檔 6419 貢獻 +3.40%**（占 residual +6.04% 的 **56%**）：殘差不是均勻分散，是被一檔吃掉。任何 timing/OOS analysis 在 ticker 集中度未拆解前都會被這檔主導。

3. **Mock 殘差符號 vs formal 殘差符號相反**：mock (4/23~4/29) = -11.25%，formal (5/11~5/15) = +6.04%。同 method、同模型、不同週，殘差完全翻向。這代表 residual_after_factor 是強烈窗口敏感指標，可能不適合直接用來推 timing/OOS 分支。

**PM 派 SA 下一輪工作（不開新單，本單延伸）**：

**簽核 A-reconciliation** — SA 必交：
- A1. **method lock doc 補件**：補交 `docs/designs/rd005_residual_deepdive_method_lock_20260517.md`（簽核 #3 鎖死條件 2，目前未交付）
- A2. **指標 reconciliation 報告**：明確說明 holding-level residual_after_factor (+6.04%) 與 NAV-level unexplained (-5.64%) 的數學關係，回答以下任一：
   - 兩者衡量不同對象，請各自定義並標示適用場景
   - 兩者可一階對齊，請列出橋接式（如：NAV unexplained = holding residual × portfolio weight - cash drag - rebalance timing - 其他）
   - 兩者不該被相提並論，請說明為何 -5.64pp 目標仍未被解釋
- A3. **6419 idiosyncratic deepdive**：拆解 6419 為何貢獻 +3.40%（事件 / news / 業績 / 短線資金 / 異常），確認是否單一事件造成而非結構性
- A4. **Window sensitivity note**：mock 與 formal 殘差符號相反需被解釋（不是要 SA 改方法，是要 SA 標註 residual_after_factor 是強烈窗口敏感指標，未來引用此數字必須註明窗口）

完成 A1~A4 → PM 再決定 B 階段（timing）/ C 階段（OOS）是否啟動、以哪個指標為準。

PM 不開新單，因 RD-005 派工單原 scope 涵蓋階段 A 的「完整交付」；reconciliation 屬於 A 階段未交完整，非 scope 擴張。

### Timebox

- A1~A4: SA 1 day，不卡其他工項
- 完成後 PM 簽核同單再寫一段，本單持續累積 audit trail

### 明確排除（延續）

- 不啟動 B/C 直到 A 階段補完
- 不基於 +6.04% 或 -5.64% 任一數字調 production allocation
- 不重訓 Champion
- 6419 deepdive 結果**不可**作為加倉 / 減倉 / 換股 production 決策依據；只用於 attribution 解釋

### Memory 更新

- REQ-034-v3 上線後 → 更新 [project_req034_daily_risk_cut_list](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_req034_daily_risk_cut_list.md)，標註資料源切換為 SQLite + synthetic boundary 機制 + real backfill log
- RD-005 A 階段 reconciliation 完成 → 更新 [project_post_incident_active_tickets](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_post_incident_active_tickets.md)，加註 holding-residual / NAV-unexplained 雙指標關係

---

## 2026-05-17 PM 驗收回函（SA 第三輪交付）

### 驗收 REQ-034-v3 06:30 排程上線 — **APPROVED，附 1 項 reliability follow-up**

PM 端 PowerShell `schtasks /Query` 驗證：

| 欄位 | 值 | 判斷 |
|---|---|---|
| TaskName | `\TW_Stock_My_Holdings_Risk_Cut_0630` | ✓ |
| Status | Ready | ✓ |
| Next Run Time | 2026/5/18 06:30:00 | ✓ |
| Logon Mode | **Interactive only** | ⚠️ **Reliability risk** |

2026-05-16 dry-run 12 筆 / red 6 / yellow 0 / green 6，未寄信 ✓，與 SA 第二輪交付一致 ✓。

**Reliability follow-up（不阻塞上線）**：

「Interactive only」代表 user 未登入 Windows 時 06:30 排程不會跑。對照 CLAUDE.md 既有 daily pipeline（05:00 排程）的執行模式，user 似乎習慣電腦保持登入，但 PM 仍要求：

- SA 在 24 小時內確認其中一條路徑：
  - 路徑 1: user 確認 24/7 保持 Windows 登入，本機接受 Interactive only
  - 路徑 2: 改用 `schtasks /Change` 加 `/RU` 或重建任務並指定 `Run whether user is logged on or not` + 加密憑證存放
- 路徑 2 涉及帳號憑證儲存，SA 須先給 PM 路徑選型理由再執行，不可自動切換
- 在切換完成前，PM 視為「06:30 排程已上線但仰賴登入狀態」，首週若漏一次需檢視原因

→ **REQ-034-v3 上線生效。** 首月 real-coverage backfill 觀察期照舊；同時觀察是否有 missed-run 事件。

---

### 驗收 RD-005 A1-A4 reconciliation backfill — **APPROVED，但 B/C 仍 HOLD；新增 A5 sub-task**

交付物：[ml/reports/rd005_reconciliation_backfill_20260517.md](../../ml/reports/rd005_reconciliation_backfill_20260517.md)、[docs/designs/rd005_residual_deepdive_method_lock_20260517.md](../../docs/designs/rd005_residual_deepdive_method_lock_20260517.md)

| 項 | 內容 | 判斷 |
|---|---|---|
| A1 method lock doc | 完整 pre-registration（input frame / factor spec / residual definition / bootstrap procedure / stop conditions / prohibited conclusions） | ✓ retroactive backfill 達標 |
| A2 reconciliation | 明確說明 holding-level vs NAV-level 為「different diagnostics」，列出 bridge components | ✓ 解釋達標，但 **內藏 A5** |
| A3 6419 deepdive | INTRADAY_CHASE / entry slippage +9.85% / target weight +12.68% / window return +29.24% / 量能 4.24x；結論 idiosyncratic concentration，**非 factor exposure** | ✓ 並且結論 actionable |
| A4 window sensitivity | mock -11.25% vs formal +6.04% 符號相反確認，標註 residual_after_factor 為窗口敏感指標 | ✓ |

**新發現 A5（PM 在 A2 中抓到，SA 未提報）**：

A2 reconciliation 報告 line 21：「**Ledger reconciliation error: +9.6212%**」

bridge 各分量總和（gross/cash -4.15 + selection +11.66 + allocation -3.70 + execution +1.07 + exit +0.43 = **+5.31%**），與 holding residual − NAV unexplained = **+11.67%** 的差距，被 SA 以 +9.62% 的「ledger reconciliation error」吸收。**此誤差量級大於 NAV unexplained 本身**，是 attribution 帳本層級的紅旗，不能略過。

SA 在 A2 結論「they should not be treated as the same target without an explicit bridge」是正確判斷，但 bridge 本身就有 +9.62% 對不上，**任何選定的 target metric（NAV 或 holding 或 bridge）在帳本誤差未釐清前都站不住腳**。

**PM 不開新單，A5 作為原派工單 scope 內延伸（同 A1-A4 邏輯）**：

- **A5 派工**：SA 1 day 內回 PM 回答
  - +9.62% ledger reconciliation error 的來源（資料源不一致 / 取樣時點差 / 計算定義差 / 其他）
  - 是否為一次性異常（限 5/11~5/15 窗口）或結構性（其他窗口也有類似量級錯位）
  - 修正方向：是補資料、調定義、還是接受兩指標永遠不可橋接？
  - 完成 A5 之前 PM **不選 target metric**、**不放行 B/C**

**A3 6419 跨單關聯**：

6419 為 **INTRADAY_CHASE / entry slippage +9.85% / 占 formal residual 56.29%** 的單檔。與 RD-002 Stage A oracle diagnostic 既有結論「INTRADAY_CHASE 54 筆，slippage cost +14.29%」**獨立證據鏈接合**。PM 指示：

- A3 結論做為 **RD-002 Stage B proxy design（票 #1）的具體 case study**，由 SA 在 Stage B 設計階段引用
- 不形成 6419 個股 production 決策（依原派工單 Memory 更新區既有排除）
- Stage B 設計交付前 SA 須在文件中明確列出「6419 是 holding-level residual 主導者，但屬 execution slippage 而非結構性 selection alpha 缺口」，避免後續讀者誤把 holding residual 直接視為 selection 問題

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034-v3 06:30 上線 | DONE，附 Logon Mode reliability follow-up |
| RD-005 A1-A4 | DONE |
| RD-005 A5（ledger error 釐清） | NEW，SA 1 day |
| RD-005 B（timing） | HOLD 直到 A5 closed |
| RD-005 C（OOS） | HOLD 直到 A5 closed + B kickoff PM 簽 |
| RD-002 Stage B design（票 #1） | PM 仍待 SA 設計交付；6419 case 必須引用 |
| INFRA-001 offsite backup | 仍暫停等 `MODEL_PIN_BACKUP_DIR` |

### 跨單原則重申（再次）

- A5 完成後 PM 才決定 target metric（NAV / holding / bridge / 全部封存）
- 6419 deepdive 結論不可作為任何加減倉決策依據
- Stage B proxy design 可引用 6419 但不可預判 production 採用

---

## 2026-05-17 PM 驗收回函（SA 第四輪交付）

### 簽核 REQ-034 reliability Path 2 — **APPROVED，附憑證儲存條款**

交付物：[docs/designs/req034_scheduler_reliability_decision_20260517.md](../../docs/designs/req034_scheduler_reliability_decision_20260517.md)

SA 選 Path 2（`Run whether user is logged on or not` + Windows-managed encrypted credentials），理由：
- 06:30 屬 unattended operational reminder
- Interactive only 在 reboot / logout / session termination 後會漏跑
- Path 1 仰賴 user 24/7 登入是 operational promise，不是 scheduler reliability control

PM 判斷：理由成立。Windows Task Scheduler 託管的憑證儲存比 user 自律保持登入更可靠，且憑證由 OS 加密管理不入 git / 不入 script / 不入 log，attack surface 可控。

**PM 簽核條件**：

1. **憑證輸入路徑限定**：user 必須在 Windows Task Scheduler GUI 互動式輸入帳密；**禁止**用 `schtasks /Change /RP <password>` 之類 CLI 模式（明文進 shell history），**禁止**寫進任何 script / .env / .reg / 任何 git-tracked 檔
2. **不在文件記錄帳號**：本決策文件、commit message、log file 都不可寫帳號名與憑證內容；只記「credentials entered <timestamp>」即可
3. **重建驗證**：rebuild 完成後 `schtasks /Query /TN "TW_Stock_My_Holdings_Risk_Cut_0630" /FO LIST` 必須顯示 Logon Mode != Interactive only，PM 端複驗
4. **重建窗口**：不可在 06:30 排程觸發前 30 分鐘內進行重建（避免漏跑當日）；建議任一交易日 09:00 後執行
5. **失敗 fallback**：rebuild 過程任一步失敗，立即還原為 Interactive only 舊任務，回 PM 開新單；不可留下半成品任務

→ **SA 可請 user 互動輸入 credentials 並重建任務。** 重建完成後 SA 回報 PM 驗證結果，本單 reliability follow-up 條目即可關閉。

---

### 簽核 RD-005 A5 ledger error closure — **APPROVED，B 階段可啟動**

交付物：[ml/reports/rd005_ledger_error_a5_20260517.md](../../ml/reports/rd005_ledger_error_a5_20260517.md)

**根因驗證（PM 端數學重算）**：

| 項 | 值 | 驗算 |
|---|---:|---|
| Ledger 5/10 cumulative P&L | -9.6212% | baseline |
| Ledger 5/15 cumulative P&L | -3.7234% | end |
| Ledger window delta | +5.8978% | -3.7234 − (-9.6212) = +5.8978 ✓ |
| NAV window return | +5.90% | 與 ledger delta 相符（0.02pp 為四捨五入殘差）✓ |
| Corrected reconciliation error | 0.0000% | ✓ |
| 「Legacy error = negative baseline」 | True | -9.6212% 被誤當成誤差項 ✓ |

**分類判斷**：
- 資料源不一致 = False ✓（同一份 Champion DB + marks）
- 取樣時點差 = False ✓
- **計算定義錯配 = True** ✓
- **structural for non-inception windows** ✓（任何 start date 在帳本起算日之後的報告都有同樣風險）

修正方向「adjust_definition」實作於 [scripts/champion_nav_attribution.py](../../scripts/champion_nav_attribution.py)，PM 接受。

**重要釐清**：A5 修正的是「window NAV delta vs cumulative ledger P&L」的對齊問題，**並未改變原始 -5.64% NAV unexplained alpha 數字**。-5.64% 是 alpha-vs-benchmark 分解後的剩餘桶，與 ledger 帳本自洽性是兩件事。A5 的價值在於確認帳本層級無漏洞，bridge 可信。

→ **A5 closed。**

---

### Target metric 選定 — **NAV unexplained (-5.64pp)**

PM 在三個候選間決策：

| 候選 | 採用 | 理由 |
|---|---|---|
| NAV unexplained (-5.64%) | **採用** | 原始 PM 派工目標；alpha 缺口本體；A5 後帳本可信；user 在初次決策時即指明此為深挖對象 |
| Holding-level residual (+6.04%) | 不採用 | 窗口敏感（mock 符號相反 per A4）；單檔 6419 占 56% 為 idiosyncratic 集中 |
| Bridge metric | 不採用 | A5 已釐清兩指標是不同診斷口徑，強行橋接無業務意義 |

選定後 holding-level 與 6419 結論不丟棄，但角色轉為**輔助 diagnostic**：
- 6419 案例（INTRADAY_CHASE / +9.85% entry slippage）作為 RD-002 Stage B proxy design 的具體 case study
- holding-level residual 報告作為「窗口敏感指標」存檔，未來引用必須註明窗口（per A4）

---

### B 階段 kickoff — **APPROVED，附 kill criteria**

派工單原 scope：「階段 B — Timing 拆分（1 day），把每日 NAV change 按 intraday / overnight / rebalance day vs hold day 拆」。A5 closed 後此階段可啟動。

**PM 鎖死條件**：

1. **Target lock**：B 階段唯一 target metric 為 **NAV unexplained -5.64pp（5/11~5/15）**，不可中途換 holding-level residual 或 bridge
2. **窗口 lock**：5/11~5/15，不可擴張（同 A 階段條件 3）
3. **方法 pre-registration**：B 階段方法必須在動工前另出 `docs/designs/rd005_phase_b_timing_method_lock_<asof>.md`，PM 簽完才動 RD（同 A 階段教訓，避免再次 retroactive backfill）
4. **拆分維度固定**：intraday（open→close）/ overnight（prev close→open）/ rebalance day（含 entry slippage 重貼） / hold day 四桶；不可增桶不可改名
5. **6419 必須在 B 報告中可追蹤**：因為它占 holding residual 56%，B 階段拆完後 PM 要能看到 6419 進場的 +9.85% entry slippage 落在哪一桶（預期是 rebalance day intraday slippage）
6. **不下行動結論**：B 報告同 A 階段，僅 diagnostic，不得建議 production change

**Kill criteria（沿用 [feedback_bounded_experiment_kill_criteria](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_bounded_experiment_kill_criteria.md)）**：
- B 拆完後若四桶中**任一桶絕對值 ≥ 3.0pp**，視為 actionable signal，PM 接續開行動單（不在本單 scope）
- B 拆完後若所有桶絕對值 < 1.0pp 且加總無法 explain -5.64pp 一半以上，B 視為 inconclusive，C 階段升 P0 並縮窗口至 single-day 細拆
- B 中途若發現任一桶需要新資料源（tick data / minute bar 等本機未備），立即停手回 PM，不可硬上 sample-thin estimate

**Timebox**: 1 day（per 派工單原規格）

→ **SA 可派 RD 跑 B 階段，但必須先交 method lock doc 給 PM 簽。** C 階段仍 HOLD 直到 B 完成 + PM 簽 B 結果。

---

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034-v3 06:30 上線 | DONE |
| REQ-034 Path 2 reliability | PM 簽核 ✓，等 user 互動輸入 credentials + SA rebuild |
| RD-005 A1-A4 | DONE |
| RD-005 A5 ledger error | DONE |
| RD-005 target metric | **NAV unexplained -5.64pp** |
| RD-005 B method lock doc | NEW，SA 先交 PM 簽 |
| RD-005 B（timing） | APPROVED kickoff（method lock 簽後動 RD） |
| RD-005 C（OOS） | HOLD 直到 B 完成 + PM 簽 |
| RD-002 Stage B design（票 #1） | 等 SA 設計；6419 case study 必須引用 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

### Memory 更新（PM 自行追蹤）

待 B 完成 → 一次性更新：
- [project_post_incident_active_tickets](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_post_incident_active_tickets.md)：REQ-034-v3 live、RD-005 A1-A5 done、B 結果
- [project_req034_daily_risk_cut_list](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_req034_daily_risk_cut_list.md)：SQLite 資料源 + synthetic boundary + Path 2 reliability + real-coverage backfill 機制
- 視 B 結果決定是否新增 memory 條目（如「ledger reconciliation 必須 window-aware」）

---

## 2026-05-17 PM 驗收回函（SA 第五輪交付）

### 簽核 RD-005 B method lock doc — **APPROVED，SA 可派 RD**

交付物：[docs/designs/rd005_phase_b_timing_method_lock_20260517.md](../../docs/designs/rd005_phase_b_timing_method_lock_20260517.md)

**PM 六條鎖死條件逐項驗收**：

| # | PM 條件 | doc 對應 | 結果 |
|---|---|---|---|
| 1 | Target lock NAV unexplained -5.64pp | line 11-17，含 exact source field 與 numeric value `-0.05638164814819354` | ✓（加分：精確到小數點 17 位防止 rounding 誤差糾紛） |
| 2 | 窗口 lock 5/11~5/15 | line 19-26，禁擴張，**禁縮為 single-day**（單日歸 C 階段） | ✓（加分：明確禁縮窗） |
| 3 | Method pre-registration doc | 本文件即是 | ✓ |
| 4 | 四桶固定 | line 39-50：B1 overnight gap / B2 rebalance-day intraday slippage / B3 held intraday mark timing / B4 mark/weight sync residual | ✓（見下方桶名對照） |
| 5 | 6419 必須可追蹤 | line 64-79，required fields 含 ticker / order type / entry date / slippage / target weight / B1~B4 contribution（即使為零或 N/A）；預期 B2；若落他桶 RD 須解釋 | ✓ |
| 6 | 不下行動結論 | line 118-129 列出所有禁止 production change 結論 | ✓ |

**桶名對照（PM 原 spec 與 SA doc 差異）**：

| PM 原 spec | SA doc | PM 判斷 |
|---|---|---|
| overnight | B1 overnight gap timing | ✓ 對應 |
| rebalance day | B2 rebalance-day intraday slippage | ✓ 對應，且明確標 intraday slippage |
| intraday | 折入 B3 held intraday mark timing | ✓ SA 把 intraday 細化為「held intraday」，更精確 |
| hold day | 折入 B3 held intraday mark timing | ✓ held position 的每日 mark 即覆蓋 hold day |
| （無） | B4 mark/weight sync residual | **SA 新增** catch-all 桶，用於吸收 stale marks / weight timing / cash/gross sync 殘差 |

B4 是 SA 為了讓 reconciliation 可達 0.50pp tolerance 而設的工程性桶。**PM 接受但加一條解讀規則（不改 lock，加在 PM 端 verdict 解讀層）**：
- 若最終 B4 絕對貢獻 ≥ B1+B2+B3 加總，PM 判定為 `dominated_by_mark_sync_residual`，視為「B 階段未有效隔離 timing 訊號」，需重新檢討方法；不直接 trigger kill criteria 但 PM 不會基於此結果決策 timing 行動
- 此規則僅影響 PM verdict，**不要求 RD 改 B 報告內容**

**SA 加分項（PM 認可）**：

| 項 | 內容 |
|---|---|
| Reconciliation 容差 | line 52-62，0.50pp 絕對誤差，超過則 `data_or_definition_insufficient` 並 stop |
| Data sufficiency rules | line 81-90，要 daily K 缺漏覆蓋權重 > 10% 才 stop；非材料缺漏可進 B4 `unknown_small` 子項，**禁止變成第五桶** |
| Required inputs 明列 | line 27-37，限定 5 種資料源 |
| 禁用資料源明列 | line 37，禁 tick data / minute bars / broker fill logs / order-book |
| 6419 expected bucket | line 77，B2，落他桶要解釋但不可改桶定義 |

PM 鎖死條件全達 + SA 加分項合理，**method lock 通過**。

→ **SA 可派 RD 跑 B 階段。** 完成後交：
- `ml/reports/rd005_phase_b_timing_split_20260517.md`
- `ml/reports/rd005_phase_b_timing_split_20260517.json`

PM 端驗收時依 kill criteria 三條 + 上述 B4 dominance 解讀規則判定。

### 確認 REQ-034 Path 2 文件狀態 — **無 PM 待辦項**

交付物：[docs/designs/req034_scheduler_reliability_decision_20260517.md](../../docs/designs/req034_scheduler_reliability_decision_20260517.md)（已更新）

PM 端驗證：
- doc line 5-12 正確反映 PM approved Path 2 + 06:30 排程 active + 目前仍 Interactive only
- doc line 12 加註「Latest non-secret status check timestamp: 2026-05-17 15:30 +08:00」，符合 PM 條款 2「文件 / commit / log 都不寫帳號內容，只記 timestamp」
- doc line 33-40 五條 credential-handling 條款逐條落地（GUI 輸入 / 禁 CLI / 不入 git/log/.env/.reg / 重建窗口 / 失敗 fallback）
- doc line 41-45 驗證流程含「records only the verification timestamp and status, not account details」

文件無偏離，且 SA 正確未在條款禁止前自行重建任務。

**球在 user 手上**：等 user 任一交易日 09:00 後在 Windows Task Scheduler GUI 互動輸入帳密，重建任務。完成後 SA/RD 端 `schtasks /Query` 驗證 Logon Mode ≠ Interactive only，回 PM 關閉 reliability follow-up。PM 不催，**首週若 06:30 排程因 Interactive only 漏跑，記入 missed-run triage 並列為 user 主動轉 Path 2 的觸發訊號**。

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034-v3 06:30 上線 | DONE，Interactive only 暫時運作中 |
| REQ-034 Path 2 rebuild | 等 user GUI credential entry |
| RD-005 A1-A5 | DONE |
| RD-005 B method lock doc | DONE |
| RD-005 B（timing）RD 派工 | APPROVED，SA 可動 RD |
| RD-005 C（OOS） | HOLD 直到 B 完成 + PM 簽 |
| RD-002 Stage B design（票 #1） | 等 SA 設計；6419 case study 必須引用 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

---

## 2026-05-17 PM 驗收回函（SA 第六輪交付 — RD-005 B 結果）

### 驗收 RD-005 Phase B timing split — **APPROVED for closure；deepdive 線收網；新單 RD-006 開立**

交付物：[ml/reports/rd005_phase_b_timing_split_20260517.md](../../ml/reports/rd005_phase_b_timing_split_20260517.md)

**PM 端 reconciliation 重算**：

| 桶 | SA 報告 | 驗算 |
|---|---:|---|
| B1 overnight gap | +5.26% | |
| B2 rebalance-day intraday slippage | -1.68% | |
| B3 held intraday mark timing | +1.26% | |
| B4 mark/weight synchronization residual | -10.47% | |
| **加總** | **-5.63%** | 5.26 − 1.68 + 1.26 − 10.47 = −5.63 ≈ −5.64% target ✓ |
| Reconciliation error | -0.0000% | 在 0.50pp tolerance 內 ✓ |

**Method lock 條件全達** ✓：target lock / window lock / 四桶固定 / 6419 可追蹤 / 不下行動結論 / 無新資料源需求 / data sufficiency 全綠。

**PM verdict-side 解讀規則命中**：

| 比較 | 值 |
|---|---:|
| \|B4\| | 10.47pp |
| \|B1+B2+B3\|（signed sum） | 4.84pp |
| 判定 | **dominated_by_mark_sync_residual** |

→ B 階段未有效隔離 timing 訊號；NAV unexplained -5.64pp 的支配項是 mark/weight sync residual（B4 占絕對量級超過 timing 三桶 signed sum 的 2.16 倍）。

**Daily contribution 關鍵觀察**：

每日 total 全部位於 -1.14% ~ -1.19% 區間（5 天分別 -1.19 / -0.96 / -1.14 / -1.16 / -1.18），**5 天近乎常數 drag ~1.17%/day**。1.17% × 5 = 5.85%，與 -5.64% target 量級吻合。這不是隨機波動，是**結構性的每日 mark/weight 對齊偏差**。

**6419 行為核對**：

| 桶 | SA | 預期 vs 實際 |
|---|---:|---|
| B1 | +1.56% | 進場前後 overnight 收益 |
| B2 | -1.25% | **entry slippage 落點（method lock 預期此桶為主）** |
| B3 | +2.15% | 進場後 held intraday rally |
| B4 | 0.00% | 個股不歸入 portfolio-level sync 桶（合理）|

method lock line 77「expected B2」嚴格說 B3 才是最大絕對值，但 entry slippage 的 specific effect（+9.85% × 12.68% weight ≈ +1.25%）正確落在 B2 為負（-1.25%），與 entry-cost 語意吻合。SA 標 `as_expected` 在 entry-slippage 語意下成立 ✓。

但 6419 在 NAV-level B-bucketing 的 timing 三桶總和 +2.46%，與 Phase A holding-level residual 報告中 +3.40% 不完全對齊，差 0.94pp。這 0.94pp 落在 portfolio-level B4 內無法追到單檔，**屬 mark/weight sync 殘差的一部分，不是新發現**。

---

### 結論性判斷：RD-005 deepdive 線使命達成，可收網

**Original PM 派工問題**：「為什麼 NAV unexplained 是 -5.64pp？是 selection？timing？OOS 雜訊？」

**B 階段已回答**：
- **不是 timing 問題**：B1+B2+B3 signed sum +4.84pp 為正（timing 三桶實際對 NAV 是貢獻而非拖累）
- **不是 selection / factor exposure 問題**：Phase A 已驗證（holding-level residual +6.04% 為 idiosyncratic、6419 主導、窗口敏感）
- **不是雜訊**：B4 -10.47pp + 每日 ~1.17%/day 常數 drag = 結構性
- **是 mark/weight synchronization residual**：portfolio-level 帳本與部位權重對齊機制存在系統性偏差

依 [feedback_nav_attribution_required](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_nav_attribution_required.md)，attribution 用於策略決策。當 attribution 殘差被 sync residual 支配時，**整套 NAV attribution 數字在 sync 修好前都不可信**。但這不是策略 alpha 缺口的問題，是 attribution pipeline 的 calibration 問題。

**PM 行動**：

1. **RD-005 Phase B 結案** ✓：deepdive scope 內所有問題已答完
2. **RD-005 Phase C（OOS）正式取消（CLOSED，非 HOLD）**：在 B4 sync residual 支配的指標上跑 OOS 信賴區間沒有業務意義；C 不會 reopen 除非 B4 root cause 被 RD-006 解開
3. **B1 +5.26pp overnight gap 為 parked insight**：可能為真實 alpha 訊號，但在 B4 sync 雜訊未清前無法獨立 confirm；等 RD-006 完成後評估是否重啟
4. **B2 -1.68pp entry slippage / 6419 -1.25pp 已歸入 RD-002 Stage B 設計範圍**（與 Stage A oracle 既有 INTRADAY_CHASE / +14.29pp 結論相互佐證）
5. **開新單 RD-006 → SA**（見下方）

---

### 新單 #4 → SA：RD-006 NAV Attribution Mark/Weight Sync Residual 調查 (P0)

#### 業務問題

RD-005 Phase B 揭露 -5.64pp NAV unexplained 中 **-10.47pp 來自 B4 mark/weight synchronization residual**，並呈現每日 ~1.17%/day 常數結構性 drag。在這個 sync residual 被解開前：

- NAV-level attribution 的所有 bucket 結果都被 sync 雜訊汙染
- 任何 NAV-based 策略決策（含 [feedback_nav_attribution_required](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_nav_attribution_required.md) 訴求的 strategic decisions）都站不住
- B1 +5.26pp 等 timing 桶的真實訊號被遮蔽

A5 已修「window vs cumulative」的 calculation-definition mismatch，但顯然還有第二層 sync 問題未捕捉。

#### PM 要 SA 決策的事項

1. **B4 sync residual 成因列舉**：列出至少 3 個技術候選成因（不限以下）：
   - 部位權重採用日 vs 標記日的 lag
   - cash/gross exposure 在 NAV 計算中的處理時點
   - 進場日權重 normalization 與 mark price 不同源
   - intraday rebalance 的 mark refresh 機制
   - 季底 / 配股 / 除權息事件的 adjustment 時序
2. **可測性決策**：每個候選成因，SA 要說明用既有資料能否驗證；不能驗證的列為「需新資料源」並評估成本
3. **跨窗口檢測**：判斷每日 ~1.17%/day drag 是 5/11~5/15 特有，還是回溯 4/23 起 / 4/1 起一樣存在；SA 設計檢測方法（不限 single-day narrowing）
4. **修正路線**：
   - 路線 A: 改 attribution 計算（同 A5 邏輯，調定義不調資料）
   - 路線 B: 改 ledger 寫入時點（涉及 production 排程 / paper portfolio db schema）
   - 路線 C: 接受 sync residual 作為已知 attribution 限制，加註但不修
   - SA 拍板首選路線並說明 trade-off
5. **影響範圍評估**：sync 修好後是否會改變過去已 ship 的所有 NAV attribution 報告（含 Champion week1 attribution）？歷史報告需 retrofit 還是只前瞻採用？
6. **與 RD-002 Stage B 的邊界**：Stage B 在處理執行層 slippage proxy，RD-006 在處理 attribution 層 sync；SA 要說明兩者是否會在 NAV 數字上互相影響

#### PM 邊界（SA 不可越線）

- 不改 production model / weight / sector cap / 14 防呆閾值
- 不重訓 Champion
- 不擴張 paper_portfolio_v2_champion.db schema（除非 SA 在設計中明確標示且 PM 簽核）
- 不對歷史 NAV attribution 報告自動 retrofit（要 retrofit 先 PM 簽）

#### 派工前強制要求

依 [feedback_validate_with_existing_data_first](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_validate_with_existing_data_first.md)：

SA 派 RD 動工前，先用 4/23~4/29 窗口 mock 一次選定的 B4 拆解方法，確認：
- 該窗口 B4 拆解後是否也呈現類似 ~1.17%/day 結構（驗證 sync 為 structural 而非 window-specific）
- 每個候選成因在 mock 中能否被獨立量化
- 沒有候選能解釋 B4 的至少一半 → SA 回 PM 報「current data insufficient」而非硬上

#### 驗收成功標準（SA 交付物）

- [ ] SA 設計文件 `docs/designs/rd006_sync_residual_method_lock_<asof>.md`
- [ ] PM 讀完能回答：「sync residual 主成因是什麼、能不能修、修了會不會 retrofit 歷史」
- [ ] mock 結果送 PM 簽核後才派 RD 跑正式

#### Timebox

- SA 設計階段: 2 day
- mock 階段: 0.5 day
- RD 實作另計

#### Kill criteria

- mock 後所有候選成因都無解釋力（< 30% B4） → SA 回 PM，PM 決定是否升「需新資料源」
- mock 顯示 sync residual 在不同窗口表現不一致 → 不是結構性，PM 重新評估是否值得修

#### Skill 對應

| 階段 | Skill | 用途 |
|---|---|---|
| SA 設計 | 無 | 純架構決策 |
| mock | 無 | RD 出資料，PM 看結果 |
| RD 實作 | `simplify` | 完工自我 review |
| PM 驗收 | `champion-audit` | 確認修正不衝突 production baseline |

#### 依賴

- RD-005 Phase B artifact（已 ship）
- A5 修正後的 `scripts/champion_nav_attribution.py`

---

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 A1-A5 + B method lock + B execution | **DONE / CLOSED** |
| RD-005 C（OOS） | **CANCELLED**（B 結果使 C 無業務價值） |
| **RD-006 sync residual 調查** | **NEW，P0，SA 2 day 設計 + 0.5 day mock** |
| RD-002 Stage B design（票 #1） | 等 SA 設計；6419 case study 必須引用 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

### Memory 更新（PM 本回合執行）

- 新增 `feedback_nav_residual_sync_diagnosis.md`：NAV unexplained 桶在用作 strategy decision input 前，必須先驗 mark/weight sync residual 是否支配；若支配則攻擊 sync 而非 strategy
- 更新 [project_post_incident_active_tickets](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_post_incident_active_tickets.md)：RD-005 deepdive 線結案、新增 RD-006

---

## 2026-05-17 PM 驗收回函（SA 第七輪交付 — RD-006 method lock + mock）

### 簽核 RD-006 method lock — **APPROVED**

交付物：[docs/designs/rd006_sync_residual_method_lock_20260517.md](../../docs/designs/rd006_sync_residual_method_lock_20260517.md)

| 區段 | 驗收 |
|---|---|
| Problem statement（聚焦 B4 機制，非原 -5.64pp） | ✓ 清楚劃線不重啟 strategy timing |
| Candidate root causes（C1~C5） | ✓ 五個候選含 hypothesis + evidence to inspect，覆蓋完整 |
| Selected SA route（A 先試，B/C 後備） | ✓ 理由含 A5 既有經驗（純定義修正可達 0% reconciliation）|
| Mock requirement | ✓ |
| RD dispatch gate（PM 簽核前不放行 production patch） | ✓ |
| Retrofit policy（只 retrofit 兩個窗口，更廣需 PM 簽） | ✓ 保守且可控 |
| RD-002 boundary | ✓ 明確 attribution layer vs execution layer 分工 |
| Kill criteria | ✓（含 PM 微調，見下方） |

**PM 微調 kill criteria（不改 method lock，加 PM 端解讀規則）**：

method lock line 115「No candidate explains at least 30% of B4 on both formal and mock windows」是嚴格 single-candidate 要求。考量 mock 已揭示 B4 符號跨窗口翻轉，PM 額外接受：

- **組合候選**：`C1 + window-specific 因子`（如 `C1 + C4 mature portfolio 模式` vs `C1 + C2 young portfolio 模式`）若加總可在各窗口分別解釋 ≥ 30% B4，視為 actionable diagnostic
- 純 single-candidate 仍是首選，組合方案要 SA 在 RD 報告中明確列出每個候選的 window-conditional 解釋力
- 純 single-candidate 與 combination 都失敗 → 才觸發 method lock 原 kill criterion 走 `current_data_insufficient`

### 簽核 RD-006 mock — **APPROVED，並接受核心 finding**

交付物：[ml/reports/rd006_sync_residual_mock_20260517.md](../../ml/reports/rd006_sync_residual_mock_20260517.md)

**PM 端 mock 重算**：

| 窗口 | B1 | B2 | B3 | B4 | signed sum B1+B2+B3 | B4 dominance |
|---|---:|---:|---:|---:|---:|---|
| 4/23~4/29 (mock) | +2.39 | -18.92 | -0.91 | +16.19 | -17.44 | False (\|16.19\| < \|17.44\|) |
| 5/11~5/15 (formal) | +5.26 | -1.68 | +1.26 | -10.47 | +4.84 | True (\|10.47\| > \|4.84\|) |

驗算 ✓。**核心 finding**：

1. **B4 符號跨窗口翻轉**（-10.47 vs +16.19）→ 否定原「stable 單向 daily sync drag」假設
2. **B4 dominance 只在 formal 觸發** → 我先前在 Phase B 端的 verdict-side rule 是 window-conditional 而非 universal
3. **Mock 中 B2 -18.92pp 占絕對主導**（entry slippage 重災區），與 4/23 Champion week 1 啟動大量 INTRADAY_CHASE 進場吻合，並與 RD-002 Stage A oracle 既有 +14.29pp slippage cost 互證

**PM 對先前 Phase B 解讀的自我修正**：

我先前 commit dc7819ef 把 formal window B4 dominance 推論為「structural daily ~1.17%/day drag」。**這個結論在 single-window 證據下成立，但在 mock 跨窗口比對後須限縮**：

- ✓ 仍正確：formal window 5/11~5/15 確實由 B4 支配，且該窗口呈現 daily 常數 drag
- ⚠ 須限縮：「structural」一詞在跨窗口未驗證前不該下；mock 顯示 B4 在不同窗口可呈現完全不同的行為 pattern
- ⚠ 後續行動：先前 commit 提及「整套 NAV attribution 在 sync 修好前都不可信」改為「整套 NAV attribution 在 B4 機制釐清前，**在 B4 dominant 的窗口**下不可信；B4 non-dominant 窗口下，timing 三桶可正常解讀但需註明跨窗口可比性」

我會在 PM 端記憶條目補一條 caveat（見下方 Memory 更新）。

**Candidate readout 驗收**：

| 候選 | SA mock support | PM 端評估 |
|---|---|---|
| C1 target/bucket basis mismatch | high | ✓ 符號翻轉直接支持此候選 |
| C2 weight snapshot lag | medium | ✓ mock 平均 gross exposure > 100% 與 formal < 100% 差異具體 |
| C3 mark refresh / stale mark | low_to_medium | ✓ 暫無證據但不排除 row-level inspection 可能挖出 |
| C4 cash/gross normalization | medium | ✓ leverage 跨窗口差異具體 |
| C5 corporate action | low | ✓ 無 aggregate 證據，留待 row-level outlier 才復活 |

**SA 排序首選 C1 + 次選 C4 / C2，PM 接受。**

---

### RD-006 RD 派工 — **APPROVED for offline diagnostic only**

method lock line 87-92 dispatch gate 條件：
- decompose C1~C5 用既有 artifacts + DB rows
- 不 mutate `paper_portfolio_v2_champion.db`
- 不改 production model / weights / sector caps / 14 guardrails / schedules

**PM 加 3 條補充約束**：

1. **Window basis explicit**：RD 報告必須對每個候選提供 mock + formal 雙窗口的解釋力數字（pp 與 % of B4），不可僅報 formal
2. **C1 hypothesis test 路徑**：SA 既選 C1 為首選，RD 必須在 offline diagnostic 中明確測試「Phase B target formula 是否在 mature vs young portfolio 下用了不同 basis」；若 C1 在 mock 中能解釋 ≥ 30% 但在 formal < 30%（或反之），仍是有價值 finding，不視為 kill
3. **RD 報告須提 PM 可決策的 fix scope**：除候選排序外，必須提供「修 C1 的最小 reporting-definition change vs 修 C1 + C2 的中等 reporting-definition change vs 動 ledger schema」三檔選項，含 blast radius 評估

→ **SA 可派 RD 跑 offline diagnostic。** 完成後交：
- `ml/reports/rd006_offline_diagnostic_<asof>.md`
- `ml/reports/rd006_offline_diagnostic_<asof>.json`

### Memory 更新（PM 本回合執行）

更新 [feedback_nav_residual_sync_diagnosis](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_nav_residual_sync_diagnosis.md) 補 caveat：

- B4 dominance 是 **window-conditional**，不是 universal property
- 觀察到 single-window B4 dominance 後，**必須跨窗口驗 sign stability**；若 sign 翻轉，B4 absorb 的東西在不同窗口不同
- 「structural」一詞在 single-window 證據下不該下；至少 ≥ 2 個 window 觀察到同號 + 同量級 dominance 才能稱 structural
- 不變的核心：B4 dominant 的窗口仍不可信用於策略決策

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 deepdive 全線 | CLOSED / CANCELLED |
| RD-006 method lock + mock | DONE |
| RD-006 offline diagnostic（RD 派工） | APPROVED，SA 可動 RD |
| RD-006 production patch | HOLD 直到 offline diagnostic + PM 簽 |
| RD-002 Stage B（票 #1） | 等 SA |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

---

## 2026-05-17 PM 驗收回函（SA 第八輪交付 — RD-006 offline diagnostic）

### 驗收 RD-006 diagnostic — **APPROVED，C1 確認為支配成因**

交付物：[ml/reports/rd006_sync_residual_diagnostic_20260517.md](../../ml/reports/rd006_sync_residual_diagnostic_20260517.md)

**核心結果**：

| 候選 | mock 解釋力 | formal 解釋力 | ≥30% 雙窗口 |
|---|---:|---:|---|
| **C1 target/bucket basis mismatch** | **100%** | **100%** | **True** |
| C2 transition weight snapshot lag | 100% | 12% | False（窗口偏差大）|
| C3 mark refresh | 0% | 0% | False |
| C4 cash/gross normalization | 0.5% | 39.6% | False（窗口偏差大）|
| C5 corporate action | 0% | 0% | False |

**C1 mature-vs-young 測試結果**：
- Mock: young_transition driver -17.25pp，power 100%
- Formal: mature_held driver +6.09pp，power 58.15%
- **PM combination rule: True** ✓

### PM 結論性釐清（再次自我修正）

**從第 1 輪到第 8 輪的概念演進**：

| 時間點 | 認知 | 修正 |
|---|---|---|
| Phase A | -5.64pp 是 selection / factor 缺口 | A 階段否定，holding-residual 是 idiosyncratic |
| A5 | -5.64pp 包含 +9.62% ledger 算錯 | A5 修正後其實 ledger 自洽 |
| Phase B | -5.64pp 由 B4 mark/weight sync 支配 | B4 dominance 命中 PM verdict-side rule |
| Phase B 後 commit dc7819ef | -5.64pp 是「structural daily 1.17%/day sync drag」 | RD-006 mock 跨窗口翻轉，過度推論被打掉 |
| RD-006 mock | B4 行為跨窗口不穩定，C1 + portfolio-state 候選 | offline diagnostic 確認 |
| **RD-006 diagnostic（本輪）** | **B4 100% 由 C1 target/bucket basis mismatch 吸收** | **這是 reporting-definition layer 的 decomposition artifact，不是 ledger / sync / strategy 問題** |

**最終定性**：

> -5.64pp NAV unexplained **既不是策略 alpha 缺口，也不是 ledger sync issue**。它是 NAV attribution decomposition 在 reporting layer 把「NAV unexplained target basis」和「observable timing proxy basis」用 B4 catch-all 桶橋接時，產生的**定義層面 artifact**。

這個 finding 是 negative-but-decisive：排除了三條原本可能耗大量 RD 時間的方向（重訓 / 改 sector cap / 改 ledger schema），把 attribution 失真的位置精確定位到 reporting 定義層。

### Fix scope 簽核 — **採 minimal_reporting_definition，medium 暫不啟動，schema 駁回**

| Scope | PM 決策 | 理由 |
|---|---|---|
| minimal_reporting_definition | **APPROVED** | 加 C1 basis labels / B4 dominance flag / cross-window sign-stability check 到 NAV attribution 輸出；blast radius 低（report schema + docs，不動 DB / 不動 production code path）；直接解決「PM/SA 過度推論 single window」這個本次教訓 |
| medium_reporting_refactor | **暫不啟動** | 拆 target 與 proxy 為獨立 sub-bases 是結構性改造，會影響 Champion week1 attribution 等 downstream 報告的桶定義；minimal 上線觀察 1~2 週後 PM 再評估是否升級 |
| ledger_schema_or_write_path | **駁回** | SA 已標 not recommended，diagnostic 已確認 reporting layer 即可修，動 DB schema 是過度反應 |

### Minimal 實作派工 — **APPROVED for SA 設計**

PM 邊界：

1. **不改 B1~B4 桶定義**：minimal 只**加** labels / flags / checks，不改現有四桶的計算式
2. **不改既有 NAV attribution JSON schema 既有欄位**：可加新欄位（如 `c1_basis_label`、`b4_dominance_flag`、`cross_window_sign_stability`），不可改既有欄位 type / 含意
3. **適用範圍**：新報告自動帶這些欄位；既有 ship 過的歷史報告（Champion week1 attribution、RD-005 deepdive 等）**不自動 retrofit**，但可在文件 README 加註「2026-05-17 以前的報告未含 B4 dominance / sign-stability metadata」
4. **新欄位語意鎖定（PM 端規範）**：
   - `b4_dominance_flag`：`|B4| > |B1+B2+B3 signed sum|` 時為 True
   - `cross_window_sign_stability`：需要至少 2 個窗口資料才能計算；資料不足時填 `insufficient_windows` 而非 False
   - `c1_basis_label`：標明該報告使用的 target basis（`nav_unexplained_target` 或 `observable_timing_proxy`）
5. **測試覆蓋**：新增 `tests/test_rd006_attribution_labels.py`，覆蓋 b4_dominance_flag 邊界（剛好相等時定義為非 dominant）、sign_stability insufficient 路徑、c1_basis_label 預設值

**SA 派工流程**（依 PM → SA → RD 規範）：
- SA 出 minimal 設計小文件（`docs/designs/rd006_minimal_reporting_labels_<asof>.md`），含上述 PM 邊界 + 實作切點 + RD 工作拆解
- PM 簽完 SA 派 RD

**Timebox**：SA 設計 0.5 day，RD 實作 0.5~1 day（含測試），不重 attribution pipeline。

### 對 Champion / RD-002 / 其他 attribution 報告的影響

| 報告 | 影響 | 處理 |
|---|---|---|
| Champion week1 attribution（4/23~4/29） | NAV-level 桶數字不變；可重跑帶新 labels 但屬可選 | minimal 上線後 SA 自行評估是否補帶 |
| RD-002 Stage A oracle | 不受影響（execution layer，非 attribution decomposition layer） | 無動作 |
| RD-002 Stage B 設計（票 #1） | 6419 case study 依舊有效；diagnostic 確認 execution slippage 是真實 alpha 缺口而非 reporting artifact | SA 在 Stage B 設計中可引用 RD-006 結論 |
| RD-005 deepdive 結論 | 補一條 PM 端記憶條目（已落地）；不重開 | 結案不變 |

### Memory 更新（PM 本輪執行）

[feedback_nav_residual_sync_diagnosis](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_nav_residual_sync_diagnosis.md) 需再次更新：

- B4 dominance 的真正成因**不是** sync issue / ledger 對齊問題
- 真正成因是 **NAV attribution decomposition 在 reporting layer 把 target basis 和 observable timing proxy basis 用 B4 catch-all 桶橋接**所造成的 definitional artifact
- 修正方向是 reporting-definition layer（加 labels / dominance flag / sign-stability check），不需動 ledger schema
- 之前說「修 B4 sync 是 P0」的措辭過寬，正確說法是「在 B4 dominant 且跨窗口 sign-stable 的情境下，不可基於 NAV unexplained 桶做策略決策；修正方向是 reporting definition，不是 ledger」

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 deepdive | CLOSED |
| RD-006 method lock + mock + offline diagnostic | DONE |
| RD-006 minimal 實作（SA 設計 + RD 實作） | NEW，SA 0.5 day 設計 |
| RD-006 medium refactor | 暫不啟動，待 minimal 上線觀察 |
| RD-006 schema 改動 | 駁回，封存 |
| RD-002 Stage B（票 #1） | 等 SA；可引用 RD-006 結論 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

---

## 2026-05-17 PM 簽核回函（SA 第九輪交付 — RD-006 minimal 設計）

### 簽核 RD-006 minimal reporting labels 設計 — **APPROVED，SA 可派 RD**

交付物：[docs/designs/rd006_minimal_reporting_labels_20260517.md](../../docs/designs/rd006_minimal_reporting_labels_20260517.md)

**PM 五條邊界逐項驗收**：

| # | PM 邊界 | doc 對應 | 結果 |
|---|---|---|---|
| 1 | 不改 B1~B4 桶定義 | line 18 Non-Goals 明列 | ✓ |
| 2 | 不改既有 JSON schema 既有欄位 type / 含意（可加新欄位） | line 19 Non-Goals + line 39-48 新增 top-level `reporting_definition_labels` object | ✓ |
| 3 | 既有歷史報告不自動 retrofit | line 22 Non-Goals + line 110-119 Historical Artifact Policy | ✓ |
| 4 | 新欄位語意鎖定 | line 66-101 三個欄位定義 | ✓ |
| 4a | `b4_dominance_flag` 嚴格 `abs(B4) > abs(B1+B2+B3)`，相等 false | line 70-78 含 strict greater-than 與 boundary case | ✓ |
| 4b | `cross_window_sign_stability` 資料不足填 `insufficient_windows` 非 false | line 94-97 明確「Do not encode insufficient data as false」 | ✓ |
| 4c | `c1_basis_label` 預設 `nav_unexplained_target` | line 57-59 | ✓ |
| 5 | 新增 `tests/test_rd006_attribution_labels.py` | line 136-147 含 7 case 覆蓋 PM 邊界 | ✓ |

**SA 加分項（PM 認可）**：

| 加分項 | 內容 | PM 判斷 |
|---|---|---|
| `label_version: rd006_minimal_v1` | 前向相容欄位 | ✓ 未來辨識 pre/post 2026-05-17 artifact |
| `c1_basis_label` 多一值 `observable_timing_proxy` | 未來只報 B1/B2/B3 不對 target 的場景可用 | ✓ 不衝突當前預設 |
| `cross_window_sign_stability` 多一值 `mixed_or_zero` | 處理 zero 邊界（如某窗口 B4 = 0） | ✓ 比單純 same_sign / sign_flip 更精確 |
| 缺值編碼 `unknown_pre_rd006_labels` 非 false | 歷史 artifact 缺 labels 時的明確訊號 | ✓ 與 PM「不可編碼為 false」邊界一致 |
| Test case 7「Existing field values are preserved」 | 覆蓋 additive-only 原則 | ✓ 直接驗證 PM 邊界 2 |
| Implementation surface 列 optional 擴張項 | `scripts/champion_nav_attribution.py` 為 optional，決策權留 PM | ✓ 不擅自擴張 |

**PM 對 optional 擴張的決策**：

doc line 32：「optionally `scripts/champion_nav_attribution.py` only if PM wants the labels in base Champion NAV attribution artifacts immediately」

**PM 決策：先不擴張 `champion_nav_attribution.py`**

理由：
- `champion_nav_attribution.py` 是 daily pipeline 的核心 attribution 路徑，影響每日 production NAV 報告
- minimal v1 先在 RD-005 Phase B style report 跑順，labels helper 行為穩定後再考慮擴
- 一次只改一條路徑，blast radius 可控
- 若未來 Champion week1 attribution 需要 labels，另開 PM 簽核擴張小單，不在本輪 scope

→ RD 本輪只實作：
- `scripts/attribution_reporting_labels.py`（new helper）
- 接到 `scripts/rd005_phase_b_timing_split.py` 的 JSON / MD 輸出
- `tests/test_rd006_attribution_labels.py`
- **不動** `scripts/champion_nav_attribution.py`

### RD 派工 — **APPROVED**

**Timebox**: 0.5~1 day（含測試），不重 attribution pipeline。

**RD 動工前自檢清單**（PM 列給 SA，SA 派 RD 時轉達）：
1. 確認新欄位純 additive，跑既有測試套件無迴歸
2. helper 為純函數（接收 B1~B4 數值與可選 cross-window list，回傳 dict），不接 DB / 不接 file IO
3. tests 7 case 全綠才可 merge
4. 改完跑一次 `python scripts/rd005_phase_b_timing_split.py`（或對應 entry point）對 5/11~5/15 重出 artifact，確認 labels 帶入 JSON + MD
5. README / docs 加註 pre-2026-05-17 artifact 缺 labels 是預期行為，非 bug

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 deepdive | CLOSED |
| RD-006 diagnostic | DONE |
| RD-006 minimal 設計 | DONE |
| RD-006 minimal RD 實作 | APPROVED，SA 可派 RD（0.5~1 day）|
| RD-006 champion_nav_attribution 擴張 | **PM 決策暫不擴**，另開單議 |
| RD-006 medium refactor | held |
| RD-006 schema | 駁回封存 |
| RD-002 Stage B（票 #1） | 等 SA |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

---

## PM 驗收回函（SA 第十輪交付 — RD-006 minimal RD 實作）

### 驗收 commit 44ddcf74 — **APPROVED，RD-006 minimal scope CLOSED**

PM 端 spot-check：

| 檢核 | 證據 | 結果 |
|---|---|---|
| commit diff 範圍 | 6 檔 +182/-1，全 additive | ✓ |
| `champion_nav_attribution.py` 不在 diff | `git show --stat 44ddcf74` 確認 | ✓（符合 PM 暫不擴張決策）|
| 無 DB / schema 變更 | diff 只含 scripts / tests / reports | ✓ |
| README 缺值編碼說明 | `ml/reports/README.md` +10 行 | ✓ |
| JSON labels 完整 | 5/11~5/15 artifact 已含 `reporting_definition_labels` 四欄 | ✓ |
| `b4_dominance_flag = true` 計算正確 | \|-10.47\| > \|+4.84\| → true | ✓ |
| `cross_window_sign_stability = insufficient_windows` | artifact 只含單窗口，符合 fallback 規則 | ✓ |
| Helper 純函數 | 無 DB / 無 IO，`deepcopy` 保證 additive，invalid `c1_basis_label` raise `ValueError` | ✓ |
| 測試 | SA 報告 `pytest -q tests/test_rd006_attribution_labels.py tests/test_champion_nav_attribution.py` → 12 passed | ✓ |
| py_compile / json.tool | clean | ✓ |

**RD-006 全鏈結案**：

| 階段 | 狀態 |
|---|---|
| method lock + mock | DONE |
| offline diagnostic | DONE（C1 confirmed） |
| minimal 設計 | DONE |
| minimal RD 實作（commit 44ddcf74） | DONE |
| medium refactor | held（等 minimal 上線觀察 1~2 週 PM 再評估）|
| schema 改動 | 駁回封存 |
| `champion_nav_attribution.py` 擴張 | 暫不擴，另開單議 |

### RD-005 → RD-006 完整鏈條的最終結論

歷時 10 輪 PM/SA/RD 循環，原 `-5.64pp NAV unexplained` 的最終定性：

> **既不是策略 alpha 缺口，也不是 ledger sync issue，是 NAV attribution decomposition 在 reporting layer 把 NAV unexplained target basis 與 observable timing proxy basis 用 B4 catch-all 桶橋接所產生的 reporting-definition layer artifact。已透過 RD-006 minimal labels 加上自我描述（b4_dominance_flag / cross_window_sign_stability / c1_basis_label），未來讀者可在不重訓不動 schema 的前提下，正確解讀 NAV attribution 是否為 timing-clean 或 B4-dominated。**

排除的錯誤方向（每條都曾經被認真考慮，每條都被資料推翻）：

| 排除方向 | 推翻證據 |
|---|---|
| 策略 alpha selection 缺口 | Phase A holding-residual 是 idiosyncratic 集中（6419 占 56%），非系統性 selection |
| Factor exposure 缺口 | Phase A factor model 解釋力 < 1% |
| Ledger 帳本錯誤 | A5 修正後 corrected reconciliation error = 0% |
| Universal structural daily sync drag | RD-006 mock 跨窗口 B4 符號翻轉（+16.19 vs -10.47）打掉 universal 推論 |
| Mark refresh / stale mark | RD-006 C3 雙窗口 0% 解釋力 |
| Corporate action / price basis | RD-006 C5 雙窗口 0% 解釋力 |
| Cash/gross normalization 為主因 | RD-006 C4 只有 formal 39.6%，mock 0.5%，非主因 |
| Schema 改動 | RD-006 SA 標 not recommended + PM 駁回 |

實際命中的單一根因：**C1 target/bucket basis mismatch + window-conditional portfolio state**，C1 雙窗口 100% 解釋力。

### 仍開放項目（PM tracking）

| 項目 | 處理 |
|---|---|
| B1 +5.26pp overnight gap insight | parked，待 minimal labels 觀察期後評估是否獨立追因 |
| 6419 INTRADAY_CHASE case study | 已交付 RD-002 Stage B design 引用（票 #1）|
| RD-002 Stage B design（票 #1）| 仍等 SA 設計交付 |
| REQ-034-v3 Path 2 reliability | 等 user GUI credential entry |
| RD-006 medium refactor | 觀察期約 1~2 週後 PM 再評估 |
| RD-006 champion_nav_attribution 擴張 | 另開單議，目前暫不啟動 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

### Memory 一次性更新（PM 本回合）

- 更新 [project_post_incident_active_tickets](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_post_incident_active_tickets.md)：RD-005/006 結案、新增 RD-006 minimal labels live、列當前未結項清單
- 觀察 1~2 週後若 medium refactor 不升級即移出 active

---

## PM 簽核回函（SA 第十一輪交付 — 票 #1 RD-002 Stage B 設計）

### 簽核 Stage B ex-ante slippage proxy v2 設計 — **APPROVED**，SA 可派 RD 做 offline evaluation

交付物：[docs/designs/stage_b_slippage_proxy_v2_20260517.md](../../docs/designs/stage_b_slippage_proxy_v2_20260517.md)（commit 42ddb6ca）

PM 端 spot-check：commit diff 62 行純新增，無 production code 變更，git status clean ✓。

**PM 原票 #1 六項決策對應**（從 [票 #1](#決策單-1--saStage-b-ex-ante-slippage-proxy-路徑決策-p1) 對應）：

| # | PM 要 SA 決策 | SA 對應 | 結果 |
|---|---|---|---|
| 1 | 技術方案路線（rule / ML / hybrid） | Section 1：hybrid rule-first，明確 reject 純 ML（樣本不足 + baseline incident）與純 hard rule（regime brittle）| ✓ |
| 2 | 候選 proxy 集合（≥ 3）| Section 2：4 個（chase intensity / liquidity impact / volatility-exhaustion / open stress），含資料可得性 | ✓ 超 PM 門檻 |
| 3 | 評估指標 | Section 3：Spearman + tail capture + avoided-bad-chase / missed-good-chase 分開 + net skip oracle + calibration error 為次要證據 | ✓ ranking-first 而非 MAE-first，符合 Stage A oracle 既有 reporting shape |
| 4 | 採用條件（design → shadow → production）| Section 4：shadow 5 條 + production 4 條，含 sample 數 / 指標門檻 / 兩個 contiguous subwindow 一致性 | ✓ |
| 5 | Baseline 依賴 + 半年降級方案 | Section 5：offline 可進；shadow/production 等 accepted Champion baseline；6 個月不穩定可降級為 advisory-only（不 hard-kill） | ✓ |
| 6 | 失敗路徑 | Section 6：先 request expanded observation fields，bounded window 仍失敗才允許 permanent archival | ✓ 不允許單輪失敗即永久封存 |

**RD-006 結束後新增邊界（SA 主動補）**：

| 新邊界 | SA 對應 | 結果 |
|---|---|---|
| 6419 case study 必須引用 + execution-layer 定性 | Section 0：明標 execution slippage，**禁**作為 selection alpha / production allocation signal；ex-ante feature only；歸入 RD-002 Stage A INTRADAY_CHASE 群組 | ✓ |
| 禁用 NAV/B4/RD-006 labels 作為 target 或 feature | Section 0.1：明列 forbidden：`NAV unexplained` / B1~B4 / `b4_dominance_flag` / `cross_window_sign_stability`；RD-006 only as narrative reference | ✓ |
| 禁用 post-entry realized return 作為 feature | Section 0.1 明示 forbidden | ✓ 避免 leakage |

**PM 原邊界（不可越線）驗收**：

| 邊界 | 驗證 |
|---|---|
| 不改 Stage B production threshold +0.50pp | doc line 8 明列 | ✓ |
| 不擴張到 Stage C（exit-side slippage）| 全文無 Stage C 內容，scope 限 entry slippage | ✓ |
| 不直接 promote / shadow flip（design only）| Section 4 多階段 gate，Section RD-Ready 明列「RD is not cleared for production code」「PM approval after SA review」 | ✓ |

---

### PM 端三條 verdict-side 觀察（不改設計，記錄於此避免未來誤讀）

1. **+0.50pp 雙重語意要明確區分**：
   - doc line 8 的 +0.50pp 是 production **kill switch**（Stage B 觸發成本上限）
   - Section 4.4 shadow gate 第 4 條的 +0.50pp 是 **shadow promotion 最低 benefit 門檻**
   - 兩者**數值相同但語意不同**。RD 與後續驗收讀者必須認清，不可混為一談；本 PM 簽核不准許未來把 shadow benefit 門檻當作 kill switch 等價。
2. **6-month advisory-only 降級為具體 fallback，不是無限期**：
   - Section 5 line 159「If the baseline remains unstable for six months」是條件式降級，6 個月後 SA 須主動回 PM 開新單再評估，**不可**默默繼續 advisory-only 超過 6 個月
3. **RD 報告 8 條 task list 中第 8 條「Include an RD-006 boundary note」是強制檢核項**：
   - PM 驗收 RD 產出時會 grep 此 note 是否存在 + 內容是否明示 NAV/B4 not used
   - 若漏寫，RD 報告退回補

---

### RD 派工授權（SA 之後執行）

SA 可派 RD 跑 offline evaluation only：

- 允許實作面：`scripts/stage_b_slippage_proxy_design.py` / `ml/reports/rd002_stage_b_proxy_design_<asof>.{md,json}` / 必要的 threshold CSVs
- 資料源：Stage A trade/slippage frame + 既有市場資料 snapshot
- **禁止**：production scheduler / order gate / model / DB schema / Champion attribution path 任何變更
- RD task list 8 條照 doc 執行，第 8 條 RD-006 boundary note 為強制檢核項

PM 不限定 RD timebox，由 SA 依 task list 量設定；完工 SA 自審後交 PM 驗收。

### 派工狀態更新

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 / RD-006 chain | CLOSED |
| **票 #1 Stage B SA 設計** | **DONE，PM 簽核 ✓** |
| Stage B RD offline evaluation | APPROVED，SA 可派 RD |
| Stage B shadow / production promotion | held（等 Champion baseline accepted）|
| RD-006 medium refactor / champion_nav 擴張 | held / 另開單議 |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |

---

## PM 驗收回函（SA 第十二輪交付 — Stage B offline evaluation）

### 驗收 commit 7d3e898c — **APPROVED with 2 PM conditions before shadow review**

交付物：
- [scripts/stage_b_slippage_proxy_design.py](../../scripts/stage_b_slippage_proxy_design.py)（491 行）
- [tests/test_stage_b_slippage_proxy_design.py](../../tests/test_stage_b_slippage_proxy_design.py)（4 函數）
- [ml/reports/rd002_stage_b_proxy_design_20260518.md](../../ml/reports/rd002_stage_b_proxy_design_20260518.md) + .json + 4 CSV

**Scope 合規檢核（commit 7d3e898c diff）**：

| 禁區 | 驗證 |
|---|---|
| production scheduler | 未動 ✓ |
| order gate | 未動 ✓ |
| model files | 未動 ✓ |
| DB schema | 未動 ✓ |
| Champion attribution path | 未動 ✓ |
| +0.50pp kill switch | 未動 ✓ |

**Test 覆蓋驗證**：
- `test_ex_ante_inputs_exclude_forbidden_post_entry_fields` ✓ 驗 RD-006 邊界
- `test_prepare_stage_b_features_adds_proxy_scores_and_liquidity_bucket` ✓
- `test_threshold_row_reports_avoided_bad_and_missed_good_separately` ✓
- `test_analyze_stage_b_proxy_includes_6419_case_and_rd006_boundary` ✓ 驗 RD task list 第 8 條

**結果摘要**：

| 候選 | Spearman | boot 90% LB | top quintile capture | net skip | avoided bad | missed good | shadow_gate |
|---|---:|---:|---:|---:|---:|---:|---|
| chase_intensity | +0.750 | +0.685 | 40.58% | +11.87 | +11.48 | +13.90 | False（avoided < missed）|
| liquidity_impact | +0.404 | +0.286 | 60.59% | +13.97 | +12.79 | +10.13 | True |
| volatility_exhaustion | +0.173 | +0.021 | 16.82% | +12.38 | +12.66 | +6.26 | False（capture < 40%）|
| open_stress | -0.683 | -0.736 | 6.16% | +6.96 | +9.24 | +3.56 | False（Spearman 負）|
| **hybrid_rule_proxy** | **+0.737** | **+0.668** | **59.88%** | **+14.52** | **+14.35** | **+13.84** | **True** |

**6419 案例驗證**：proxy 0.5841 > cutoff 0.5324 → flag True。2026-05-12 進場前以 ex-ante feature 可擋下，避免 +9.85% 進場 slippage。**強驗證**。

---

### PM 兩條附加條件（shadow review 啟動前必達）

**條件 1：avoided−missed 差距需 bootstrap CI**

hybrid_rule_proxy avoided +14.35% 與 missed +13.84% 差距僅 **+0.51pp**，可能在 noise 區。SA 設計 Section 4 shadow gate 第 5 條「avoided 大於 missed」是定性要求，本次數值差距太窄。

PM 要求 SA 在 shadow review 啟動前補：
- `(avoided_bad − missed_good)` bootstrap 90% CI
- CI 完全在正側（lower bound > 0）才視為通過該 gate；否則 PM 不放行
- 用既有 135 trade frame 即可跑

**條件 2：Liquidity 觀測缺口需明確路徑決策**

報告 line 46-49 揭露 4 個 liquidity 欄位缺：ADV / spread / intraday volume curve / limit-up-down state。SA 自評「liquidity_impact 為部分 order-size proxy」。

PM 要求 SA 在 shadow review 啟動前對缺項擇一：
- 路徑 A：明列資料源 + 補資料 timebox（依 Stage B 設計 Section 6 failure path 規格）
- 路徑 B：明確接受「部分 proxy」上線，並在 shadow + production gate 加註限制
- 不可不決策進 shadow

---

### 觀察：3 個並行 commits 落 post-incident whitelist，PM 不否決但要求合併報告

PM 端 `git log` 觀察到 3 個 production-touching commits 未在本輪 SA report 提及：

| commit | 主旨 | 白名單對應 |
|---|---|---|
| 239dd98b | repair stale model selection pointer | infra / pipeline 修正，**允許** |
| dac55a38 | protect weekly retrain model artifacts | INFRA-001 follow-up，**允許** |
| f16dd0ef | cap lightgbm cpu threads | training resource 硬化（infra），**允許** |

三件皆落 [feedback_post_incident_out_of_scope_lock](../../C:/Users/cheng/.claude/projects/f--stock/memory/feedback_post_incident_out_of_scope_lock.md) 白名單。PM 不否決。

但 PM 要求 **未來 SA 輪次回報必須涵蓋本期間所有 production-touching commits**，不可只報 headline delivery。理由：
- PM 驗收要對整段期間做合規檢核
- SA「Stage B scope 合規」陳述若被未涵蓋 commit 反證會破壞 audit trail
- 後續任何 commit 若落白名單外更需主動報

→ SA 後續輪次附「本期間所有 commits + 白名單對應分類」。

### Stage B 派工狀態更新

| 工項 | 狀態 |
|---|---|
| Stage B offline evaluation | DONE（commit 7d3e898c）|
| **PM 條件 1**：avoided−missed bootstrap CI | NEW，SA 派 RD 補 |
| **PM 條件 2**：liquidity 缺口路徑決策 | NEW，SA 設計 + PM 簽 |
| Stage B shadow review | held 直到 PM 條件 1+2 達標 **且** Champion baseline accepted |
| Stage B production promotion | held（Section 4 production gate + 上述前置 + PM 另開單）|
| +0.50pp kill switch | 不動 |

### 全局狀態（PM 收尾觀察期）

| 工項 | 狀態 |
|---|---|
| REQ-034 Path 2 | 等 user GUI credential entry |
| RD-005 / RD-006 chain | CLOSED |
| Stage B offline eval | DONE，附 PM 2 條件 |
| Stage B shadow review | held |
| RD-006 medium refactor / champion_nav 擴張 | held / 另開單議 |
| Champion baseline | v2.6 incident edition |
| INFRA-001 offsite backup | 暫停等 `MODEL_PIN_BACKUP_DIR` |
