# V2 Champion 第一週 Alpha 缺口追因 — RD 需求單

- **發起人**: PM
- **發起日**: 2026-04-30
- **對應分析**: [ml/reports/champion_week1_attribution.csv](../../ml/reports/champion_week1_attribution.csv)
- **背景脈絡**: V2 Champion Shadow Book 2026-04-23 上線，第一週 (4/23–4/29) MTM -2.09% 對 TWII +4.21%，alpha -6.30%
- **歸因結論**: Selection 沒崩 (held-to-end alpha +0.31pp)，**entry timing 錯過 4/24 大行情** + **產業配置偏離領漲族群** 是主因；MA5_BREAK 1.72pp 是次要成本

---

## 票 #1 — 產業 Beta 暴露稽核 (P0, 1.5 day)

### Why
4/23 batch 21 檔只配 1 檔半導體（且為輸家 6510），其他電子/光電/通信佔 57%。本週 TWII 領漲族群是半導體，Selection 在族群配置上偏離 beta 來源。需確認這是單週現象還是系統性偏差。

### Scope
建立 `scripts/sector_beta_audit.py`，輸出 `ml/reports/sector_beta_audit_<window>.{csv,md}`。

對任意 prediction window（CLI 參數 `--start --end`）：
1. 拉出該窗口內所有 V2 Champion 已選 picks 的產業權重分佈
2. 對齊同窗口 TWII 各 TSE 產業指數（或以 TSE 產業 ETF 替代）的同期報酬
3. 計算 **產業權重 × 該產業同期報酬** 的貢獻分解（Brinson-style attribution）
4. 標出「low-weight + high-return」（錯過的 beta）和「high-weight + negative-return」（押錯的）

### 驗收條件
- [ ] 對 2026-04-23 ~ 2026-04-29 跑出 attribution，明確顯示半導體/電子零組件等領漲族群的「應配 vs 實配」缺口
- [ ] 報告必須產生機器可讀的 JSON（`sector_alpha_decomposition`），讓未來能納入 dashboard
- [ ] 同產業內個股報酬離散度 > 5% 時要在報告標註（避免「平均產業表現」誤導判斷）

### 不在範圍內
- 不改 selection 模型本身
- 不改 sector cap 規則（目前 20%）

### 依賴
- TSE 產業指數資料：先確認 [大盤指數/](../../大盤指數/) 是否有；若無，列為 follow-up issue 補抓

---

## 票 #2 — Entry Timing Rule Audit: Open[t+1] vs Close[t+1] (P1, 2 day)

### Why
4/23 picks 在 4/24 開盤進場，4/24 TWII 從 37862 開盤跳到 38932 收盤 (+2.83%)，**進場日就錯過大半行情**。Open[t+1] 規則設計目的是反映可實際成交的價格，但在強勢盤可能犧牲過多 selection alpha。需以資料判斷規則是否該調整。

### Scope
寫 `scripts/entry_timing_replay.py`，對 Q1 2026 (`2026-01-01 ~ 2026-04-29`) 所有歷史 prediction CSV 跑下列 4 種 entry 規則回放：

| Variant | Entry | Notes |
|---|---|---|
| A (baseline) | Open[t+1] | 現行規則 |
| B | Close[t+1] | 收盤進場 |
| C | VWAP[t+1] | 全日成交均價 |
| D | Open[t+1] 但若當日 TWII open-to-mid > +1% 則延後到 t+2 | 強勢盤 buffer |

### 驗收條件
- [ ] 輸出 `ml/reports/entry_timing_audit_q1_2026.{csv,md}` 含每個 variant 的 basket cum return / MDD / win rate / avg hold / entry slippage proxy
- [ ] 按 TWII 同日漲跌幅分桶（≤-1%, -1~0%, 0~+1%, +1~+2%, ≥+2%）顯示各 variant 表現
- [ ] 結論段必須回答：在 TWII 強勢日 (≥+1%)，是否有 variant 顯著優於 baseline；trade-off 是什麼
- [ ] **不可直接改 production**，只是研究產出

### Out of scope
- 部分 fill / split-order 不在本票範圍（若 audit 顯示需要才開新票）

### 依賴
- 既有 `scripts/v2_exit_overlay_backtest.py` 的 prediction CSV 讀取邏輯可重用

---

## 票 #3 — MA5_BREAK 樣本累積 + 同 entry exit replay 框架 (P1, 2 day)

### Why
本週 MA5_BREAK 17 次，但 hit rate 50/50 (9 反彈 / 8 續跌)，反彈幅度 (+7.34%) > 續跌幅度 (-3.38%)，淨成本 +1.72pp。樣本仍不足以下結論。需建立「同 entry、不同 exit policy」的 fixed-snapshot replay 基礎設施，待累積 ≥10 筆樣本後做嚴格 A/B。

### Scope
1. 建 `paper_portfolio_v2_champion.db` 的 closure artifact dumper：每週把已結清部位連同進場日 raw feature snapshot 存到 `ml/reports/closures/<exit_date>.parquet`
2. 寫 `scripts/exit_policy_same_entry_replay.py`，吃 closure parquet + 用同一組 (ticker, entry_date, entry_price, raw_features) 套用以下 exit 變體並計算每筆 MDD / final P&L / hold days：
   - `ma5_break` (baseline)
   - `ma5_break_with_buffer_2d` (進場後 2 天內不觸發)
   - `ma5_break_reduce_only_50pct` (觸發時減半倉而非全出)
   - `ma5_break_with_twii_strong_filter` (TWII 5DMA 上彎時不觸發)
   - `no_exit_until_20d` (對照組)
3. 報告寫到 `ml/reports/ma5_break_same_entry_replay_<asof>.md`

### 驗收條件
- [ ] Closure dumper 跑得起來且正確 dump 4/23 batch 的 21 筆已結清
- [ ] 同 entry replay 對 baseline 重現的 P&L 與 paper_portfolio_v2_champion.db 實際 realized 誤差 < 0.1pp（驗證 replay 邏輯正確）
- [ ] 報告呈現各 variant 的勝率、平均 P&L、MDD 分佈
- [ ] **不下結論修改 production**，明確標註當樣本 < 10 時不做 promotion

### 依賴
- 票 #2 的 entry replay 框架可共用部分讀檔/估價程式碼

---

## 票 #4 — V2 Overlay Decision 升級為 exit_only_ab (P2, 1 day)

### Why
[ml/reports/overlay_validity_audit.md](../../ml/reports/overlay_validity_audit.md) 已點出當前 overlay watch 是 `strategy_comparison`（讀 prediction CSV replay）而非 `exit_only_ab`（讀 champion DB）。要讓 overlay decision 真的能影響 production，需直接 replay `paper_portfolio_v2_champion.db` 的 unified_positions/unified_marks。

### Scope
擴充 `scripts/v2_overlay_decision.py`：
1. 新增 `--mode exit_only_ab` 旗標
2. 該模式下不再讀 `predictions_*.csv`，改讀 `paper_portfolio_v2_champion.db`，以 unified_positions 的 (ticker, entry_date, entry_price) 為基準替換 exit policy
3. 升級 `wording_mode` 從 `strategy_comparison` 變 `exit_only_ab`
4. 維持 `CHAMPION_INSUFFICIENT` gating（< 15 mark dates 仍視為 insufficient）

### 驗收條件
- [ ] `--mode exit_only_ab` 跑得起來且不依賴 prediction CSV
- [ ] 對 baseline 重現的勝率/MDD 與 unified_positions 實際 realized 一致（誤差 < 0.1pp）
- [ ] tests/test_overlay_decision.py 補一個 exit_only_ab 模式的 case
- [ ] 在 `ml/reports/v2_overlay_decision_latest.md` 的 audit 區段加 `mode: exit_only_ab` 欄位

### 依賴
- 票 #3 的 closure artifact 結構可直接複用

---

## 排程建議

| 週次 | 票 | 為什麼這週 |
|---|---|---|
| 2026-W18 (5/4-5/8) | #1 | 最關鍵的根因驗證，且只 1.5 天 |
| 2026-W18 (5/4-5/8) | #3 (前半 closure dumper) | 不阻塞 #1，可平行 |
| 2026-W19 (5/11-5/15) | #2 | 等 #1 結論再決定要不要做 D variant |
| 2026-W19 後 | #3 後半 + #4 | 等 closure 累積 |

## 不該做的事（明確排除）

- ❌ **不要改 MA5_BREAK production 規則** —樣本不足且本週 alpha 缺口主因不在此
- ❌ **不要改 sector cap 上限** — 等票 #1 結論
- ❌ **不要追加 entry signal 過濾** — 票 #1 結論先出來再討論

## Memory 更新需求

完成票 #1 後，更新 [project_v2_champion_week1_attribution.md](../../C:/Users/cheng/.claude/projects/f--stock/memory/project_v2_champion_week1_attribution.md)，標註結論與後續行動。

---

## 2026-05-01 PM Addendum — Attribution 驗收後優先級重排

### 最新驗收結論

票 #1 初版已完成，後續 #1.5 修正 benchmark 後，attribution 可以對齊 Champion vs benchmark alpha 缺口：

| 項目 | 結果 |
|---|---:|
| Benchmark mode | `market_cap_sector_index` |
| 實際資料來源 | `local_turnover_value_weighted_sector_proxy` |
| Fallback 原因 | 本地尚無官方 TSE 產業指數，日K 也無 shares outstanding / market cap 欄位 |
| Champion return basis | -0.21% |
| Benchmark return | +5.33% |
| Champion vs benchmark alpha | -5.54pp |
| Allocation effect | -4.24pp |
| Selection effect | -1.30pp |
| Reconciliation error | 0.00pp |
| TWII scale residual | +1.11pp |

解讀：本週 alpha 缺口主因不是 entry timing，也不是 MA5_BREAK。`-5.54pp` local proxy alpha 中，`-4.24pp` 來自 allocation，`-1.30pp` 來自同產業 selection。TWII scale residual 主要反映 local traded-value proxy 與 TWII 市值加權之間仍有尺度差，尤其大型權值股效應。

### 結構性訊號

| 優先 | 訊號 | 結論 |
|---|---|---|
| P0 | 半導體業 `UNDERWEIGHT_HIGH_RETURN` | Champion 16.20% vs benchmark 46.12%，active -29.92pp；sector +7.05%，且同產業 selection 仍跑輸 |
| P0 | 通信網路業 `OVERWEIGHT_NEGATIVE_RETURN` | Champion 32.30% vs benchmark 5.28%，active +27.02pp；sector -5.38%，單一產業吃掉主要 alpha 缺口 |
| P1 | 電子零組件業 `UNDERWEIGHT_HIGH_RETURN` | Champion 5.62% vs benchmark 20.60%，active -14.98pp；allocation 與 selection 雙殺 |

### 待釐清：通信網路 32.30% vs 20% Sector Cap

`sector_beta_audit` 的 `champion_weight` 是 `2026-04-23 ~ 2026-04-29` 跨 prediction 日累計目標權重，與單日 Top-N sector cap 不是同一個維度。仍需確認單日 cap 是否真的生效：

1. 若 4/23、4/24、4/27、4/28、4/29 各日皆單日 sector ≤ 20%，則只是累計語意差異，需在 audit 報告補註。
2. 若任一單日超過 20%，則視為 sector cap 行為 bug，需找 root cause 並 patch。

---

## 新票與優先級

## 票 #5 — V2 Selection Sector Bias 根因分析 (P0, 2 days)

### Why

`-5.54pp` alpha 缺口中，`-4.24pp` 來自 allocation。這是 model-side / scoring-side sector bias 問題，不是 exit overlay 或 entry timing 的主因。

### Scope

1. 拉 V2 prediction model 過去 60 天 Top30 sector distribution，對照 TSE 大盤 sector market-cap 或可用 proxy 分佈。
2. 檢查 V2 對半導體業、電子零組件業的 `leaderboard_score` / `pred_return_20d` / `prob_edge` 分佈是否系統性偏低。
3. 檢查 V2 對通信網路業、其他電子業的 score 分佈是否系統性偏高。
4. 檢查 feature importance 或 SHAP-like attribution 中是否有 sector-correlated feature，使特定產業天然高分或低分。
5. 分析 bias 是來自 raw model score、penalty overlay、alpha classifier gate、sector cap 後排序，或 tradability / production gate。

### 驗收條件

- [ ] 產出 `ml/reports/v2_selection_sector_bias_<window>.{csv,md,json}`。
- [ ] 報告必須回答：「V2 對半導體業的 score 中位數 vs 通信網路業的 score 中位數差距是多少？」
- [ ] 報告拆出 raw score、penalty 後 score、final leaderboard score 三層，避免把 scoring 問題誤判為 sector cap 問題。
- [ ] 報告列出過去 60 天每個 prediction day 的 Top30 sector weights 與 benchmark active weights。
- [ ] 不改 production model、sector cap、entry filter；只做根因分析。

### 依賴

- 票 #1.5 的 benchmark mode 與 sector attribution reconciliation。

---

## 票 #6 — Sector Cap 規則行為驗證 (P1, 0.5 day)

### Why

Audit 顯示通信網路累計權重 32.30%，高於 CLAUDE.md 所述單一產業 Top-N 20% cap。需確認這是跨日累計語意差異，還是 cap 計算/套用 bug。

### Scope

1. 對 `2026-04-23`、`2026-04-24`、`2026-04-27`、`2026-04-28`、`2026-04-29` 每個 prediction 日獨立檢查 Top-N sector cap。
2. 比對 `apply_sector_cap` 的輸入、輸出與 `unified_signals_YYYY-MM-DD.csv` 實際入帳部位。
3. 若單日皆 ≤ 20%，在 audit 報告補註「跨 prediction 日累計可超過單日 cap」。
4. 若有單日 > 20%，找出 root cause 並 patch。

### 驗收條件

- [ ] 產出 `ml/reports/sector_cap_validation_20260423_20260429.{csv,md,json}`。
- [ ] 每個 prediction day 都列出 sector counts、sector target weights、cap limit、是否 pass。
- [ ] 若發現 bug，補測試覆蓋 cap 行為。

---

## 票 #1.6 — Audit 報告語意補強 (P2, 0.5 day)

### Why

避免未來讀者把跨日累計 `champion_weight` 誤解為單日 sector cap 失效，也避免把 local traded-value proxy 當成 TWII 市值加權 benchmark。

### Scope

1. 在 Sector Attribution 表頭或 Scope 加註：`champion_weight` 是跨 prediction 日累計目標權重，與單日 sector cap 不同維度。
2. 在 TWII scale residual 說明加註：local proxy 是 traded-value 加權，TWII 是市值加權，差異可能來自大型權值股效應。
3. 若票 #6 證明單日 cap 都 pass，將該結論連到 audit 報告 Notes。

### 驗收條件

- [ ] `ml/reports/sector_beta_audit_20260423_20260429.md` 有清楚語意註解。
- [ ] JSON summary 也保留 benchmark fallback 與 weight basis 欄位。

---

## 優先級變更

| 票 | 原優先級 | 新優先級 | 決策 |
|---|---:|---:|---|
| #5 V2 Selection Sector Bias | NEW | P0 | 立即排入主線 |
| #6 Sector Cap 規則行為驗證 | NEW | P1 | 優先釐清 cap 是否失效 |
| #1.6 Audit 語意補強 | NEW | P2 | 跟 #6 結論一起補報告 |
| #2 Entry Timing Audit | P1 | P3 | 凍結到 #5 結論出來 |
| #3 Same-entry exit replay 後半 | P1 | P1 | 可平行，但不阻塞 #5 |
| #4 Overlay Decision exit_only_ab | P2 | P2 | 等 #3 replay 基礎穩定後接 |

### 新排程建議

| 週次 | 票 | 為什麼這週 |
|---|---|---|
| 2026-W18 (5/4-5/8) | #5 | allocation 是主因，需先釐清 model/scoring sector bias |
| 2026-W18 (5/4-5/8) | #6 | 半天內確認 sector cap 是語意差異還是 bug |
| 2026-W18 (5/4-5/8) | #3 後半 | 可平行接 closure parquet 做 same-entry replay |
| 2026-W18/W19 | #1.6 | 依 #6 結論補 audit 語意 |
| 2026-W19 後 | #2 | 只有在 #5 顯示 entry timing 仍可能是主因時升級 |
| 2026-W19 後 | #4 | 等 #3 replay 誤差 < 0.1pp 後升級 |

### 新明確排除

- 不因本次 attribution 直接改 sector cap。
- 不因通信網路 overweight 直接加 sector blacklist。
- 不重開 entry timing audit，直到 #5 回答 sector bias 是否來自 model/scoring。
- 不把 `local_turnover_value_weighted_sector_proxy` 稱為官方市值加權 benchmark。

---

## 2026-05-03 PM Addendum — Sector Cap 驗證後 scope 修訂

### 票 #6 結論

`sector_cap_validation_20260423_20260429` 顯示單日 Top30 sector cap 沒有失效：

- `apply_sector_cap` 每日單一產業皆 ≤ 6/30。
- production penalty-overlay cap 與 `unified_signals.rank_20d` ticker order 100% 對齊。
- 通信網路 32.30% 週累計權重來自跨 prediction 日累計，加上 tradability / market-regime gate 後 target-weight denominator 縮小，不是 cap bug。

### 票 #5 scope 修訂：V2 Selection × Gate Funnel

原本 raw / penalty / final score 三層仍保留為描述性背景，但主追因改成 gate funnel：

1. `predictions` raw Top30 → penalty + sector cap 後的 `rank_20d`
2. `rank_20d` → tradability gate 後 active target
3. tradability → market-regime gate 後 active target
4. 每個 gate 依 sector 計算 before / after / kill rate

核心問題改為：

- 為什麼通信網路在 gate 後存活率高？
- 半導體是在 selection 階段已經低，還是在 gate 階段被砍？
- gate 後 target-weight re-normalization 對 allocation alpha 的貢獻是多少？

### 票 #5.1 — Minimum Effective Positions 設計 (P1, 0.5-1 day)

Why：4/24～4/29 多個 prediction day 只有 4～7 檔 active，現行 target-weight re-normalization 會讓單檔曝險升到 14～25%，單一產業曝險升到 40～60%。

Scope：

1. 統計最近可用 canonical `unified_signals_YYYY-MM-DD.csv` 的 active count 分佈。
2. 對 active < 10 的情境比較三種 fallback：
   - `cash_if_under_min`: active < N 時全現金。
   - `keep_cash_buffer`: active < N 時不重正規化，剩餘權重保留現金。
   - `relax_gate_to_min`: active < N 時依 `rank_20d` 補回被 gate 擋掉的標的至 N 檔。
3. 對 2026-04-23～2026-04-29 做 next-trading-day open → mark-date close 的同窗口反事實。

驗收：

- [ ] 產出 `ml/reports/min_effective_positions_20260423_20260429.{csv,md,json}`。
- [ ] 報告列出三種 fallback 的 return / alpha / concentration 比較。
- [ ] 明確標註 `relax_gate_to_min` 是研究上界，不可未經 gate-quality review 直接 promotion。
- [ ] 不改 production gate、model、sector cap 或 entry filter。
