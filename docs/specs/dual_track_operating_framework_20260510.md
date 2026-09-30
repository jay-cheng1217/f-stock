# 雙軌操作框架（2026-05-10）

## 結論

系統不再追求一條「全天候打贏 TWII」的單一主線。2024-2026 的 TWII 報酬高度受台積電、聯發科等大市值 AI 循環權值股驅動，這與 Champion 的中小型動能 alpha source 不同。

正式定位改為雙軌：

| 軌道 | 狀態 | 目的 |
|---|---|---|
| Champion | Production 自動主軌 | 中小型動能選股，依 strategy-fit benchmark 評估模型有效性 |
| 大票法人趨勢 | 手動 overlay | 大市值主導牛市時補足 TWII exposure gap |

## 實證依據

REQ-025 測試「日均成交金額 >= 30 億、外資連買 >= 3 日、投信同向、站上 MA20」的大票法人信號。單筆 bucket 有明確 edge，但組合層級因 20% 產業上限與單日外資翻賣 exit 過嚴，平均持倉僅 2.38 檔。

REQ-026 窄測放寬兩項規則：

- >=30 億 universe 單一產業上限放寬至 40%（最多 4 檔）
- 外資連賣 >= 2 日才出場

結果：

| 指標 | REQ-025 Control | REQ-026 Treatment |
|---|---:|---:|
| 月均報酬 | +1.87% | +2.98% |
| 月均 alpha vs TWII | -0.86pp | +0.25pp |
| 月均 alpha vs strategy-fit | +0.52pp | +1.63pp |
| MDD | -2.13% | -7.96% |
| 平均持倉數 | 2.38 | 4.80 |

REQ-026 沒有達到「升主線」門檻（alpha vs TWII > +1pp），但 April 2026 replay 方向正確：

- 2026-04 Treatment +20.98%，TWII +17.34%，alpha +3.64pp
- 2454 聯發科 2026-04-15 進場，截至 2026-05-08 仍持有，未實現 +102.79%

因此大票法人趨勢是牛市放大器，不是全天候自動策略。

## 手動 Overlay 規則

每日觀察三檔核心大票：

- 2330 台積電
- 2454 聯發科
- 2308 台達電

進場條件：

1. 外資連買 >= 3 日
2. 收盤站上 MA20

出場條件：

1. 外資連賣 >= 2 日

部位限制：

- 單檔不超過 20%
- 最多同時持有 2 檔大票

## 明確排除

- 不把 REQ-026 直接升為 production Champion
- 不替代 Champion 自動選股
- 不因大票 overlay 放寬 Champion 的 LOW_LIQUIDITY、sector cap 或 group cap
- 不把 TWII 單一口徑作為 Champion 是否失效的唯一判斷

## 後續觸發條件

若未來要自動化此 overlay，需要另開工單並重新驗證：

1. 擴大 universe 至所有 avg_20d_amount >= 30 億股票
2. 確認外資連買 / 連賣資料每日穩定
3. 建立獨立大票 overlay 帳本，不混入 Champion
4. 用 2026-04 之後 forward OOS 驗證，而不是只靠 April replay

