# 審計:Trust/Dealer 髒資料消費者全盤點與重判(2026-07-02)

背景:TPEX 法人欄位錯位 6 年(上櫃 Trust≈0、Dealer=外資+投信),2026-07-02 修復並
全量重建。本審計盤點**所有曾消費髒欄位者**,並對受污染的「判決」重判。

## 一、消費者分類與處置

| 消費者 | 層級 | 影響 | 處置 |
|---|---|---|---|
| `ml/features/institutional.py`(trust_cumsum/inst_total/foreign_trust_sync/trust_reversal) | **V2 模型特徵** | 上櫃 46% universe 六年錯值 | 🔄 DATA-PR-009 重訓鏈進行中(Stage1 已完成) |
| `ml/features/institutional.py::chip_diverge_bear/bull` | **硬擋 #13(籌碼背離)** | 用 inst_total → **上櫃股此硬擋六年失靈**(該擋沒擋/誤擋) | ✅ 公式沒錯、輸入已修,今後自動正確;歷史 guardrail 決策對上櫃不可信(僅註記) |
| `ml/features/entry.py`(validated chip momentum filter) | **production 進場 filter** | 選股用髒 trust → 歷史每日選擇受影響 | ✅ 每日重算,今後自動用乾淨值;其 +320% 回測判決由 suite 重跑覆核 |
| `ml/predict.py` 法人賣壓/inst_ok(推薦分級) | production 推薦 | 上櫃股歷史推薦受影響 | ✅ 每日重算自癒 |
| `app.py`/`backend/*`(charts/chat/stocks/ranking/scoring/workbench) | 顯示/解釋層 | 展示錯值 | ✅ 讀日K即時自癒 |
| `ml/dataset_t1.py` | T+1(已凍結 research) | — | 註記即可 |
| `scripts/agent_arena.py` 等 research | research | — | 註記即可 |

## 二、判決重判結果

**原則:凡在 07-02 12:05(重併完成)之前、且用到 Trust/Dealer 的回測判決一律重跑。**

| 判決 | 原結果(髒) | 重判(乾淨) | 裁定 |
|---|---|---|---|
| 季末投信做帳 | 買超 −1.31%/39% 無edge | 買超 **−0.95%/41.5%**(vs 賣超 −0.96%)仍無分辨力 | ✅ **維持否決** |
| 土洋同買 | +0.93%/45.5% 過濾過頭 | **+1.98%/49.9%**(數字改善但仍低於 base +2.68%) | ✅ **維持否決**(髒資料低估了它,但結論不變) |
| 四大面向(髒 alpha −6.7% → 乾淨 −1.1%) | 無edge | 仍無edge | ✅ 維持不採用 |
| 因子組合 IC(0.060 → 0.0598) | — | 相同 | ✅ 維持 |
| MA60反彈(−0.19%/44% → 同值) | 降權 | 相同 | ✅ 維持降權 |
| 策略搜索最佳(chip_momentum_top5 +320.5% → **+322.1%,仍是3785種最佳**) | 驗證現行 | 更強 | ✅ 維持 |
| scoreboard(11:20) | — | 無需重跑:其 5 策略只用 Foreign(一直正確)+價格 | ✅ 判決有效 |
| 三味藥(投信連買3/融資減/TDCC) | — | 本來就在修復後跑 | ✅ 有效 |
| rere lane / ma20_combo / momentum | — | 只用 Foreign+價格 | ✅ 有效 |
| friend combo 的 `flow_dealer_nonnegative` overlay | dealer 髒 | 該 overlay 判讀不可信;主結論(pullback+MACD+量靜)不依賴 dealer | 註記,擇期重掃 |

## 三、結論(2026-07-02 14:20 最終)

**重判收官:所有策略判決在乾淨資料下全數維持,零翻轉。** 髒資料影響的是模型特徵
(DATA-PR-009 重訓中)與個別數字精度,未曾把任何策略結論帶錯方向。

## 三之一、原結論

1. **策略層判決:重判後全數維持**(季末做帳/土洋同買否決不變;rere lane 等本就乾淨)。
2. **真正的殘留風險集中在模型**(DATA-PR-009 重訓鏈處理中)與 **chip momentum filter 歷史選擇**
   (suite 重跑覆核中)。
3. **chip_diverge 硬擋**今後自動正確;歷史上對上櫃股的攔截紀錄不可用於歸因分析。
4. 顯示/推薦/gate 層皆屬「每日重算」性質,資料修復即自癒,無需改碼。
