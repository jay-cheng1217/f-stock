# TDCC Step 9 抓取失敗傳遞

已把 twstock.step9_update_tdcc 委派給已驗證的 scripts.fetch_tdcc_weekly.fetch_and_save()。此函式無參數，成功才更新 summary 並回 True；空回應 False 現在會讓 Step 9 拋 RuntimeError，HTTP/schema/date/coverage 例外直接傳遞。既有週檔仍每次查詢官方、驗證同週修訂，不再依檔案存在跳過。

Step 9 不再在抓取失敗後重算舊 summary，也不在成功委派後重複呼叫 legacy summarizer。22 個 focused/lineage tests 通過，包含真 canonical 空回應、同週重抓更正、錯資料保留既有 bytes、失敗不重建 summary。

本次沒有實際抓取或改正式 TDCC 資料，也沒有改級距、大戶/散戶定義、週期、80% minimum source floor、特徵或策略。此處修正呼叫流程，不把最低 coverage floor 宣稱為精確全市場完整性。root 負責 serial commit。
