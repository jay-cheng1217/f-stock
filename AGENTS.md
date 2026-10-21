# Repository Agent Rules

本檔只放一定要遵守的短規則,以及「什麼情況讀哪份文件」。事故經過、機制細節、系統參考與防呆規則全文在 docs/,需要時再讀
(2026-10-18 瘦身:原文逐字搬到 `docs/AGENT_LESSONS.md`、`docs/SYSTEM_reference.md`、`docs/SPEC_prediction_guardrails.md`)。
新增知識時:細節寫進對應的 docs,本檔只加一行規則+指引;`tests/test_agents_md_contract.py` 會檢查本檔大小與指到的檔案都存在。

## ⛔ 動手前紅線(Claude + Codex,優先於本檔其他所有內容)

**任何有副作用的操作**(重跑 pipeline/抓取、git checkout/merge/reset/rebase、改 DB/帳本、刪或搬檔)之前:

1. **先跑 `python -X utf8 scripts/preflight_check.py`**。有 STOP 就不做,先向用戶回報。
2. **盤中(交易日 08:30–14:30)不啟動 Phase-1/twstock/任何抓取**——會寫入未收盤假棒且中止無法回滾。
   程式已強制擋(`smart_update_auto` 拒絕,`--force` 不能繞過)。
3. **git 重寫工作樹的操作會擊落當日名單憑證(API 503)**;做了就必須立即重跑 Phase-2,不可碰 mtime。
4. **看起來像事故時,先回報、不自己修**。「日期/時段/資料新鮮度」的判斷一律以系統時鐘+preflight 為準,不以推論為準。
5. **數字矛盾先查、不編故事**(報價欄位互相矛盾、檢查結果與剛做完的事矛盾 → 先懷疑工具與自己)。
6. **所有破壞性修改前先備份**到 `logs/quarantine_*/`;改動必須能被還原。

事故細節與各項機制說明在 `docs/AGENT_LESSONS.md`;紅線本身不需要讀完全文就要遵守。

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

- Before changing data fetch, ingest, official-source contracts, or scheduler steps, read `docs/AGENT_LESSONS.md` (the incident record behind the standing rules below) together with `docs/SPEC_data_integrity_and_change_control.md`.
- Before modifying `prediction_explain`, recommendation classification, filters, or guardrail thresholds, read `docs/SPEC_prediction_guardrails.md` (rules 1-14).
- For commands, directory layout, model versions, schedules and data conventions (typhoon closures, fundamentals audit, TDCC buckets, ex-dividend adjustment), read `docs/SYSTEM_reference.md`.

## Data Integrity & Change Control (常設規格,動資料層/production 前必讀)
- `docs/SPEC_data_integrity_and_change_control.md` — 四大教條(檔案存在≠完整、空資料≠合法、
  語意正確≠數值正常、跨層日期語意一致)、變更分級 L0-L4、method lock + fixed-snapshot A/B 協定、
  基準管制、各層契約(抓取/匯入/特徵/排程/帳本)。2026-07 全系統審查戰役成果沉澱。

## 常駐規則(每條一行;細節與事故經過在括號指的文件)

### 資料與日期
- 整批預測/名單的日期用 `ml/prediction_dates.canonical_prediction_date` 或產物憑證的 as-of,不可用 `df["date"].iloc[0]`。
- 預測/名單憑證只對當下來源狀態有效;修復一律重跑產生,不可改名、搬檔或改 mtime 讓憑證通過。
- **手動寫入任何正式資料目錄之後、下一次排程匯入之前,跑 `python -X utf8 scripts/ingest_dry_run.py`**(用正式匯入程式預演;preflight 會提醒)。
- 正式資料目錄的暫存檔建在目標目錄下再 `os.replace`(Windows 暫存目錄 ACL 陷阱)。
- 盤中不跑會重建 `集保分散/tdcc_summary.csv` 的正式 fetcher(該檔在名單憑證來源內);TDCC 週資料每週被覆蓋,官方日期新於本地時先存原始檔。
- 對官方來源新增 fail-closed 契約前:用排程實際時段的 live payload 驗證、測「週一同日」與「長假後首日」、第一個交易日先 warn、失敗訊息印原始值。
- 官方來源日期:公司名冊出表日期下限=前一交易日;處置股 title 只取括號內區間、日期去星號;夜間融資券日期用 21:00 前=前一交易日;
  財報稽核 heal 遇交易所暫時性 5xx 要重試(`HEAL_RETRY_ATTEMPTS`),不可單次失敗就發 DATA GAPS。
- 停牌/終止上市櫃單一來源 `config/trading_status_events.json`;不要再往 `config/retired_tickers.csv` 加新案;3718 不串接 5371 價格歷史。
- 官方終盤校驗只在 Phase-1 內執行;不要重新啟用 TpexBarVerify 或另建校驗排程。
- 月營收:匯入代號一律取自檔名;`twstock.step6` 會重抓空值列、以官方彙總改正最近 12 個月(改正值是重編數字,回測有輕微前視)。
- 季報:官方彙總只列查詢當下已上市櫃的公司;季報更新程式尚未修(待 PM 排程),新上市股缺季與事後更正仍會累積。
- 盤中報價下結論前先查欄位一致性;「高點=漲停幅度+賣一空」是鎖死指紋,不是回落。
- 盤前讀證交所即時報價(MIS)拿到的是前一交易日的值;算開盤 gap 或「今日」數字前必須核對資料日期 `d`(2026-10-14~10-21 名單夜盤提示因此全錯)。
- 市場基準股票池用訊號/進場當日的流動性條件,不可用期末條件。
- 全市場重建類步驟要一起評估 timeout(DataA refresh 用 `SHADOW_DATAA_REFRESH_TIMEOUT` 3600s)。
- 夜間 certified 名單:Champion 快照 as_of≠T 時以 degraded warning 跳過,名單由 Phase-2 早上發布;要恢復前一晚就有名單需 PM 核可。
- 排程失敗:先讀 `logs/smart_update_auto_*.log` 找 FAIL 步驟,再讀 `smart_update_auto_stdio_*.log` 看 traceback;Phase-1 修好後 `python scripts/smart_update_auto.py --phase 1` 重跑。
  (以上各條的事故經過見 `docs/AGENT_LESSONS.md`「官方來源與資料層」)

### 盤中與事故修復
- 交易時段不得啟動 Phase-1;被中止的抓取 run 之後,ingest 前先掃日K `>= TODAY` 的列並隔離。完整修復順序見 `docs/AGENT_LESSONS.md`「盤中中止 Phase-1」條。

### Git 與合併
- main 是唯一長駐分支,工作目錄平時 checkout main;分支實驗一律用獨立 worktree,結束就併回並刪除。
- 合併一律 `bash scripts/merge_branch.sh <分支> <工作目錄> [--not-before YYYYMMDDHHMM]`(preflight、完整測試、憑證影響、改到匯入程式時跑匯入預演、推送、清理)。
- 在 live worktree 做 checkout/merge/reset/rebase 之後立即重跑 Phase-2;不可用還原 mtime 的方式修憑證。
- 允許失敗的測試只有登記在 `tests/gate_known_failures.txt` 與 `tests/gate_worktree_env_failures.txt` 的;新增一列要寫日期與原因。
- 測試不可碰正式環境(`tests/conftest.py` 擋真實 HTTP 與正式 log);需要連線的測試標 `@pytest.mark.allow_network`;會經過 `smart_update_auto.LOGGER` 的新測試先換空日誌。
- 4 月~9/30 的完整歷史在本地 tag `archive/main-full-history-20260930`(含大檔,不可推遠端);>100MB 產物不入版控。
  (事故經過見 `docs/AGENT_LESSONS.md`「Git、合併與測試」)

## ML 預測解釋防呆(摘要;全文 `docs/SPEC_prediction_guardrails.md`)
- 三層架構:模型計算層不動 SHAP 數值;防呆層的多空分類依財務常識,不可被 SHAP 方向翻轉;衝突報告層標「⚠️ 訊號矛盾」。
- 絕對不可「先射箭再畫靶」:不可為了讓文字與模型結論一致,把利空事實包裝成利多。
- 規則 1-14(本業虧損與轉機、估值虛胖與豁免、低基期、流動性 500 張、暴跌 40%、極端預測 30%、ATR 天花板、乖離分層、
  產業上限 20%、停損 −10%、籌碼頂部背離兩級)改任何一條前必讀全文,並跑回測或固定快照 A/B。
- 正式推論只用 `scripts/model_pin_registry.py` 驗證的 Champion pins;裸 `ml/predict.py` 有最新檔 fallback,不能當正式推論;
  正式推論、Web 解釋與儀表板要核對實際模型、來源快照與產物憑證,不能只看日期或檔名。

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
- 市場基準股票池用期初條件的原文與數字見 `docs/AGENT_LESSONS.md`「策略背景」。
- 2026-09-06 策略完整性續審：`docs/REVIEW_strategy_integrity_20260906.md`。rere 門檻與 Champion pins 不變；rere 必須用完整60日觀察機會 cohort，舊4.58%/35.8%及 closed>=30 降權法已退役。V2 未來訓練分離validation/test，但本輪未重訓。
- For strategy structure (Champion auto-layer vs discretionary short-swing layer), current backtest/performance status, and known data gaps, read `docs/STRATEGY_overview.md`. Keep it consistent with `skills/stock-entry-decision-workflow/SKILL.md`; update the overview when strategy or performance understanding changes.

## Model Artifact Protection
- Do not delete model artifacts produced by `TW_Stock_Weekly_Retrain` as ordinary cleanup.
- Protected model artifacts include `ml/models/lgbm*.txt`, `ml/models/lgbm*_meta.json`, `ml/models/model_selection.json`, `ml/models/registry.json`, `config/champion_pin_manifest.yaml`, and `archive/model_pins/*.sha256.txt`.
- Before deleting anything under `ml/models` or `archive/model_pins`, run `python scripts/model_cleanup_precheck.py <candidate paths>`.
- If the precheck aborts, stop and escalate. Model retirement requires explicit PM/SA approval, replacement pins or `model_selection.json` updates, registry validation, and required A/B evidence when production behavior can change.

## Git Commit Policy
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
