# 交接報告 — Claude 工作階段(2026-09-23 ~ 10-01)

> 供下一位接手工程師(Claude/Codex/人類)使用。系統現況、本期變更、待辦與驗證指令。
> **開工前必讀 `AGENTS.md` 頂端「⛔ 動手前紅線」六條**——本期兩次事故的直接產物,優先於其他一切。

## 一、系統現況(2026-10-01 14:00 驗證)

| 項目 | 狀態 |
|---|---|
| 名單 API `/api/entry/canonical` | ✅ 200,17 檔,trade_date 2026-10-01 |
| 排程 | Phase-1 19:30 / Phase-2 07:00 正常;10/1 晨唯一 FAIL=TAIEX 月初假警報,**已修**(`d506d27f`),明晨 10/2 應全綠 |
| Git | **main 唯一長駐分支**,本地=遠端(GitHub jay-cheng1217/f-stock),每日產物由 Phase-2 尾端白名單自動 commit+push |
| 資料 | 日K/DuckDB 至 9/30(正式收盤資料);9/30 盤中污染事故已全數清除(備份在 `logs/quarantine_partial_bars_20260930/`) |
| 投資賽 | 38 筆單 9/30 誤判已還原 PENDING,10/1 晨排程已正常處理;`agent_arena_latest.json` 10/1 重產 |
| 儀表板 | 固定 Artifact 網址每平日 07:45 自動重發;rere 區塊置頂 |

## 二、本期完成(按主題)

### A. 9/23 Codex 審查修復(P1 全數完成)
- 盤中未確認不亮綠燈(`597bffe0`/`00ee23b3`)、rere 法人缺值標待確認(同上)
- auto-commit hook 改純提醒(`3e5fd721`)→ 後續由 Phase-2 白名單自動收檔(`7824ba31`)+ push 防漂移(`38e1d754`)
- 個股詳情逐檔資料日期+過期紅幅、dataA/Champion 分列標名(`24464d51`)
- retrain guard 測試隔離(`7b7fc869`)、momentum 回測 friction/missed 修復+research-only 警告(`601638f6`)
- 重訓治理語意澄清:週六 artifact 產出≠上線,production pins 未動(`98a10b65`)
- **教訓**:改到憑證綁定檔(generate_entry_candidates.py 等)必須立即重跑認證鏈,否則名單 API 503

### B. rere 研究線
- **淺洗盤型(shallow)第三子型態上線**(`9334f1f7`,PM 核准):8%≤洗盤<10%,其餘同蹲點型,不掛投信條件(鈦昇 16 訊號投信全 0);帳本 `subtype` 分開累積,蹲點/發動 forward 口徑零變動。首日即出卡(4576)。subtype 標籤 bug 已修(`37f22ab8`)
- **BT-rere-pre-trust-accumulation 判決**(`16cce5df`):「外資淨買無拐點」不開子型態(對照組隔離後邊際僅+0.5pp);**附帶重大發現:現行蹲點型(深洗盤+外資拐點)跑輸同類盤整 base rate 且左尾 2.7 倍**——列入下次策略審查議程,forward 期不動 gate
- 側寫檔新案例:台表科 6278(⌚🦪=表科)、瑞耘 6532、南亞/台虹戰役、她的四段情報流自曝(她自己也跑 forward 驗證)、9/30 持股全解碼(7 檔:覆蓋 3 帳本+1 watch+3 miss)
- 鑑定方法:她曬單→昨收=現價−漲跌 反查日K 精確鎖定;**鎖死盤報價怪癖**已修 `intraday_quote.py`(🔒標記)

### C. 9/30 三連事故與機械化防線(最重要)
事故鏈:git 合併擊落 mtime 憑證(API 503)→ 日期誤判盤中啟動 Phase-1(103 檔假棒)→ Phase-2 把假棒灌進全鏈。修復順序已文件化(AGENTS.md Git Commit Policy 段)。防線:
- **盤中 Phase-1 程式級拒絕**(`d0af395b`):交易日 08:30-14:30 直接 refuse,`--force` 不可繞,需 `--allow-intraday`
- **`scripts/preflight_check.py`**(唯讀預檢):時鐘/盤中/git/程序/API/假棒,有 STOP 退出碼 1 → 回報不動手
- AGENTS.md 頂端六條紅線;ingest cp950 emoji 崩潰兩處修復(`7905ae7a`/`4672764d`)
- Git 收斂:main 唯一分支、遠端快照重接(完整歷史在本地 tag `archive/main-full-history-20260930`)、>100MB 快取永久 gitignore

### D. 10/1
- TAIEX 月初假警報修復(`d506d27f`,8/3、9/1、10/1 三連發根治,3 測試)

## 三、待辦(優先序)

1. **南茂 8150 watch 卡**(本 session 收盤後掛,若未完成見下節):區間 105.5-110(發動日低~前平台頂)、失效 104.5;掛入 `logs/entry_watchlist.json` 後**必須重跑認證鏈**(`_run_canonical_entry_plan`)否則 API 503
2. **明晨 10/2 驗證**:Phase-2 應 all_ok=True(TAIEX 修復首驗);投資賽單據成交正常
3. **REQ-034 每日持股風險報告:已在正式寄送**(勘誤 10/1:原寫「dry-run 從未寄出」有誤)。排程
   `TW_Stock_My_Holdings_Risk_Cut_0630` 平日 06:30 跑 `run_my_holdings_risk_cut_report.bat`(未帶 `--dry-run`),
   5/16 起每日寄信(logs 共 96 份)。報告 JSON 的 `status: dry_run_only_pm_review_required` 是**判定規則
   尚未經 PM 核定為交易指令**的標籤,不是寄送狀態——寄送與否看 log 的 `email_sent=`/排程 LastResult。
   待辦改為:PM 決定是否退役信首的 synthetic-tested 註記(`--no-synthetic-note`)
4. **SA 票**:lineage 快速簽章 mtime→sha 語意(`docs/TICKETS_post_review.md` #4,免 git 操作誤傷)
5. **下次策略審查議程**:蹲點型核心條件跑輸 base rate(見 B);momentum 回測完整對齊(P1-3)、research-only 警告下沉輸出、舊勝率評估器、T+1 命名口徑(9/23 審查補遺)
6. repo 歷史 7.5GB 瘦身(大型每日產物移出版控/LFS)——中期票

## 四、私人資料邊界(務必遵守)

- `my_holdings.db`(gitignore)含用戶真實持股;**任何報告/commit/對外文件不得含成本、股數、金額**(REQ-034 隱私欄位省略設計)
- 本期含用戶持股諮詢脈絡(攤平數學、回本機率 base rate 表在 scratchpad);諮詢一律「系統判定+紀律框架」,不替用戶做買賣決定
- ntfy 主題、email 均不入版控

## 五、常用驗證指令

```bash
python -X utf8 scripts/preflight_check.py        # 動手前必跑
python -m pytest tests/ -q -k "intraday_phase1 or taiex or generate_entry or weekly_retrain"
grep -E "all_ok|FAIL" logs/smart_update_auto_$(date +%Y%m%d).log
```
名單 API:`http://127.0.0.1:8001/api/entry/canonical`;儀表板固定網址見 `skills/dashboard-interface-update/SKILL.md`。
