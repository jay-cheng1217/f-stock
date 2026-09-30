# Champion 停損層除息還原 — Fixed-Snapshot A/B 驗證（2026-07-15）

## 變更
- `order_simulation.simulate_intraday_stop` 增加 `dividend_adjust`：停損價 = entry×0.90 − 進場後累計權值息值
- `update_unified_portfolio.refresh_open_positions`（T1 與 20D 兩路徑）：close_return / intraday_drawdown / realized_return 全部除息還原
- 資料源：`ml/data/ex_dividend_calendar.csv`（TWSE+TPEx 官方，2019→今 16,048 事件，Phase-2 每日增量）
- 緊急回退：環境變數 `EXDIV_STOP_ADJUST=0`

## A/B 設定
- `unified_execution_replay.py --start-date 2026-04-20 --end-date 2026-07-14 --exit-policy asymmetric_v2`
- 同一組 unified_signals 快照（59 檔案），唯一變因 = EXDIV_STOP_ADJUST

## 結果
| 指標 | raw | adjusted | 差異 |
|---|---|---|---|
| 持倉數 | 248 | 248 | 0 |
| status 變動 | — | — | **0 筆**（無假停損、無翻轉） |
| 報酬變動 | — | — | 1 筆：6679 -2.22% → -0.30%（持有期含除息，MA5_BREAK 出場不變） |
| 組合 MTM | -2.113% | -2.106% | +0.007pp |

## 結論
行為等價（歷史零冤案，與 2026-07-15 帳本審計一致），唯一差異為經濟上正確的權息加回。核可上線。

## 殘留風險註記
- ExitManager 的 HWM 類 trailing 政策（pure_hwm_*）之高水位錨定未做除息位移，asymmetric_v2 主用 MA5_BREAK+硬停損不受影響；若未來切換 HWM 類政策需先補此處。
