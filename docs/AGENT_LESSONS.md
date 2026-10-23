# 事故與機制紀錄(Agent Lessons)

原本寫在 `AGENTS.md` 的事故經過與機制細節,2026-10-18 依 PM 指示逐字搬到這裡(AGENTS.md 瘦身)。
`AGENTS.md` 只留一行規則與「什麼情況讀哪份文件」;要動對應機制前,讀這裡的原文。
新增事故時:細節寫進本檔對應段落,`AGENTS.md` 只加一行規則+指向本檔的指引。

## 官方來源與資料層(原 AGENTS.md「Official Source Date Semantics」整段)

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
- **TDCC 週資料只保留最新一週(2026-10-05 PM 提醒)**:集保 opendata `getOD.ashx?id=1-5` 每週被新一週覆蓋,
  **錯過就無法從同一來源補抓**(`backfill_tdcc.py` 的逐檔歷史查詢是另一條慢路徑,保留期未查證)。
  系統靠平日晚上 Phase-1「Update TDCC weekly data」入檔(常態=週一 20:1x);週六重訓失敗**不影響**這一步,
  但若整週 Phase-1 的 TDCC 步驟都失敗,該週資料永久缺失。操作規則:(1) 發現官方資料日期 > 本地最新 raw 週時,
  先下載原始檔到 `logs/quarantine_tdcc_<date>/` 當保險(格式與 `集保分散/tdcc_<date>.csv` 相同,可直接轉入);
  (2) **盤中不可跑正式 fetcher**——它會重建 `集保分散/tdcc_summary.csv`,該檔在快照/名單憑證簽章內
  (`ml/dataset._raw_cache_source_patterns`),一動名單 API 即 503;正式入檔留給 Phase-1 或收盤後。
- **月營收三缺陷與回補(2026-10-17,PM 核可)**:`月營收/` 1,246 檔只有 2025-09 起(3 月擴大股票池時用重試模式建檔,
  `_resolve_revenue_update_window` 重試起始寫死 2025-09)、3 檔 KY 歷史全是空值列(更新只看月份在不在,空值永不重抓)、
  60 檔 142 列與官方現值不符(公司事後重編,本機每月只抓前 2 個月)。已用 MOPS 全市場彙總 t21sc03 回補+改正並寫入
  (備份 `logs/quarantine_revenue_backfill_20261017/`,報告 `ml/reports/ab_revenue_history_backfill_20261017.md`)。
  `twstock.step6` 現在:空值列會重抓、舊檔讀不到不覆寫、公布期以官方彙總改正最近 12 個月(明細 `logs/revenue_corrections_*.csv`;
  改正值是重編後數字,對回測有輕微前視)。**營收匯入代號一律取自檔名**(`backend/db/ingest._ingest_revenue`):
  檔內 Ticker 欄曾讓 66 檔存成 '1441.0',且全部檔案無 Ticker 欄時舊 SQL 會讓匯入失敗。
- **季報兩缺陷與回補(2026-10-18,PM 核可,#41)**:MOPS 彙總表 t163sb04/05/06 只列「查詢當下」已上市櫃的公司,
  且歷史季只在檔案缺失/列數 <80% 時才重抓——所以 (1) 季檔寫入後才上市的股票永遠缺上市前季度(39 檔 262 列),
  (2) 季度不再是「最新應完整季」後,公司更正申報就不會進來(9 檔 47 列)。已用官方現值補+改正並寫入 75 個季檔
  (備份 `logs/quarantine_quarterly_backfill_20261018/`,含 manifest 雜湊;還原=把備份複製回原位;
  報告 `ml/reports/ab_quarterly_fundamentals_backfill_20261017.md`)。官方現值的 Market/Name 是「現在」的值
  (上櫃轉上市股的歷史季也標上市、改名股歷史季用新名),特徵不用這兩欄。官方已不列的下市公司舊列一律保留。
  **更新程式尚未修**(C 待 PM 排程):在修好前,新上市股與更正仍會再累積;稽核只看整季檔列數,看不到逐檔缺列與數值漂移。
- **預測批次資料日不可取第一列(2026-10-10/12 事故)**:`ml/predict.run_prediction` 原以排序後第一列的 `date` 命名輸出檔;
  6173 最後一根K棒停在 10/06 又排第一,10/08 資料的預測被存成 `predictions_2026-10-06.csv`——10/10 週六重訓與 10/12 早上兩次
  蓋掉歷史檔、舊版紙上組合 `paper_portfolio.db` 鎖成 2026-10-06 並以 10/07 開盤進場(前視錯誤)、10/12 儀表板因找不到
  有效的 10/08 預測憑證而失敗(名單 API 正常)。修法:單一來源 `ml/prediction_dates.canonical_prediction_date`
  (多數列共有的日期,同票取較晚;不用第一列、不用最大日期),11 處同型寫法全改用它,資料日落後的個股只告警不影響檔名。
  **鐵律**:任何「整批預測/名單的日期」都要用這個函式或產物憑證的 as-of,不可用 `df["date"].iloc[0]`。
  預測憑證只對「當下來源狀態」有效(`load_prediction_csv` 比對 `lineage.source_state()`),隔天來源更新後舊檔憑證必然過期,
  修復一律重跑產生,不可改名或搬動檔案讓憑證通過。被蓋掉的歷史檔以 `ml/reports/archive/nightly/<date>/` 的封存為原件。
- **停牌與終止上市櫃(2026-10-12 PM 核可)**:單一來源 = `config/trading_status_events.json`(9/6 建立,官方公告 URL + 原檔 sha256,
  規格見 `scripts/latest_bar_coverage.validate_events`)。(1) **即時推論**股票池扣掉名冊上已生效且已可得知的 `terminated`
  (`ml/dataset._latest_snapshot_tickers`;名冊已列入快照憑證來源)——訓練/回測/DuckDB 匯入不受影響,歷史資料不可因事後下市刪除
  (`config/retired_tickers.csv` 是全域排除,會造成倖存者偏差,新案不要再加進去)。(2) 既有官方K棒覆蓋稽核
  `scripts/latest_bar_coverage_runtime.build_stale_bar_coverage` 原本只掛網頁健康 API,現於 Phase-1 ingest 後與 Phase-2 預測後
  各跑一次,只告警不擋流程(`logs/latest_bar_coverage_latest.json`、degraded warning)。告警「官方行情無此股且名冊未登錄」
  = 要補登事件(減資停牌、終止上市櫃),不是要改程式。(3) **停牌列不進即時推論(2026-10-12 PM 核可)**:
  `ml/dataset._drop_stale_latest_rows` 剔除最後一筆不等於整批資料日的股票(停牌、減資換股、下櫃),恢復交易隔天自動回來;
  剔除超過 5% 直接報錯(疑似半匯入)。固定快照 A/B 見 `ml/reports/ab_live_universe_exclusion_20261012.md`。
  (4) **3718 中光電投控不串接 5371 價格歷史**(9/6 名冊紀錄,2026-10-12 PM 再確認不例外)。
- **官方終盤校驗只有一條路(2026-10-13 PM 核可)**:獨立排程 TpexBarVerify(20:15 跑 `scripts/verify_otc_bars.py`)已停用——
  與 Phase-1 `exec_verify_daily_bars` 同一支程式、無互斥鎖,交易日會在 Step1 抓取進行中執行並搶先修補,使排程 log 與告警看到 0 檔。
  不要重新啟用或另建獨立校驗排程;細節與還原方式見 `docs/STRATEGY_overview.md`「TpexBarVerify retired」。
  診斷修補量時兩個檔都要看:`logs/tpex_bar_verify.log`(每次執行一行)與當日 `logs/smart_update_auto_*.log`。
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
- **盤前的「即時」報價是前一交易日的(2026-10-21 發現)**:名單 07:00 產生時呼叫證交所 MIS 即時加權(`tse_t00.tw`),
  此時 `z`=前一日收盤、`y`=前二日收盤,`d`=前一交易日。`_night_session_gap()` 沒核對 `d`,把「前一日日盤漲跌」寫成
  「【夜盤】…跳空開高/下殺」;10/14~10/21 六份名單 6/6 如此(10/21 寫「−1.1% 跳空下殺日,先觀望」,實際夜盤大漲、開高約 1.5%,
  由 PM 轉貼的 rere 限動「昨晚夜盤飛起來」與截圖倒推的加權收盤對出)。修正:`d` 不是今天就回報「盤前無最新夜盤資料」。
  通則:任何用即時報價算「今日」數字的地方都要核對資料日期;夜盤真實價格(台指期夜盤)要另外接,且須用 07:00 實際時段的 live payload 驗證。
- **颱風假當天的 Phase-1 失敗是誤報(2026-10-23)**:颱風休市沒有事先進行事曆,早上 Phase-2 照常產出當天名單,晚上 Phase-1 把當天當交易日:
  twstock(exit 1)、估值(TWSE 日期不符、TPEx 空表)、法人兩市缺失、資料缺口警報;匯入與候選發布依設計被擋,沒有資料被寫壞。
  同晚 Yahoo「possibly delisted」約 900 筆、日K 官方校驗修補約 1,900 檔都是每天例行,不是原因。判讀順序:先看即時報價 `d` 與
  MI_INDEX 當天是否全空,再登記 `add_adhoc_closure`。連帶問題:主 lane/影子帳本當天的列會永遠「待判定」——2026-07-10 巴威颱風
  留下 2 列至今待判定,另有數列用事後被清掉的占位假棒結算;已改由 `entry_filter_tracker.check` 自動標 void(休市不計)。

## Git、合併與測試(原 AGENTS.md「Git Commit Policy」段的事故條目)

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
- **合併一律走固定流程(2026-10-13)**:分支工作目錄的成果用 `bash scripts/merge_branch.sh <分支> <工作目錄> [--not-before YYYYMMDDHHMM]`
  併回 main——它會跑 preflight、**完整測試關卡**(`scripts/premerge_gate.py`,全套約 40 秒,合併前在分支、合併後在主工作目錄各一次)、
  **憑證影響檢查**(改到快照/預測/名單憑證來源者,交易日 18:00 前拒絕合併)、快轉、推送、清理。不要再只跑「相關」測試:
  2026-09-30~10-13 因此漏掉三個早已失敗或有副作用的測試。允許失敗的只有登記在 `tests/gate_known_failures.txt`(待裁決的真失敗)
  與 `tests/gate_worktree_env_failures.txt`(分支工作目錄缺資料/模型檔)裡的測試;新增一列要寫日期與原因。
  新測試凡會經過 `smart_update_auto.LOGGER` 的,必須先換成空日誌,否則會把假訊息寫進正式排程 log(2026-10-12 事故)。
  **測試不可碰正式環境(2026-10-13 補強,由 `tests/conftest.py` 統一擋)**:主工作目錄跑完整測試時,既有測試曾把假 ERROR 寫進
  當日 `smart_update_auto_*.log`、把 `logs/latest_bar_coverage_latest.json` 蓋成測試結果,並且真的對 127.0.0.1:8001 送出
  `POST /api/db/release`(線上伺服器因此釋放 DuckDB 連線)。現在 conftest 會:讓排程/網頁的四個 logger 不開正式 log 檔、
  把覆蓋報告與 KOL log 導到暫存目錄、擋下所有真實 HTTP(`urllib`、`requests`)並讓發出連線的測試失敗;
  確實需要連線的測試標 `@pytest.mark.allow_network`。關卡另外比對測試前後的排程專用檔案,有變動即判 FAIL。
  讀當日排程 log 時注意:2026-10-12 18:05/18:46 與 2026-10-13 18:07~18:08、21:39 的 `REFUSED blocked`、`failure-fixture`、
  `verify_daily_bars failed`(traceback 指向 `tests/`)是測試寫入的假訊息,不是排程失敗。
- **從分支工作目錄預覽產生器會出現假的憑證失效(2026-10-19)**:分支的 `generate_entry_candidates.py` 匯入時把自己的根目錄插到
  `sys.path` 最前面,之後才延遲匯入的 `ml.snapshot_lineage` 就從 worktree 載入、用 worktree 的程式檔算來源簽章 →
  「Canonical snapshot lineage rejected」。這是預覽工具造成的,不是正式憑證失效:先在主工作目錄直接呼叫 `_verified_snapshot()` 確認;
  預覽時先從主工作目錄匯入 `ml.snapshot_lineage`,載入分支模組後再把 worktree 移出 `sys.path`。
  另:舊日預測檔的憑證對現行來源必然失效,過去交易日的處置名單也無法重建(complete=False、0 檔),重算歷史名單只能當近似。

## 策略背景(原 AGENTS.md「Strategy Background」第一條)

- **市場基準的股票池要用「期初」條件(2026-10-12 更正教訓)**:計算「同期全市場等權」時,流動性門檻必須看**進場/訊號當日**,
  不可用期末(最新)是否流動來挑股票——期末流動的股票偏向已經漲上來的,基準會被墊高(實測五週 +2.8pp、三個月 +5.8pp),
  使策略與 KOL 標的的超額被低估。逐日以訊號當日取池的回測(#9/#17/#18/#19)不受影響。共用實作見
  `scripts/rere_public_follow_tracker.build_follow_book` 與 `scripts/replay_rere_new_rules.py`。
