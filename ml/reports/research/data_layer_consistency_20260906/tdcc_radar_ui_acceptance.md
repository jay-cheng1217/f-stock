# TDCC 衍生更新：隔離 UI 驗收與只讀複核

結果：**PASS**。使用 root 既有 18 檔 canonical、固定的 9/4 隔離市場／模型資料與新 TDCC staging derivative。沒有重選股票、修改帳本或寫入正式 UI。

## 實際 browser 驗收

- Chromium 1440×1080 與 390×844：scrollWidth 分別1440／390，18張卡片，無水平溢出。
- rere仍為3605、3189、3066、2364、3176、6472六檔；embedded canonical順序不變。畫面按既有狀態分組，暫緩卡片排列到觀察卡片之後。
- 型態／模型來源均9/4；集保週報與大戶雷達均9/4且fresh。rere維持60交易日、最小試單，六檔都明寫「模型分數不否決 rere」。
- 3605、3189、2364按既有籌碼發散規則顯示「暫緩・有否決條件」，理由是9/4相鄰週資料，不是模型負分或新增門檻。其餘rere三檔仍待盤中確認。
- 實際截圖發現既有 `.card.c-skip{opacity:.55}` 淡化風險原因；經 root 授權只移除此一行，最終三張风险卡opacity=1。保留原暫緩badge、紅色風險框及全部數值／判斷。
- 最終console：0 errors／0 warnings。已目視確認手機完整宏致風險卡、桌機三檔風險卡的可讀性。

## 建置證據

命令：`python -B -X utf8 output/tdcc_lineage_20260906/build_dashboard_preview.py`。開始 2026-09-06T15:06:22.196264+08:00、22.58秒、PASS。隔離輸出 `F:\stock\output\playwright\tdcc_lineage_dashboard_20260906.html`，9,364,101 bytes，實際file SHA256 `c570384149fadd02717370f217049cf58910175454fbcdaa7d90bb8ae18bec38`。

renderer／consumer使用本次正式程式，市場與模型context指向既有實體隔離workspace；canonical只讀 root正式檔，TDCC loader只轉向新staging。canonical、watchlist與entry_filter／rere／shadow_dataA三帳本共5檔建置前後SHA完全相同，完整hash見JSON。這是有界的來源替換驗收，並非重跑全部ML流程。

## 只讀 review 結果與交接

在15:03用真函式＋全mock work units重現兩项缺陷：daily pipeline預測失敗仍會執行unified／portfolio／email；月營收同一期第一列NaN、第二列有效值會漏過重複檢查。fixture只寫output目錄，不跑工作入口、不寄信。完整前修復證據保留於JSON。

root已接手修復，回報新增明確artifact dependencies（commit `46a4dc0c`）並將月營收seen與valid集合分離。上述fixture描述的是**修復前實證**，不是聲稱final仍未解決；依分工未再擴大review或改root持有的兩份程式。

## 截圖

- `F:\stock\output\playwright\tdcc_lineage_desktop_20260906.png`
- `F:\stock\output\playwright\tdcc_lineage_mobile_20260906.png`
- `F:\stock\output\playwright\tdcc_lineage_risks_desktop_20260906.png`
- `F:\stock\output\playwright\tdcc_lineage_risk_mobile_20260906.png`
- `F:\stock\output\playwright\tdcc_lineage_risks_desktop_dimmed_before_20260906.png`
- `F:\stock\output\playwright\tdcc_lineage_risk_mobile_dimmed_before_20260906.png`

頁首截圖在唯一opacity修復前取得（日期/名單不受影響）；risk-card desktop/mobile為修復後最終截圖；dimmed_before檔刻意保留前狀態。正式重建、發布、寄信與commit由root統一；此subagent未執行。
