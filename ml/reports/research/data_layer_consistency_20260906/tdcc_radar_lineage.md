# TDCC 原始→週增雷達→使用端一致性驗收

結果：PASS_STAGING。修復分級 L1（來源／衍生／排程），公式與策略不變；執行契約為 `docs/METHOD_data_layer_consistency_20260906.md`。

## 根因與修復

原始集保 9/4 檔於 9/5 已更新，但 `tdcc_whale_weekly_scan.py` 沒有實際 Phase-1／Phase-2 排程呼叫。dashboard 另以 7/3 滾動快照比較 archive，形成原始新、衍生舊。直接補跑舊 producer 還可能把 7/3→9/4 誤算單週。

新的共用 `tdcc_whale_radar.py` 使用本地相鄰 W-FRI 週資料（容許假日週四實際生效日），拒絕跨缺週、日期混合／不符檔名、缺欄、NaN、重複級距及不足 15 個必要級距。雙週共同股票覆蓋與原始列數另設 80% 殘檔下限；80% 不是完整保證，新增／消失代碼逐一保留在 manifest。千張大戶仍為級距15、散戶仍為1–9；2330 大戶>50%、大戶+散戶≤100%（允許0.05pp四捨五入誤差）作語意不變量。

產生完整 delta、舊相容 snapshot 與榜單後才原子發布帶來源／輸出 SHA 的 status marker。consumer 使用相同完整 delta；來源／衍生 hash 不符、日期不符、raw 已更晚、future 或失敗狀態皆為 UNKNOWN，並印出可診斷 warning。Phase-1 raw 更新後與 Phase-2 顯示刷新前皆有獨立 catch-up step；其失敗是 step FAIL，不更動 Champion／rere gate。

`fetch_tdcc_weekly.py` 不再因同週檔存在就跳過。新 API 回應先依欄名嚴格轉換，再以 staging 驗證；不填補 NaN、不靜默丟列，驗證成功才原子替換。可修復同週已凍結殘檔；壞 API 不得覆蓋好檔或重建 summary。

## 真實週對與使用端

| 項目 | 修前 | staging 修後 |
|---|---:|---:|
| consumer 資料日 | 2026-07-03 | 2026-09-04 |
| 使用端有效雙週股票 | 2951 | 2960 |
| raw 週對 | 舊 snapshot 決定 | 2026-08-28→2026-09-04 |
| 9/4 raw 列數／全部代碼 | — | 68,867／4,051 |
| 9/4 四碼代碼 | — | 2,962（各17級） |
| 相鄰週共同代碼／覆蓋率 | — | 2,960／99.9325% |
| 命中既有命名雷達 | — | 14 |

新增 3718、8491 與退出 5286、6461 不捏造週差；兩週皆有資料才比較。現有公式（基準 revision `d9d2685c2f80d172011a99c6090cde64a09921d1`）與新程式在**相同 8/28／9/4 raw** 上，2,960 檔×6 個數值欄逐值完全相同，差異0、最大delta0，命中名單相同。此次是來源與排程修复，並未建立新的策略績效論據。

逐級抽查 2330：8/28 千張／散戶=84.75%／9.92%；9/4=84.74%／9.92%。JSON 列出級距1–9每項原始比例與級距15，與衍生值相符。

對現有 canonical rere 六檔只替換 TDCC evidence 的使用端檢查：狀態翻轉代碼=['3605', '3189', '2364']。完整前後理由見 JSON；仍用原判斷，不改 canonical 候選、分數、排序、gate、失效價或權重。正式名單共18檔沒有寫入。

## 官方 API 真實 smoke

2026-09-06 14:53:43+08 開始，6.50秒，官方 `https://openapi.tdcc.com.tw/v1/opendata/1-5` HTTP200。實際不同欄序由 strict_api_frame 正確處理，9/4的68,867列通過嚴格驗證；四碼2,962檔各級1–17齊全。與既有 raw 六欄68,867列數值全部相同；file SHA 不同僅因序列化格式，不能當成資料修正。此 smoke 只寫 `output/tdcc_lineage_20260906/live_fetch`。

## 驗證與正式 refresh 邊界

57 tests passed in1.81s（`test_tdcc_radar_lineage.py`、`test_entry_dashboard_cards.py`、`test_smart_update_auto.py`、`test_backfill_tdcc_summary.py`）。涵蓋真 partial/empty/wrong-date、缺週、假日週四、合法零命中、重跑等價、SHA損毀、future、manifest日期、舊快照忽略、API殘檔修復與好檔保存。已用新 consumer 實際讀 staging manifest/delta 驗證日期9/4；未以正式UI重寫充當驗收。

隔離生成命令：

```text
python -B -X utf8 scripts/tdcc_whale_weekly_scan.py --as-of 2026-09-04 --output-root output/tdcc_lineage_20260906/staging
```

正式 refresh **未執行**，交 root 協調：先執行上述 producer（省略 output-root，即寫正常衍生位置），再只讀既有 canonical 重建 dashboard。新 consumer 在正式 manifest 尚不存在時會誠實顯示 UNKNOWN，不能把舊 snapshot 當新。snapshot/delta/榜單/manifest 是本次唯一必要正式資料寫入範圍；沒有訓練、protected-model、帳本、正式 raw/summary、既有 dirty HTML、排程觸發或發信。

完整來源／產物 SHA 與 smoke receipt 見 `tdcc_radar_lineage.json`。測試／實作完成交 root 統一 commit 與通知。
