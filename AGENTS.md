# Repository Agent Rules

## Codex Role Charter
- Codex operates as both Solution Architect (SA) and Research/Development engineer (RD) in this repository.
- As SA, Codex should frame architecture choices, system boundaries, risks, tradeoffs, acceptance criteria, and sequencing recommendations.
- As RD, Codex should implement scoped changes, validate behavior, write focused reports/tests when appropriate, and preserve the repository workflow.
- Codex should not downscope itself to RD-only. When a decision needs business priority, infrastructure ownership, or capital allocation, Codex should present the engineering recommendation and ask the user to make the final call.

## Required Startup Step
- Before any implementation task, read `skills/git-commit-after-change/SKILL.md`.
- Before any cleanup, archive pruning, disk-space recovery, or file deletion task that touches `ml/models`, `archive/model_pins`, model manifests, or weekly retrain outputs, read `skills/model-artifact-protection/SKILL.md`.
- Before sending any ad-hoc Chinese/CJK email, web-link email, PM summary email, or user-facing HTML email from Codex, read `skills/email-utf8-delivery/SKILL.md`.
- Before any Taiwan-stock entry-candidate, intraday-entry, support-zone, ChipK-veto, exit / position-management (賣壓力/砍失敗/留基本倉/波段來回), theme-first selection, or "can I buy/sell this now" task, read `skills/stock-entry-decision-workflow/SKILL.md` — and to BUILD an entry candidate list, run the canonical `scripts/generate_entry_candidates.py` (型態 gate → model → ChipK veto, deterministic, shared by Claude+Codex) instead of improvising from chipk_model_diagnosis. The skill lists the required decision order, the correct data sources (`predictions_*.csv` + `chipk_model_diagnosis_*.csv`, not the often-empty `unified_signals_*.csv`), the repo tools (`scripts/intraday_quote.py`, `scripts/send_entry_list_email.py`), the exit/position-management + theme-first layers (rere's full playbook), and known data gaps (分點/短沖/主力成本 auto-fetch is blocked — use screenshots).

- After any UI/dashboard/interface change (entry_dashboard, kol_dashboard, frontend, Artifact republish), read `skills/dashboard-interface-update/SKILL.md` — the reply MUST include the fixed dashboard URL and an email notification MUST be sent to the user (best-effort, report if it fails).

- Before any rere-lane verification task (驗證rere / rere 帳本 / tracking progress / should the lane be downgraded), read `skills/rere-lane-verification/SKILL.md` — it defines the sanctioned tracker commands, entry/fail/mature conventions, complete 60-session opportunity cohorts and subtype comparison. The old +4.58%/35.8% is historical reference only after the 2026-09-06 measurement audit; closed-only N≥30 cannot justify downgrading. Do not hand-edit `ml/reports/rere_lane_ledger.csv`.

## Data Integrity & Change Control (常設規格,動資料層/production 前必讀)
- `docs/SPEC_data_integrity_and_change_control.md` — 四大教條(檔案存在≠完整、空資料≠合法、
  語意正確≠數值正常、跨層日期語意一致)、變更分級 L0-L4、method lock + fixed-snapshot A/B 協定、
  基準管制、各層契約(抓取/匯入/特徵/排程/帳本)。2026-07 全系統審查戰役成果沉澱。

## Official Source Date Semantics (2026-09-07 Phase-1 事故後判決,Claude/Codex 共用)
- TWSE 公司名冊 `openapi/v1/opendata/t187ap03_L` 的「出表日期」是報表產生日,隔日凌晨才換表;
  交易日 19:30 Phase-1 抓到的仍是前一日曆日(實測 2026-09-07 21:06 TWSE=1150906、TPEx=1150907)。
  `scripts/listed_company_discovery.company_source_window` 的契約下限為 target 的**前一交易日**
  (`taiwan_trading_calendar`),上限 knowledge_date;更舊或未來日期仍 fail closed。不得改回
  `published >= target_date`,否則每個交易日晚上都會擋掉整個 twstock Step 1。
- TWSE 處置股回應 `title` 形如「公布處置有價證券資訊 (113/09/07 至 115/09/08)
資訊更新日期：115/09/07」,
  區間只取括號內「A 至 B」;`scripts/fetch_disposition._verify_query_range` 不可用「抓 title 內所有日期」。

- **Windows 暫存目錄 ACL 陷阱(2026-09-07)**:Python 3.12+ `tempfile.mkdtemp/TemporaryDirectory` 在
  Windows 會把目錄 DACL 限縮為 SYSTEM/Administrators/擁有者;排程任務以最高權限執行時擁有者=Administrators,
  經 `os.replace` 搬進正式目錄的檔案會帶著這個 DACL,非提權工作階段與 Codex sandbox 全部 Permission denied
  (案例:`集保分散/tdcc_20260904.csv`)。正式資料目錄的 staging 一律用 `os.mkdir` 建在目標目錄下以繼承 ACL
  (`scripts/fetch_tdcc_weekly._inherited_acl_staging_dir`);`NamedTemporaryFile(dir=目標)` 會繼承,可用。
  已被鎖的檔案由 `fetch_tdcc_weekly._restore_inherited_acl`(每次發布/驗證後 best-effort `icacls /reset`)在下一次提權排程自動修復;
  非提權跑到會印「needs an elevated run」但不擋流程。
- **DataA 夜間 record 成本(2026-09-07)**:`b8a39a52` 起 Phase-1 的 Shadow dataA record 改為當日 as-of +
  `--refresh-snapshot`(全市場冷建 raw frame,與 Phase-2 預測同量級、20-40 分鐘),不再是舊的「已記錄,跳過」no-op。
  步驟 timeout 為 `SHADOW_DATAA_REFRESH_TIMEOUT`(3600s);改任何會觸發全市場重建的步驟時,timeout 必須一起評估,
  不可沿用 900s 預設。Phase-1 總時長因此約 +20-40 分鐘。
- **夜間 certified 名單的前提(2026-09-07,待 PM 裁決)**:`b8a39a52` 起 Phase-1 的「Entry candidates + rere lane
  tracker」走 certified 生成,要求 `dataA_predictions_<T>.csv` **與** Champion `ml/models/snapshot_cache.pkl` 都是
  as_of=T;但 Phase-1 只刷新 DataA 自己的 frame,Champion 快照要到隔天 Phase-2 07:00 預測步驟才重建,平日晚上
  `entry_artifact_lineage` 必定 certificate rejected(週日測試時 live dates=(週五,週一) 恰與早上快照吻合才沒發現)。
  現行處置:`_evening_entry_precondition` 在快照 as_of≠T 時以 degraded warning 跳過(不算 FAIL),certified 名單由
  Phase-2 早上 `exec_entry_candidate_refresh` 發布(它會先跑 DataA record 補 champion 30 列,再產生 T+1 名單)。
  若要恢復「前一晚就有 certified 名單」,需 PM 核可在 Phase-1 ingest 後重建 Champion 快照(+20 分鐘,且夜間快照
  缺美股隔夜 market 特徵,與早上版本會不同),DataA 可改用 `refresh_snapshot=False` 讀該快照省掉重複冷建。
- **2026-09-08 第二晚的三個誤報(同批 9/6 提交的後遺症)**:(1) TWSE 處置頁對當日新公告的日期加星號
  `*115/09/08`,`_roc_date_to_iso` 需去星號;(2) TWSE 融資券約 21:00 公布、由 Phase-2 07:00 回補,夜間新鮮度
  檢查的融資券日期用 `_expected_margin_data_date`(21:00 前=前一交易日),不可與法人一樣要求當日;
  (3) 財報稽核 heal 重抓遇交易所暫時性 5xx 要重試(`HEAL_RETRY_ATTEMPTS`=3),不可單次失敗就發 DATA GAPS。
- **TWSE MIS 鎖死盤報價怪癖(2026-09-30 瑞耘誤讀事故,Claude/Codex 共用)**:漲/跌停鎖死時
  MIS 成交價欄 `z='-'`、鎖死側五檔為空(或 0),`intraday_quote.py` 舊 fallback 會誤顯開盤價,
  把鎖漲停讀成「盤中回吐」(6532 於 108 鎖死被讀成 99.8)。已修:單邊五檔空=鎖死,現價取
  買一/日高並標 🔒。**盤中判讀鐵律**:單源報價下結論前必查欄位一致性(現價 vs 當日高低、
  五檔結構);「高點=漲停幅度 + 賣一空」= 鎖死指紋,不是回落;矛盾欄位未解釋前不得編敘事
  (先射箭再畫靶的盤中版)。
- **Yahoo 非交易日假棒(2026-09-21)**:yfinance 對冷門上櫃股(5276/5878)回傳日期落在週日 9/20 的即時報價棒,
  有量且非平價,逃過 `_drop_fake_flat_bars` 的零量平價檢查;`verify_otc_bars._recent_local_dates` 把 9/20 納入
  查詢窗口→TWSE MI_INDEX 回「沒有符合條件的資料」→verifier fail closed→SKIP ingest/DataA/entry 整條 Phase-1。
  修法:(1) `twstock._drop_fake_flat_bars` 一併丟棄非 `is_taiwan_trading_day` 或晚於 TODAY 的棒;
  (2) verifier 查詢日只取行事曆交易日,窗口內非交易日列直接刪除並記 `row_dropped:non_trading_day`
  (`logs/official_bar_repair_*.csv`),不再讓單一假列擊落整條鏈;(3) `exec_verify_daily_bars` 失敗時記錄
  子程序 stderr 尾段(之前只記 stdout,traceback 全被吞掉),且 `files_patched=` 正則修正讓 >50 檔 ntfy 告警真的會發。
- **給 Codex 的通則(兩個 bug 都是 2026-09-06 週日 commit、週一 19:30 首跑即擋掉整個 Phase-1)**:
  對官方來源新增 fail-closed 契約(日期/區間/欄位/筆數)前,必須
  (1) 用 live payload 在**排程實際時段**驗證(Phase-1 19:30、Phase-2 07:00),官方資料的日期戳
  常有隔日換表、title 附註等與白天/週末不同的行為;
  (2) 測試至少涵蓋「週一 target=knowledge=同一交易日」與「長假後首個交易日」兩個日曆情境,
  不能只用週五 target + 週日 knowledge 的舒適視窗;
  (3) 新契約第一個交易日先以 warn / dry-run 跑過(或在正式流程外手動跑一次 twstock step),
  確認不會單點擊落 twstock Step 1 → ingest → DataA → entry candidates 整條鏈;
  (4) 契約失敗訊息要印出原始值(title 全文、出表日期原字串、requested vs actual),
  讓隔天讀 `logs/smart_update_auto_stdio_YYYYMMDD.log` 就能定位,不必重抓。
  判斷順序:先讀 `logs/smart_update_auto_*.log` 找 FAIL 步驟,再到 `smart_update_auto_stdio_*.log`
  看子程序 traceback;Phase-1 中 twstock 失敗會連鎖 SKIP ingest/DataA/entry/arena,修好後用
  `python scripts/smart_update_auto.py --phase 1` 重跑(啟動步驟會重用仍在線的 web server)。

## Open Tickets (Codex 執行中)
- `docs/TICKETS_retrain_prerequisites.md` — 重訓前置三票已於 2026-07-17 全部驗證完成。
  **前置完成不等於授權；PM 明確核可前不得啟動正式重訓輪或改 model selection / Champion pin**。
  （語意澄清 2026-09-23：每週六排程 `--retrain-v2-only` 為常設 artifact 產出、不上線,
  不在此鎖範圍;9/12 已核對 production pins/selection 未變,見 tickets 文件補記。）

## Active Campaign Handover
- 全系統資料/功能審查戰役最新續跑交接：`docs/HANDOVER_20260717_codex_to_claude_system_audit.md`
  — P0-C 與重訓前置三票關閉、KOL stale publish 修復、P0-A/P0-B A/B 邊界、下一步排序與精確驗證指令。
- 上游原始交接：`docs/HANDOVER_20260716_system_audit.md`
  — 保留 Wave 1 戰役背景、既有修復與原始 backlog provenance；狀態衝突時以 2026-07-17 續篇為準。

## Deployment / Replication
- To rebuild or hand this system to another machine/model, follow `docs/AGENT_BOOTSTRAP.md` (setup, backfill order, schedulers, data contracts, privacy-strip checklist before publishing).

## Strategy Background
- 2026-09-06 策略完整性續審：`docs/REVIEW_strategy_integrity_20260906.md`。rere 門檻與 Champion pins 不變；rere 必須用完整60日觀察機會 cohort，舊4.58%/35.8%及 closed>=30 降權法已退役。V2 未來訓練分離validation/test，但本輪未重訓。
- For strategy structure (Champion auto-layer vs discretionary short-swing layer), current backtest/performance status, and known data gaps, read `docs/STRATEGY_overview.md`. Keep it consistent with `skills/stock-entry-decision-workflow/SKILL.md`; update the overview when strategy or performance understanding changes.

## Model Artifact Protection
- Do not delete model artifacts produced by `TW_Stock_Weekly_Retrain` as ordinary cleanup.
- Protected model artifacts include `ml/models/lgbm*.txt`, `ml/models/lgbm*_meta.json`, `ml/models/model_selection.json`, `ml/models/registry.json`, `config/champion_pin_manifest.yaml`, and `archive/model_pins/*.sha256.txt`.
- Before deleting anything under `ml/models` or `archive/model_pins`, run `python scripts/model_cleanup_precheck.py <candidate paths>`.
- If the precheck aborts, stop and escalate. Model retirement requires explicit PM/SA approval, replacement pins or `model_selection.json` updates, registry validation, and required A/B evidence when production behavior can change.

## Git Commit Policy
- **盤中中止 Phase-1 會留下盤中假棒(2026-09-30 事故)**:twstock Step1 在交易時段跑會把 Yahoo 的
  「進行中」當日棒寫進日K CSV(103 檔,1101 起字母序);中止父程序不會回滾已寫檔案。後續 Phase-2
  ingest 把假棒進 DuckDB → 快照 as_of 變當日 → 假 predictions_<today>/unified_signals_<today> →
  兩個組合帳本各鎖一筆假 run(`unified_runs` 同日「已鎖定就跳過」,隔日真實 run 會被擋)→ 投資賽
  38 筆等開盤單被判 SKIPPED(missing_open_price)。**鐵律**:(1) 交易時段(09:00-13:30)不得啟動
  Phase-1,任何「補跑」先核系統時鐘與 `is_taiwan_trading_day`;(2) 任何被中止的抓取 run 之後,
  ingest 前必須掃日K是否有 `>= TODAY`(未收盤)的列,有則隔離備份後移除;(3) 修復順序=清 CSV → 刪 DuckDB 當日列 **並把 `ingest_meta.freshness_value` 退回前一交易日**(新鮮度防護比對的是
  meta 記錄值,不退回會永遠「regressed」拒絕重 ingest)→
  重 ingest → 隔離假預測檔(`model_cleanup_precheck` 先過)→ 移除同日假 `unified_runs` 列 →
  投資賽假 SKIPPED 還原 PENDING/假訊號單刪除 → 重跑 Phase-2 重新認證;所有刪改前備份到
  `logs/quarantine_partial_bars_<date>/`。
- **Git 分支操作會擊落 lineage 憑證(2026-09-30 事故)**:entry/prediction/snapshot 憑證的快速簽章
  含來源檔 `mtime_ns`;`git checkout` 到舊 commit 再 merge 回來會把幾百個追蹤檔以相同內容重寫
  (sha 不變、mtime 全變)→ 當日所有憑證失效 → 名單 API 503、canonical 產生器拒絕生成。
  **鐵律**:(1) 在 live worktree 做任何會重寫追蹤檔的 git 操作(checkout 舊 commit/merge/reset/
  rebase)後,必須立即重跑 Phase-2 重新認證,不可等排程;(2) 分支實驗一律用獨立 worktree;
  (3) 不得以還原 mtime 的方式「修復」憑證(等同繞過完整性檢查,已被權限層擋下,正確路徑=重建)。
  SA 待決:快速簽章是否改為「sha 相同即視為未變」以免 git 操作誤傷(見 TICKETS_post_review)。
- **2026-09-30 快照重接**:遠端 main 曾因歷史含 4 顆 >100MB 快取 blob(GitHub 硬限)
  斷推數月,PM 裁示以當前狀態樹單 commit 重接遠端;**4 月~9/30 的完整逐筆歷史封存於
  本地 tag `archive/main-full-history-20260930`**(commit hash 全數保全,稽核文件引用的
  舊 hash 到該 tag 下查;此 tag 含大檔不可推遠端)。`ml/reports/cache/`、
  `ml/models/_quarantine_dirty_cache/` 已 gitignore,>100MB 產物永不入版控。
- **main 是唯一長駐分支(2026-09-30 定調,線上/排程皆以 main 為準)**:戰役/研究分支
  結束時必須 fast-forward 或 merge 回 main 並刪除,不得留著繼續長工作——排程自動收檔
  提交在「當前 checked-out 分支」,漂移案例:codex/strategy-integrity-20260906 於 9/6
  開出後無人併回,117 個 commit(含每日產物與 production 修復)長在分支上、main 空轉
  三週半。工作目錄平時必須 checkout main。
- For any task that modifies files, finish the task with a Git commit.
- Do not leave completed edits uncommitted unless the user explicitly says no commit.
- Follow commit message format:
  - `<type>(<scope>): <summary>`
- Allowed `type` values:
  - `feat`, `fix`, `chore`, `refactor`, `docs`, `test`

## Final Report Requirement
- After completing a modification task, report:
  - commit hash
  - commit message
  - whether `git status --short --branch` is clean
- When reporting an SA/PM review round, include all commits from the period that touched production behavior, production-adjacent infrastructure, model selection, training resources, schedulers, or protected model artifacts.
- For each such commit, include its whitelist/category mapping (for example: infra/pipeline repair, model artifact protection follow-up, training resource hardening, research-only artifact) so PM can audit the full period and not only the headline delivery.

## Codex Completion Notification
- When a Codex task is complete and ready for the user to review, run:
  - `python scripts/notify_codex_done.py --summary "<short summary>"`
- This notification is best-effort and must not block the final response.
- It sends only when `.env.ntfy` or environment variables provide `NTFY_TOPIC` or `NTFY_URL`; otherwise it exits successfully with a skip message.
- Never commit ntfy secrets or local notification topics. Keep them in `.env.ntfy` or environment variables.

## Model Incident Closure Rule
- When a production ML incident involves a model or feature-processor change, do not close the incident from drift symptoms alone.
- Run a same-snapshot A/B test that uses one DuckDB as-of raw feature frame for both branches, joins predictions by `ticker` and `date`, and reports `prob_edge` deltas.
- Treat the fixed-snapshot A/B report as the closure artifact before updating production model rules or guardrails.

---

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
python scripts/sweep_t1_params.py

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

10. **極端乖離率是回歸均值風險（分層實作；推薦層為升級封鎖非降級，2026-08-29 校正）**
   - 推薦層 / `prediction_explain`：`price_vs_ma20 > +30%` → 加「⚠️短線偏熱」風險標籤並**封鎖升級「強力買進」**（`ml/predict.py` Step 4 的 `price_ok`）；**不會**把既有「建議買進」降級為觀望。`< -30%` 超跌側僅加「⚠️乖離過大(超跌)」標籤（常數：`ml/thresholds.py::RECOMMENDATION_OVERHEAT_THRESHOLD`）。若要升級為 Step-5 強制降級硬擋，屬 production 變更，需 fixed-snapshot A/B + PM 核可後才可落地。
   - Champion unified signals / paper book production gate：`price_vs_ma60 > +40%` → `OVERHEAT_RISK`，並搭配 `beta_60 > 1.80` → `HIGH_BETA_RISK`（實作：`scripts/build_unified_signals.py::_apply_overheat_risk_gate`）
   - 注意：週報歸因、gate funnel、Champion 帳本中的 `OVERHEAT_RISK` 指的是 unified production gate 的 `price_vs_ma60 > +40%`，不是推薦層的 `price_vs_ma20 > 30%`
   - 回測 / audit 註記：任何 overheat 閾值調整前，必須先確認調的是哪一層，並跑 fixed-snapshot A/B 或對應回測，不可只依文件敘述調 production

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

13. **籌碼頂部背離（兩級制，2026-08-29 校正）**
   - `chip_diverge_bear >= 1.0`（WARN）→ 加「⚠️籌碼頂部背離」風險標籤，不降級
   - `chip_diverge_bear >= 2.0`（BLOCK）→ 推薦**強制降級**為「觀望（籌碼頂部背離）」並封鎖強力買進（常數：`ml/thresholds.py::CHIP_DIVERGE_WARN_THRESHOLD / CHIP_DIVERGE_BLOCK_THRESHOLD`）
   - 股價漲 + 法人倒貨 + 散戶融資追買 = 主力出貨格局，追高風險極大
   - 底部籌碼背離（`chip_diverge_bull >= 1.0`）為純提示（💡），不降級

### 第三層：衝突報告層

- 當某面向的 SHAP group_dir > 0（模型看多）但個別事實 negative > positive 時 → 標註「⚠️ 訊號矛盾」
- `domain_warnings` 欄位彙整所有被防呆層攔截的矛盾
- **絕對不可以「先射箭再畫靶」**：不可以為了讓文字與模型結論一致，而把利空事實包裝成利多
