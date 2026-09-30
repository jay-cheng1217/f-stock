# 架構審查後續工單 (post-review backlog)

來源：2026-06-30 `research/v2-exit-overlay` 合併入 main 前的整體架構高層審查。
本次已落地：抽常數 (turnaround margin)、main↔branch 正規合併、2 個 stale test 對齊。
以下為**刻意延後**的項目，依風險與依賴排序。

> 規範提醒：凡動到 filter / 防呆 / 推論路徑，落地前必須先建 baseline 並跑
> fixed-snapshot A/B 回測（CLAUDE.md 開發規範）。閾值調整 >±5pp 需 PM 確認。

> **Skill 對應**：每張工單標注要用的 skill（2026-06-30 裝入 `~/.claude/skills/` 的
> PM Skills + 既有專案 skill + 內建 `/code-review`、`champion-audit`）。格式：
> 主要 skill 起手 → 落地後用驗證 skill 收尾。

---

## P0 — 結構性風險（train/serve drift）

### RD-PR-001：收斂三套 dataset 建構路徑
- **問題**：snapshot/特徵建構邏輯有三份平行實作，靠各自維護保持一致 →
  train/serve skew 風險。
  - `ml/dataset.py::build_latest_snapshot`（train/predict）
  - `ml/dataset_t1.py`（T+1 軌道平行複製）
  - `app.py:480–1109` `_build_live_prediction_df` + `_load_live_*_override_df`（web live）
- **目標**：單一 snapshot 來源，web/predict/train 共用。
- **驗證**：重構前後對同一 fixed snapshot 跑預測，逐檔比對 pred_return_20d /
  recommendation 必須 byte-for-byte 一致（純重構不得改數值）。
- **Skill**：`rfc-writer`（先寫 RFC 設計收斂方案 + 三套差異盤點）→
  `architecture-decision-record`（記錄單一 snapshot 來源決策）→ 實作後
  `code-review-checklist` + `/code-review` → 落地前 `champion-audit`。
- **風險**：最高（最敏感路徑）。先做差異盤點再動。

---

## P1 — 維護性

### RD-PR-002：拆解 `app.py::prediction_explain`
- **問題**：單一函式約 1110 行（佔 app.py 30%），混雜防呆三層邏輯。
- **目標**：抽成獨立 `explain_service`，app.py 只留薄 router。
- **驗證**：對既有 explain 測試 + fixed snapshot 比對輸出文字/分類不變。
- **Skill**：`architecture-decision-record`（記錄抽 `explain_service` 邊界）→
  實作後 `code-review-checklist` + `/code-review`。

### RD-PR-003：router/service 分層
- `backend/routers/chat.py`(1416)、`my_holdings.py`(1363) 為 god file，
  business logic 應下沉 service。
- `warroom_service.py`、`tw_news_intel_service.py` 自建 `sqlite3` 連線，
  未走 `backend/db/engine.py` → 收斂到統一 DB 抽象。
- **Skill**：`architecture-decision-record`（DB 抽象層決策）+
  `database-schema-design`（盤 sqlite/duckdb 存取模式）→ `code-review-checklist`。

### 文案-PR-004：warroom / email 標籤用字不一致（需產品決策）
- 4 個既有測試紅燈（合併前即存在，非合併引入）：
  - `test_warroom_shortwave_feed.py`：`explain_action_label` 期望「波段小量」
  - `test_send_daily_email_encoding.py`：entry candidate 區塊用字
- **需先決定正式文案用字**，再讓 test 與 production 對齊。
- **Skill**：`prd-template`（把正式文案/狀態用字寫成小 spec 給 PM 拍板）→
  定案後 `code-review-checklist` 對齊 test 與 production。

---

## P2 — 衛生 / infra

### 衛生-PR-005：repo 瘦身 —— ✅ 已盤點（2026-06-30），結論：無安全清理空間，降級為前瞻準則
**原假設**（review 初判）：`ml/reports/` 638 檔每日累積膨脹 git、model binary 該 untrack。
**盤點結果推翻此假設**：

| 類別 | 數量 | 處置 |
|---|---|---|
| ml/models（5 `lgbm*.txt` + 21 `*_meta.json` + selection/registry + 9 csv） | — | **絕不動**。`model-artifact-protection` skill Hard Rule：**所有** binary/meta 皆受保護（live predict / model selection / pins / 事故鑑識 / A/B 都會用），非僅 pinned；且 2026-05-16 教訓證明 binary 重生會 sanity FAIL 16pp，不可靠重生 → `git rm --cached` 會讓 fresh clone 永久缺檔。 |
| ml/reports forensic/決策 trail（audit002_*、`*_cl3_backtest`、closures、req*/rd*/inc*/diag*、`*_attribution`、`*_replay`） | **402** | **保留**＝殺實驗/做決策的證據鏈，刪＝丟審計軌跡，違反事故謹慎文化。 |
| ml/reports 每日重生診斷（chipk_model_diagnosis、momentum_continuation_backtest） | 26 / **216KB** | 理論可清，**但 0 個 `*_latest` 指標** → dated 檔是唯一副本，刪了還丟資料，非冗餘。 |

**量化**：整個 `ml/reports` 追蹤體積僅 **36.7MB**（repo 真正大物 `stock.duckdb` 1.1GB 早已 gitignore）。638 是 branch diff insertion 行數的放大錯覺，實際 footprint 不大。安全可清僅 ~216KB 且有副作用 → **CP 值為負，不動手**。

**修正建議（前瞻準則，取代原清理動作）**：
1. 每日重生型報表（chipk_diagnosis、momentum_backtest）**未來**先加 `*_latest.*` 指標，再把 dated 版納入 `.gitignore` 停止累積。
2. 既有 forensic trail 一律保留；要降低 active 目錄雜訊就移到已 gitignore 的 `ml/reports/archive/`，**只移不刪**。
3. model binary 永不 untrack。
- **狀態**：CLOSED（wontfix-as-scoped）/ 準則併入日常。原 Skill 對應（`model-artifact-protection` + `dependency-audit` + `runbook-writer`）已於本次盤點使用完畢。

### 衛生-PR-006：scripts/ 分層
- 163 個 py，~25 個 `backtest_*` 變體 + 大量編號一次性腳本混在同層。
- 分 `scripts/research/`（可凍結封存）vs `scripts/production/`。
- **Skill**：`architecture-decision-record`（記錄分層準則：哪些算 production
  入口、哪些封存）→ `code-review-checklist`。

### 衛生-PR-007：設定去重 / 依賴
- 路徑常數在 `backend/config.py` 與 `ml/config.py` 各一份 → 收斂。
- overheat `0.40` 在 `thresholds.py` 與 `config/sector_thresholds.yaml` 兩寫。
- `requirements.txt` 為 pip freeze 全量 dump 且 pytest 未列入 → 分
  `requirements-dev.txt`。
- **Skill**：`dependency-audit`（盤依賴：直接 vs 傳遞、過期、未鎖）。

### ENV-PR-008：測試 DB 隔離
- `test_entry_filter_benchmark.py` 直開 `stock.duckdb`，與執行中行程（web
  server）搶鎖 → IOException。改為測試專用唯讀 copy 或 fixture DB。
- **Skill**：`runbook-writer`（測試 DB 隔離 SOP）+ `code-review-checklist`。

---

## 🔴 P0 — DATA-PR-009:V2 模型法人特徵污染 → 重訓決策(PM 拍板)

- **開票**:2026-07-02(TPEX 投信/自營欄位錯位修復後的連鎖影響)
- **狀態**:**選項 A 執行完畢(2026-07-02)— CL3 FAIL,不升級,production 維持 pinned v2.6**。
  執行鏈:髒 cache 隔離 → Stage1 重訓(1.83M 乾淨樣本)→ Stage2 ranker → 乾淨 OOF fold
  重建(req032_step4_dataA)→ CL3 gate(dataA_cl3_final_20260702.md)。
  結果:新 Stage1 holdout **−0.017 vs 舊 −0.054(乾淨特徵確實改善預測)**,但 fold 組合層
  alpha +1.90% < 舊 +3.54%、MDD/Sharpe/Calmar 皆退 → **FAIL 全部四項 gate**。
  解讀:與 5 月事故一致——pinned 20260508 為不可重現的幸運訓練,任何重訓在 fold 上皆輸它;
  「髒特徵傷模型」與「舊模型碰巧強」同時為真。
  **PM 已裁示(2026-07-02):兩模型並存、看實際績效** —— shadow 每日對決已上線
  (`scripts/shadow_dataA_tracker.py`,每晚自動:雙方各記 Top30 → 20日後計 forward →
  `shadow_dataA_tracking_latest.md`);**N≥60 且 shadow−champion 顯著為正 → 重啟升級討論**
  (仍須重過 CL3/audit)。原建議:
  skew 以 guardrail 熔斷率監控(原 B 方案自動生效)。首次 CL3 報告(dataA_clean_20260702)
  因腳本預設抓到 0512 舊 fold 無效,已以 --new-fold 補正重跑。

### 事實(已量化)
1. TPEX 法人欄位錯位 6 年(2020 起):上櫃股 Trust=外資自營淨(≈0)、Dealer=外資合計+投信。
   資料已於 2026-07-02 修復並全量重建 1,576 天、重併日K(對帳原相分毫不差)。
2. **V2 模型特徵直接吃髒欄位**(`ml/features/institutional.py`):`trust_cumsum_*`、
   `inst_total_*`(=F+T+D → 上櫃實為 2F+T)、`foreign_trust_sync`、`trust_reversal_buy`、
   `chip_diverge_*`。上櫃佔 universe **46%(879 檔)** → 近半 universe 的法人特徵六年皆錯。
3. **production chip_momentum_top5 filter 亦吃同批特徵**(inst_total_20d_norm、
   trust_cumsum_20d_norm)——其 +320% 回測是在髒特徵下算的(一致性髒,故仍有效,但非最優)。
4. **立即風險:train/serve skew**——日K 已修乾淨,今晚起 serving 特徵為乾淨值,
   但 pinned 模型(v2.6, 20260508)是髒特徵訓的;上櫃股 inst_total 尺度腰斬式變化。
   緩解:特徵有做每日截面百分位(_apply_percentile_ranking),尺度衝擊被部分吸收,
   但上櫃/上市相對排名會系統性移動。

### 選項(PM 擇一)
- **A. 完整重訓(建議,但工程大)**:乾淨資料重訓 Stage1+Stage2 → 依
  [[feedback-two-stage-retrain-gate]] 重過 CL3 → 依 CLAUDE.md incident closure 跑
  same-snapshot A/B(單一 DuckDB as-of frame、join ticker+date、報 prob_edge delta)
  → 過門檻才升 production。
- **B. 觀察先行(保守)**:維持 pinned 模型,先監控 2-4 週 guardrail 熔斷率/攔截率與
  Top30 組成變化(尤其上櫃佔比突變),異常再啟動 A。
- **C. 混合**:先跑 shadow 重訓(不升 production)與現行並行盲測,累積證據後再決。

### 驗證要求(不可繞過)
- 兩階段重訓 gate(Stage2 必重訓、CL3 必重過)、same-snapshot A/B closure artifact、
  scoreboard 註冊重評。禁止只看 OOF 指標升級。

### Skill 對應
- 主要:(RD)重訓 pipeline — 輔助:`champion-audit`(升級前)、`/code-review`;
  監控:`ml/reports/` guardrail 熔斷率日誌。


---

## 2026-09-23 審查補遺 backlog(SA 核對回饋新增)

1. **momentum 回測 research-only 警告下沉到輸出**:601638f6 只把警告寫在腳本
   docstring;應同步出現在輸出 Markdown / JSON(報告讀者看不到腳本註解)。
   連同 P1-3 完整對齊(共用正式條件、首日進場、除息還原)一起處理。
2. **舊勝率評估器修正**:historical_win_rate 評估器口徑問題(SA 2026-09-23 指出,
   未含在本批)。
3. **T+1 目標名稱與校準口徑修正**:T+1 research-only 資產的目標命名與校準口徑
   需修正(SA 2026-09-23 指出,未含在本批)。
