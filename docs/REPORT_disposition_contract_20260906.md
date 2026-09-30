# 處置資料契約與官方修訂驗證（2026-09-06）

## 結論

修復的是官方處置資料與交易資格接線，沒有更改 rere 型態、模型、篩選門檻或出場策略。
15 筆官方修訂逐筆核對完成；同一份 DuckDB 日K frame 的歷史資格回放，8 檔有資料股票
共 168 個 ticker/date 中有 24 個資格結果改變，全部發生於修訂公布之後。這是資料正確性
驗證，不代表策略報酬提高，也不是新的報酬／MDD 基準。

另確認舊抓取器漏掉「已公告、下個交易日才開始」的處置。修正後同一知悉截止日的
官方查詢從 167 筆變 172 筆，補回 5 筆，包含 2455 全新和 6933 AMAX-KY 新一期處置。

## 原始來源與時間語意

官方對照表：https://www.twse.com.tw/downloads/zh/about/company/dispositionlist.pdf

兩頁共 15 筆：10 檔普通股票、5 檔權證。第一頁頁腳標示製表時間為
2026-08-07 18:00；新制生效為 2026-08-10。表格「公布日期」是原先個別證券處置的
公布日，不能冒充本次更正的知悉日期。

更正登錄於 `config/disposition_corrections.json`，包含原公布日、原起迄日、修訂迄日、
`known_at`、`effective_date`、版本、來源網址。例：8046 原 8/3～8/18，修正為
8/3～8/11。`2026-08-07T17:59:59+08:00` 查詢仍保留舊迄日，18:00 起才看到修訂。
date-only cutoff 明確解釋為台灣當日 EOD；盤中必須傳 timestamp。

`disposition_periods.csv` 保留官方原始公告期間與新抓取的 source / announcement_date，
查詢時再建立修訂視圖；不直接覆寫歷史迄日。`disposition_active.csv` 是當下已公告、
尚未結束的修訂視圖，保留同一股票的多個期間。健康 JSON 是 metadata，不能當股票清單。

## 修復與界線

- 按官方欄名解析；保留原公告日、市場別。欄位錯置、短列、日期錯誤、倒置期間、
  回應區間不合、列數與官方 total 不合一律失敗。TWSE `count` 是不同證券數，
  `total` 才是公告列數（實查 45 檔／60 列），兩者不可混用。
- TWSE + TPEx 必須同時成功；任何市場失敗重試，最後仍失败則保留原 CSV、寫 STALE。
  全歷史刷新單市場低於上次完整列數 80% 也擋下；失敗後保留最後完整基準供下一輪使用。
- 官方 `startDate/endDate` 查的是處置期間。新版查到下一台灣交易日，再按
  `announcement_date <= knowledge_end` 去除尚未公布資料，並記錄
  `disposition_coverage_end`。沒有這個覆蓋證據的舊快照只能回 UNKNOWN。
- 列數、雙市場、截至日、目標交易日、下一交易日覆蓋與檔案 hash 都納入讀取契約。
  寫多份 CSV 前先發布 UPDATING 狀態，避免半新半舊世代仍宣稱 OK。
- 出關雷達接入相同修訂函式；同股票另一個處置期間仍生效時，不顯示為已出關。
- `attention/full_delivery/suspended` 仍未建立來源，回傳明列 unsupported。
  通過本模組僅代表處置資料資格，不能改名成「所有特殊交易狀態均正常」。

## 固定快照驗證

方法鎖：`docs/METHOD_disposition_contract_20260906.md`。

重現：

```powershell
python -m pytest tests/test_disposition_contract.py tests/test_smart_update_auto.py -q
python scripts/audit_disposition_contract.py --output-dir ml/reports/research/strategy_integrity_20260906/disposition --online
```

驗證產物在上述研究資料夾，包括 `disposition_audit.json`、修訂期間、原始 frame、
資格翻轉明細與官方查詢 A/B。所有 production 來源檔的 before/after hash 相同。

| 項目 | 結果 |
|---|---:|
| 固定原始歷史 | 1,618 列 |
| 官方修訂匹配 | 15 列／15 證券 |
| 修訂公布前期間變更 | 0 |
| 有價格資料的股票／8 月原始 frame | 8 檔／168 列 |
| 修訂引起交易資格翻轉 | 24 個 ticker/date |
| 公布前交易資格翻轉 | 0 |
| 當下來源查詢（知悉截止 9/5） | 167 → 172 列 |
| 誤用未公布公告 | 0（具 fixture） |

24 筆翻轉：1515=4、3026=4、3055=4、3167=1、3532=4、6531=1、6969=1、8046=5。
同一股票其他尚有效期間仍會阻擋，因此不能單純把每筆縮短天數相加當交易资格翻轉。
5 檔權證及 4169、7610 的 DuckDB 日K 為空，沒有將其假補價格或計入回放。

CLI 直接開啟 DuckDB 及 `query_df` 的拷貝 fallback 均被 Windows 寫鎖拒絕。
改從持有連線的 Web 服務唯讀 `/api/stocks/{ticker}/daily?days=1000` 取得資料；
該端點在 `backend/routers/stocks.py` 直接查 `daily_k`。每個 ticker 每次驗證僅取一次，
凍結同一份 frame 給兩分支，前後檢查 DB 檔案 size/mtime 未变，未停止服務。

- 原始期間檔 SHA-256：`5e8bc45662dc119180fa9f08669fac4c1c9e1c1c7b275126c5f9708758404de4`
- 共用 DuckDB frame SHA-256：`8bd6393dbd3af4958fdfae79b0cb4c913cd4993a66177a9f7db7ea8e6ff1e89a`
- 每筆 API 回應 hash 另列於 `disposition_audit.json`。
- `prob_edge`：不適用，沒有模型重算或特徵輸入變更。

補回的五筆已於 9/4 公告，起日都是 9/7：

| 市場 | 代號 | 迄日 |
|---|---|---|
| TWSE | 2455 | 9/15 |
| TWSE | 6933 | 9/11 |
| TPEx | 24552 | 9/15 |
| TPEx | 24553 | 9/15 |
| TPEx | 704556 | 9/11 |

## 對外 API 與剩餘風險

`load_disposition_gate(base_dir, *, target_date, required_as_of, knowledge_as_of=None)`
唯讀返回 `status`（OK／UNKNOWN）、`complete`、`blocked_tickers`、`reasons`、
`effective_date`、`unsupported_sources`。UNKNOWN 不能解讀為沒有處置股，呼叫端應保留
純型態訊號但標記交易資格未確認。`required_as_of` 由呼叫端用交易行事曆決定。

純函式：`evaluate_disposition_snapshot(periods, report, ...)`；
`resolve_disposition_periods(periods, *, knowledge_as_of, corrections=None)`。

本次驗證沒有寫正式 CSV 或帳本。啟用當下資料需要主整合流程成功執行修復後抓取器。
舊三欄歷史尚無每筆公告日；本報告只證實指定官方更正的知悉邊界，不能把它宣稱為完整
歷史 announcement-time 資料集。`backtest_disposition_release.py`、
`build_unified_signals.py`、`train_v2_alpha_classifier.py` 的原始期間讀取未在此工作流
改動，仍需按各自歷史／production 方法鎖另行接入，不在本報告宣稱已修復。

分類：fetch/contract＝infra/pipeline repair；官方更正＝data semantic correction；
目前雷達＝production-adjacent observation repair；audit/fixtures/report＝research-only artifact。
