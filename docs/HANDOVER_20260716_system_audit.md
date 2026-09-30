# 交接文件：全系統資料/功能審查戰役（2026-07-15~16）

**對象**：Codex / Claude（SA + RD）
**狀態**：Wave 1 接手修復與回歸驗證已完成；整場約 88 項候選並未全部結案。
**最後更新**：2026-07-17

接手前先讀 `AGENTS.md`、`skills/git-commit-after-change/SKILL.md`，以及與任務相符的專用 skill。模型、特徵處理或 production gate 變更仍須遵守同快照 A/B 與模型資產保護規則。

## 0. 戰役背景

2026-07-15 原相除息被當成跌幅後，追查出一整族「pipeline 綠燈，但內容凍結、殘缺或語意錯置」事故。最嚴重案例包括財報殘檔與 TDCC 六年口徑錯置。PM 因此要求從資料抓取、匯入、特徵、預測、風控、帳本、排程到 Web 顯示做全系統對抗審查。

本戰役的核心原則：

1. 檔案存在不等於資料完整。
2. 空資料不等於合法的「沒有結果」。
3. 除權息、快照日期與 point-in-time 語意必須在每一層一致。
4. 發現 production 規則問題時，先方法鎖與 A/B，再改 production。

## 1. 戰役既有成果

| Commit | 內容 |
|---|---|
| `efa57c7d` | 建立 2019 年至今除權息日曆，rere 與 Champion 風險信採還原報酬 |
| `f8dc7791` | Champion 停損除息還原；同快照 A/B 顯示 248 倉 status 零變動 |
| `9e860b34` | rere 基準改為 +4.58% / 35.8%，舊 +9.4% 基準作廢 |
| `de0784ba`, `e72e4025` | 修復 2026Q1 EPS、損益表與資產負債表殘檔並補抓 |
| `ca1f4721` | 財報完整性稽核、自癒與申報行事曆感知 |
| `4f8ab947` | 修復 TPEx 融資凍結與外資持股凍結 |
| `db6f4d1e` | 修正 TDCC 六年級距字串排序錯置並重建 summary |
| `88ed16ae` | 修復 2025Q4 財報、估值缺日與幽靈法人檔 |
| `5071a8d5` | CSV 匯入層第一版列數守門與原子化基礎 |

事故量化：Q1 財報修復造成訊號翻轉 10.7%、Top30 更換 22 檔；TDCC 修復造成訊號翻轉 14.9%、Top30 更換 26 檔。報告位於 `ml/reports/incident_2026q1_fundamentals_ab_20260716.md`。

## 2. Wave 1 接手完成項

### 2.1 資料與匯入完整性

| Commit | 分類 | 已關閉問題 |
|---|---|---|
| `dced9641` | data integrity | TDCC backfill 保留 PM 核定的散戶 L1-9 / 千張大戶 L15 口徑 |
| `0dce60bc` | infra / ingest | reject 稽核、必要欄位與期別驗證、staging 驗證後原子 swap；關閉單列丟失、寫入後才檢查、schema drift 與 per-period freshness 問題 |
| `080ecd50` | feature integrity | 資產負債表來源損毀時明確失敗，不再靜默沿用舊季 |
| `4565c339` | database reliability | DB 鎖定改為可辨識錯誤，不再偽裝成空 DataFrame |
| `097987ef` | point-in-time data | universe eligibility 改為逐日 as-of，移除最新快照套整段歷史的 look-ahead |
| `75e9798c` | dataset cache | 所有特徵來源都納入快取 invalidation |
| `530c03bf` | scheduler / source integrity | 除權息雙來源失敗時排程明確失敗，不再 exit 0 |
| `69815b76` | data refresh | 除權息日曆更新至 2026-07-17 |

### 2.2 排程、風控與帳本

| Commit | 分類 | 已關閉問題 |
|---|---|---|
| `56087dfa` | scheduler reliability | Windows 鎖擁有者探測不再誤殺程序 |
| `1ea4baca` | holdings risk | my_holdings 風險規則恢復現金股利還原 |
| `05f15eb0` | reporting integrity | 風險信除息截止日改與市場資料快照一致 |
| `1f3d4aab` | ledger integrity | entry ledger 持有報酬納入現金股利 |
| `8aa3abce` | rere methodology | 同一 source/ticker 跨 closure 去重；停損由進場次日起算；保留實際 source_date |
| `4c8dac31` | rere repair | 重開並重算 legacy day-zero stops |
| `6050b251` | production ledger | 以 sanctioned tracker 更新正式 rere 帳本；7 筆重複訊號標記 duplicate_signal；2399 改為真實 day-1 stop -6.14% |
| `3586fd2e` | test isolation | tracker 測試不再覆寫正式前端 ledger |

rere 最新帳本共 80 列：holding 55、stopped_out 11、duplicate_signal 7、pending_entry 7。已結束樣本平均 -8.04%、勝率 0%，但 `N=11 < 30`，依 skill 只能標「累積中」，不得提前降級或結案。

### 2.3 即時進場介面

| Commit | 分類 | 已關閉問題 |
|---|---|---|
| `61dc672c` | production interface / feed | 即時價格已高於區間時，`MOMENTUM_MISSED_NO_CHASE` 不再被 signal age 規則覆寫；同步修正測試契約與 DB 隔離 |

Web 已驗 `http://127.0.0.1:8001/` 回應 200。固定 dashboard：
`https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b`。
更新通知已用 UTF-8 寄出；當次 tunnel 為 `https://integration-hay-ears-loved.trycloudflare.com`，tunnel URL 可能隨服務重啟改變。

## 3. 實戰與回歸驗證

2026-07-16 19:30 Phase-1 為新守門第一次 production 實戰：

- `all_ok`，耗時 51.5 分鐘。
- 17 個步驟完整通過。
- ingest 已回報真實資料列數（例如 daily_k 約 300 萬列、tdcc 約 86.7 萬列），不是舊式檔案數。
- 列數守門、財報稽核與語意不變量均靜默通過，未需使用 `INGEST_ALLOW_SHRINK=1`。
- 沒有 DATA GAPS 警報。

接手修復後驗證：

- 全套測試：`411 passed`，502 warnings，64.60 秒。
- 集中測試：63 passed；rere / candidate 相關測試：20 passed。
- 所有本輪變更 Python 模組均通過 `py_compile`。
- `fundamentals_completeness_audit.py`：月營收、季度 EPS、損益表、資產負債表全綠。
- `audit_exdiv_false_stops.py`：34 個部位，持有窗內除權息案例 0，錯殺案例 0。
- `quick_validate.py` 在目前 repo 不存在；舊交接中的該指令已失效，不得再宣稱通過。

## 4. 尚未完成：已確認、但不得直接改 production

### P0-A：退出策略除權息一致性

`scripts/exit_policies.py` 的 production 預設 MA5 / high-water-mark 路徑仍使用未還原 close。這可能在除息日誤觸 exit，但屬 production 行為變更，必須先：

1. 鎖定現行與候選算法。
2. 用同一 DuckDB as-of frame 做固定快照 A/B。
3. 回報 status flip、報酬、MDD、turnover 與實際除息樣本。
4. PM 核可後才改預設路徑。

### P0-B：`ml/predict.py` guardrail 完整性

已確認的風險：

- 缺少必要欄位時部分 hard guard 會靜默失效。
- `operating_margin` 為 NaN 時可能通過本業虧損判斷。
- 極端預測值檢查位於 ATR clipping 後，可能永遠不會觸發。
- overheat、chip divergence、turnaround 等文件與執行順序有 drift 候選。

這一票須先產出 method lock 與同快照 A/B。不得只因文件寫法不同就直接改 production threshold。

### P0-C：殘餘「檔案存在即完成」凍結路徑

依序處理並各自加入 partial-content fixture：

1. `scripts/backfill_eps.py`：80% 門檻在申報截止日前可能凍結早申報殘檔，且缺季完整性不足。
2. `scripts/backfill_valuation.py`：單一市場 partial response 可能凍結整日估值。
3. `scripts/download_margin_data.py`：檔案存在與 partial-source freeze。
4. `twstock.py`：舊日 K 讀取失敗可能以新片段覆蓋歷史；融資 merge watermark 可能漏掉歷史補洞；Step 8 尚有 80% freeze 路徑。

財報每晚 audit 可偵測多數結果，但不能取代抓取器寫入前防護。

## 5. 後續優先序

### P1：排程與介面可靠性

- `scripts/run_kol_tracker_daily.bat`：補 errorlevel、避免失敗後發佈 stale artifact，並處理 07:30 排程競態。
- `scripts/entry_dashboard.py`：對 TDCC / model snapshot 顯示 freshness；避免 latest-glob 選到非日期或 stale artifact。
- `ml/features/industry.py`：查清 train / serve 分類映射是否一致。
- KOL / dashboard 的 timezone、輸入 escaping 與 XSS 候選逐項驗證。

### P1：資料補齊

- 外資持股 110 個失敗日：把 scratchpad 邏輯整理成 `scripts/backfill_foreign_ownership.py --retry-partial`。
- **TDCC 43 缺週（PM 2026-07-19：記錄待辦，之後再補）**：根因已查明——
  `backfill_tdcc_finmind.generate_fridays()` 只產生**週五**日期，但 TDCC 資料日是
  該週最後營業日，週五休市時落在**週四**；2026-03-24 付費批次抓到 279 週，
  43 週中有 29 週正是「資料在週四、腳本從沒問過」。修正版已備妥：
  `scripts/backfill_tdcc_gap_weeks.py`（逐日探測 五→一、鎖定單日並驗證回傳日期、
  覆蓋率下限、原子寫入）。**PM 再訂閱 FinMind 付費等級後執行一次即可補完**，
  執行後接 `python twstock.py --step 9` 重建摘要。未補前特徵層由 RETRAIN-02 gap-aware 處理。
- 估值 2021 年 3 個殘檔：確認源頭是否確實無資料後結案。

### P1：進場名單收斂稽核發現（2026-08-27 15-agent 對抗驗證,改前必回測）

- **MACD_DELTA_FLOOR 尺度失真**：✅ 回測完成(2026-08-28,`scripts/backtest_macd_price_relative.py`,
  報告 `ml/reports/backtest_macd_price_relative_20260828.md`)。B版(delta/close>=-1.0%)依事前判準
  PASS:主要收益=解除對>200元股的過度封鎖(7,045筆前瞻+2.55%/勝率51.1% vs 共同集+1.16%);
  今日名單零翻轉。**已落地**(PM 2026-08-28 核可,commit 見 git log;切換日名單零翻轉實證)。註:原稽核稱 6206/3046「MACD帶病通過」係
  層級口徑誤判,production delta 口徑下兩檔合法通過(其 CUT 理由不變但與 MACD 無關)。
- **主軌無動能/波動下限**：「貼MA20+不爆量」天然偏好低波動防禦股（台灣大振幅1.73%、3月+0.0%、
  pred+0.4%；遠百類似），佔用主軌名額。加 ATR%/振幅下限=過濾器變更，先回測。
- **kw_alert 金控誤殺**：元大金被「母公司認購子公司現增」例行公告觸發 veto（fail-safe 方向正確
  但系統性侵蝕金控 rere 候選）；veto 列 reason 欄仍留「務必小倉」模板文字，顯示矛盾。
- **晚間預跑 guard 盲區**：手動晚間預跑用前一日預測+當日日K（正式 05:00 排程會重生成對齊版）;
  預跑版 trade_date="next-session" 可識別，屬已知行為非缺陷，但人工讀晚間版需知模型層晚一天。

### P2：專項資料票

- 公司行動、配股比例與訓練標籤：**CLOSED 2026-07-17**，詳見
  `docs/TICKETS_retrain_prerequisites.md` 與 `ml/reports/retrain03_price_integrity_20260717.md`。
- 金融股資產負債表專用解析。
- EPS TTM 缺季時不得把第五季湊入 rolling(4)。

### P3：重訓包

**⛔ 重訓仍未授權（PM 2026-07-17 裁示）**：RETRAIN-01 標籤除權息還原、
RETRAIN-02 TDCC gap-aware、RETRAIN-03 公司行動斷點已全部完成，影響量化與全套
`483 passed` 均通過。前置阻斷已清除，但**只有 PM 明確核可後才能啟動重訓**。

只有 PM 明確核可後啟動。理由包括除息標籤、2026Q1 髒窗、TDCC 語意翻轉、turnover_rate 與存活偏誤修正。新模型走 two-stage gate，不得自動升 production，並須 Stage 2、CL3 與同快照 A/B。

## 6. 環境開關與單一真相

- `EXDIV_STOP_ADJUST=0`：緊急關閉 Champion 停損除息還原。
- `INGEST_ALLOW_SHRINK=1`：只有刻意縮減資料且已人工確認時，允許單次繞過 shrink guard。
- 除權息：`scripts/update_ex_dividend_calendar.py`、`scripts/exdiv_utils.py`。
- 財報稽核：`scripts/fundamentals_completeness_audit.py`。
- rere 基準：`scripts/backtest_rere_lane_baseline.py`。
- rere 正式帳本：只能由 `scripts/rere_lane_tracker.py` 更新，不可手編。
- 原 workflow journal：`C:\Users\cheng\.claude\projects\F--stock\bf7736a1-2cc5-4ee7-bb73-498eacee7e58\subagents\workflows\wf_3666b3e5-2fb\journal.jsonl`。

## 7. 鐵律

1. 每個完成項單獨 commit，格式 `type(scope): summary`。
2. 模型、feature processor、guardrail、filter、threshold 或 production exit 改動前，必須 method lock + 對應回測或同快照 A/B。
3. 不動 `ml/models/lgbm*`、`model_selection.json`、registry、pin manifest 與 `archive/model_pins`，除非已讀 model-artifact-protection skill 且取得 PM/SA 核可。
4. 資料完整性必須同時驗：內容、列數、必要欄、日期/期別、來源覆蓋與語意不變量。
5. 測試不得寫入 production ledger、SQLite、DuckDB 或前端 static artifact。
6. 排程異常先讀 `logs/smart_update_auto_YYYYMMDD.log`，不可只看 Task Scheduler 的 `0x1`。
7. 不把 journal 裡的代理候選直接當成 bug；先以程式碼、fixture 與可重現證據對抗驗證。

## 8. 接手驗證指令

```powershell
python scripts/fundamentals_completeness_audit.py
python scripts/backtest_rere_lane_baseline.py
python -X utf8 scripts/rere_lane_tracker.py check
python scripts/audit_exdiv_false_stops.py
python -m pytest -q
git status --short --branch
```

## 9. 結案邊界

Wave 1 接手修復可結案，因原交接的 7 個 P0 深化問題及接手期間確認的高風險帳本/排程/介面問題均已修復並通過完整回歸。整場全系統審查不得宣稱全數 CLOSED：§4 至 §5 仍是有效 backlog，其中 P0-A、P0-B 必須先完成 A/B 才能動 production。

本輪未重訓、未升版模型、未修改 protected model artifacts 或 production model selection。
