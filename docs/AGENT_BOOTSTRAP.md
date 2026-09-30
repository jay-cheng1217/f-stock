# AGENT_BOOTSTRAP — 把這套系統部署成你自己的複製品

> 目標讀者:任何 coding agent(Claude Code / Codex / Gemini CLI / Cursor)或人類工程師。
> 讀完本檔+照做,即可在一台 Windows 機器上重建整套台股選股系統。
> 非投資建議;歷史回測不代表未來績效。

## 0. 系統一句話

每晚自動:抓全市場價量籌碼 → 官方終盤校驗 → DuckDB 入庫 → LightGBM 模型預測 →
canonical 產生器出雙策略名單(主 lane 型態+模型 / rere lane 洗盤+外資轉買)→
深色儀表板+郵件推送;帳本自動 forward 驗證,週頻大戶雷達補觀察池。

## 1. 前置需求

- Windows 10/11(排程用 Task Scheduler;Linux 可改 cron,bat 需改寫)
- Python 3.12+(`pip install -r requirements.txt`;3.14 對 TPEX SSL 有相容問題,
  相關腳本已內建 curl fallback,需系統有 curl)
- git;node(選用,只為儀表板 JS 語法自檢);cloudflared(選用,遠端 web 隧道)
- 磁碟 ≥10GB(日K CSV+DuckDB+模型)

## 2. 安裝

```bash
git clone <your-fork>
cd stock
pip install -r requirements.txt
copy .env.example .env.email   # 填 SMTP;ntfy 另存 .env.ntfy(選用)
```

模型檔(`ml/models/lgbm*.txt` + pins)已隨 repo 提供,可直接預測;
週度重訓(TW_Stock_Weekly_Retrain)為選用。**模型檔受保護:刪除前必跑
`python scripts/model_cleanup_precheck.py`(見 AGENTS.md)。**

## 3. 資料回補(首次,一次性;約數小時)

依序執行:

```bash
python twstock.py                          # Step1-11:全市場日K/法人/融資/月營收/技術指標
python scripts/backfill_eps.py             # 季報 EPS
python scripts/backfill_balance_sheet.py
python scripts/backfill_valuation.py
python scripts/backfill_tdcc.py            # 集保股權分散(週頻)
python scripts/backfill_exdiv_calendar.py  # 上市除權息日曆(TWT49U)
python scripts/daily_pipeline.py           # CSV → stock.duckdb 入庫 + 首次預測
python scripts/tdcc_whale_weekly_scan.py   # 建大戶週增快照(次週起出榜)
```

## 4. 排程註冊(核心 4 個;時間=台北)

| 排程名 | 時間 | 指令 | 作用 |
|---|---|---|---|
| TW_Stock_Nightly | 交易日 19:30 | `scripts\run_daily.bat` | 主 pipeline:抓數→校驗→入庫→預測→名單→帳本→寄信 |
| TpexBarVerify | 交易日 20:15 | `python scripts/verify_otc_bars.py` | 終盤價第二道校驗(0702 Yahoo 事故防線) |
| TW_Stock_My_Holdings_Risk_Cut_0630 | 每日 06:30 | `python scripts/...risk_cut...` | 持股風險分級信(選用,需 my_holdings.db) |
| TW_Stock_Weekly_Retrain | 週六 | `scripts\run_weekly_retrain*.bat` | 週度重訓(兩階段 gate,新模型不自動上線) |

```bat
schtasks /create /f /tn "TW_Stock_Nightly" /tr "<REPO_ROOT>\scripts\run_daily.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 19:30
```

已退役、不要註冊:TW_Stock_ChipK_Relaunch(籌碼K桌面自動化,保持 `CHIPK_DESKTOP_ENABLED=0`)。

## 5. 每日資料流地圖

```
19:30 twstock.py 更新 → exec_verify_daily_bars(TWSE/TPEX 官方對照,錯>50檔告警)
    → ingest → predictions_<date>.csv(20D 模型) + dataA_predictions_<date>.csv
    → generate_entry_candidates.py → logs/entry_list_<次交易日>.json
    → entry_filter_tracker.py record/check → ml/reports/entry_filter_ledger.csv
    → rere_lane_tracker.py → ml/reports/rere_lane_ledger.csv(forward 驗證)
    → 寄信(名單+固定儀表板連結)
07:00 entry_dashboard.py + kol_dashboard.py 重生 HTML(發佈方式見 §7)
週六  tdcc_whale_weekly_scan.py → logs/tdcc_whale_delta_<date>.csv(大戶週增榜)
```

## 6. 資料契約(核心檔案)

| 檔案 | 關鍵欄位/語義 |
|---|---|
| `日K資料/<tk>.csv` | Date,OHLCV,Foreign/Trust/Dealer_BuySell(股),Margin/Short_Balance,MA/RSI/MACD/KD。**混合價格制:歷史=還原價、近期=原始價**,跨除息計算需校正(參 backtest_dividend_refill.py 的恆等式) |
| `ml/models/predictions_<date>.csv` | v1 signal/up_prob、v2 pred_return_20d、recommendation(含防呆硬擋)、risk_tags、inst_cost_10d、POC/VA、shortwave_* |
| `logs/entry_list_<date>.json` | rows[]:lane(main/rere/watch)、kind(go/small/watch)、zone、stop、no_chase、reason |
| `ml/reports/entry_filter_ledger.csv` | 名單模擬帳本(觸發/停損/到期),只經 tracker 寫入 |
| `ml/reports/rere_lane_ledger.csv` | rere forward 驗證,**只准 rere_lane_tracker.py 寫**,基線 +9.4%/50.6%,N≥30 判級 |
| `ml/data/tdcc_whale_snapshot.csv` | 千張大戶/散戶<100張 週快照(與籌碼K同口徑:級距15 / 1-9) |
| `ml/data/exdiv_calendar_twse.csv` | 上市除權息(結果表=歷史;未來事件用 TWT48U 預告表) |

## 7. 儀表板發佈(依平台擇一)

- **Claude Code**:Artifact 固定網址 + 雲端 routine 每日重發(本 repo 現行做法)
- **其他平台**:`ml/reports/entry_dashboard_latest.html` 為自包含單檔,
  推 GitHub Pages / S3 / 任何靜態空間即可;私人資料注意見 §9

## 8. Agent 接入(讓任何模型接管營運)

開工必讀順序:`AGENTS.md`(單一守則來源,含 ML 防呆規則 1-14)→ 任務對應
`skills/*/SKILL.md` → `docs/STRATEGY_overview.md`(策略現況與判決)。
鐵律三條:改過濾器前必回測;完成任務即 commit;rere/entry 帳本只經 tracker 寫入。
Claude Code 另有 `.claude/agents/`(子代理)與 memory;其他模型把 AGENTS.md
當 system prompt 等價使用即可(Codex 原生支援)。

## 9. 發佈前私人資料剝離清單(必做)

| 移除 | 原因 |
|---|---|
| `.env*`(所有)| SMTP/ntfy 秘密 |
| `my_holdings.db`、`paper_portfolio.db` | 真實持股/紙上部位 |
| `kol_tracker.db`、`docs/rere_strategy.md` | 私人來源側寫(朋友/KOL),涉隱私 |
| `ml/reports/*ledger*.csv`、`ml/reports/archive/` | 個人交易紀錄 |
| `logs/`、`集保分散/`、`日K資料/`、`stock.duckdb` | 大型資料,對方自行回補(§3) |
| `scripts/send_web_link_email.py` 內固定 Artifact 網址 | 換成部署者自己的 |

建議用 `git archive` 出乾淨副本後照表刪除,再初始化新 repo 發佈。

## 10. 已知限制

- 分點(券商分公司)資料無法自動抓取——隔日沖判定靠人工 App 截圖比對(買方/賣方 Top15 跨日)
- 上櫃除權息歷史無公開 API(僅上市 TWT49U);TPEX 只有未來場次(openapi prepost)
- Yahoo 為價格管道、交易所為真相源:終盤校驗鏈(§5)不可拆
