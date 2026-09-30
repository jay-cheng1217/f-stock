# 缺失官方股票來源回補 A/B（2026-09-06）

唯一變因：加入既有抓取器因「只枚舉現有 CSV」漏掉的 84 個普通公司來源（4 TDR 另列未納入）。原有模型、特徵算法、eligibility、產業排名、rere 型態與倉位/出場方法全不變。新來源只有官方未還原 OHLCV，不借用前身股票歷史；法人、融資等未補齊欄位保留 unknown。

先驗收再 promotion。下列方法在 A/B 執行前鎖定：

- 凍結原有隔離資料與新增 84 檔 official source manifest（9/4 as-of、16,024 rows）。來源快照唯一，使用隔離 DuckDB as-of union raw frame供兩分支。existing ticker 原始資料不可因分支改變。
- A：現有來源 universe；B：同一份 union frame 加入 84 檔。每檔 raw 特徵可共用一次計算，再各自套用 canonical cross-sectional ranking/sector/industry/winsorization，以保留 universe 對排名的實際影響。
- 保持正式 pin 與 environment；不重訓。existing ticker/date 一對一比較 prob_edge、pred_return、推薦/TopN變化，列新 eligible/ineligible 原因與缺值。若比較 dataA/rere，也必須用同分支快照，不能混用兩份日期或模型。
- 驗收：官方日期/價格/唯一性/來源完整性通過、未拼前身歷史、全部新增仍受原 eligibility 約束；預測有限、舊樣本無原始資料差異，策略/模型/門檻 SHA不變。量化所有輸出改變，不能用只看平均差掩蓋極端值。
- 若新 source缺欄引發feature例外或資料契約失敗，停止該分支promotion，修資料取得或明列unknown；不得用零值湊出可買訊號。
- 不以截面 A/B 宣稱新股票歷史績效或新策略有效；未更改策略的本輪以來源完整性/行為差異為驗收。任何額外特徵定義或策略修改仍需另行PM判斷。
