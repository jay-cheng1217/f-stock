# 2021-05-10 估值來源錯日修復候選

完整雙市場官方對帳證實，舊 `valuation_20210510.csv` 的上市分支實際裝入 2021-05-18 資料：951 檔券碼、951 個名稱及 2,853 個數值格，與本地 5/18 檔和本輪實抓官方 5/18 回應全部逐值相同。這是全市場內容指紋，並非僅憑 3092 重複推測。原始抓取請求或覆寫機制缺少歷史紀錄，無法再斷言是 cache 或 API 修訂。

3092 的官方轉上市日為 2021-05-13。5/10 官方 TWSE 估值沒有 3092，TPEx 估值有 3092（PE 64.56、PB 3.38、殖利率 2.04%）；當月 TWSE 日K首日 5/13，TPEx 5/10 收盤 73.60。因此舊檔 3092 上市列（PE 51.05、PB 2.67、殖利率 2.58%）是錯日上市分支帶入，應以當期上櫃列為準。

隔離候選以 5/10 官方 TWSE 950 列與 TPEx 785 列重建整天，不只刪除 3092 重複。1736→1735 列；947 檔共 2546 個數值格改變（PE 809、PB 943、殖利率 794），全部在上市分支；上櫃 785 列數值未變。官方未知值保留未知，7 格未知→已知、24 格已知→官方未知，未填造 NaN。

- CURRENT 正式檔 before SHA256：`4fff924e7084742473c1cfbfe4785f8f053314d25f700db8d50db8c72e26f8fe`
- 完整候選 SHA256：`6bfcc6faf5b77b29ced51638631e243069fc8958daeaeed1c039f44f4600a27c`
- 完整候選：`F:/stock/output/pipeline_acceptance_20260906/workspace/output/historical_gaps_20260906/valuation_day_20210510/valuation_20210510.csv`
- 全部數值變更：`valuation_20210510_cell_changes.csv`
- 最新特徵 A/B：`valuation_20210510_latest_ab.csv`、`valuation_20210510_repair.json`
- 錯日內容指紋：`valuation_20210510_date_lineage.json`

用一次凍結的實際截至 9/4 日K日期，逐券保留估值函數最新特徵完整依賴的 60 筆原始觀測，以同一 canonical `compute_valuation_features` 計算 before/after。1,985 檔 × 5 個特徵 = 9,925 格，exact 差異 0（含相等 NaN）。這是最新依賴範圍等價證據，不表示歷史樣本或歷史預測不變，也不是回測。完整歷史 rolling/訓練影響未做全市場推論；沒有重訓。

先前只刪 3092 上市列的候選及兩券歷史 A/B 已被本次完整日候選取代，不可發布舊的局部候選。正式發布應由 root 檢查 CURRENT before SHA 後 compare-and-swap，另留下 promotion receipt。本文記錄候選驗收，不代表已發布。

來源與驗證：

- [TWSE 2021-05-10 估值](https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20210510&response=json)
- [TPEx 2021-05-10 估值](https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&d=110/05/10&o=json)
- [TWSE 2021-05-18 估值內容指紋](https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20210518&response=json)
- [TPEx 3092 終止上櫃官方公告](https://www.tpex.org.tw/storage/eb_data/11005/11000571291.html)
- [TWSE 3092 當月日K](https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=20210501&stockNo=3092&response=json)
- [TPEx 3092 當月日K](https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=3092&date=2021/05/01&response=json)

隔離 runner `valuation_20210510_complete_stage`、`valuation_may10_may18_lineage` 成功；估值來源契約 9 個 fixtures 與 TDCC 17 個 fixtures 共 26 passed（`valuation_tdcc_contract_fixtures_local_log`）。來源 date/status、欄名重排、重複、非有限值、合法未知與 0、轉板前後市場判定均有檢查。最初 pytest 預設寫 Windows nul 被 guard 拒絕，將 log 指向副本後通過，未放寬 guard。

防止再發的 production 修復為 `scripts/backfill_valuation.py` 與無副作用共用 parser `scripts/valuation_source_contract.py`：兩市场必須回傳同一 requested date；TPEx內表民國日期也須一致；從官方具名欄位映射、拒絕錯日/重複欄名/重複券碼/殘列/非有限值與空表。雙市場合併另擋ticker重複，保留原最低1500列門檻和數值範圍防護。未知數字標記保留NaN、0保留0；不再將失敗或空schema稱為合法no_data。假期須由行事曆/官方休市流程另證，不能從空估值回應推定。

更新後共50個相關fixtures通過（估值fetch/completeness24、repair9、TDCC17），receipt `valuation_recurrence_contract_tests_roc`。真正呼叫兩個 production fetch 函數，官方5/10返回950+785列，與完整candidate全部逐值相同；真實官方5/18回應拿來要求5/10時被拒絕。`valuation_fetch_contract_acceptance.json` 保存此實網驗收及code SHA。首輪實網核驗發現TPEx內表使用民國110/05/10日期，原過嚴西元字串比較擋下；已按實際格式解析並加入內外日期衝突fixture，未繞過日期核對。

Generic compare-and-swap plan：`valuation_20210510_promotion_plan.json`，相容 `scripts/verified_source_promotion.py`；包含完整candidate、原始SHA與三份驗收證據SHA，由root執行發布。

為避免嚴格空回應處理把假期當抓取故障，`generate_trading_days` 已接 canonical `is_taiwan_trading_day`。該共用模組明示完整年表僅2026，其他年份不能從單一adhoc日期推定已完整：先向官方年度行事曆用 `date=YYYY0101` 查詢，驗實際 date/queryYear/欄位/每列日期，排除市場無交易、僅結算與放假日；無法辨識的事件或錯年回應會拋錯。另有4個fixture（本檔共28 passed），真正2021官方年表所得244個交易日與現有244個估值檔日期全等，詳見 `valuation_calendar_acceptance.json`。新驗收使用新實體檔名載入相同SHA的production模組，沒有覆寫正在進行C組A/B的frozen workspace既有檔案。
