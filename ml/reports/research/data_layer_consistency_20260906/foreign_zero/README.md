# 外資持股數字零值解析修復 — 2026-09-06

`twstock.py::_parse_twse_number` 原先使用 `not s`，把官方 JSON 數字 `0.0` 誤轉 NaN；目前僅讓有觀測的數字零保留為零，明確缺值字串仍為 NaN。這是 L1 parser 修復，沒有改選股、模型或 rere 門檻。

隔離 staging 以 CURRENT `F:/stock/外資持股` 讀取現場原始 bytes，未使用較舊驗收副本的 ownership。1,618 檔共 29,557 個缺失百分比可重建：21,252 筆持有股數正好為零；8,305 筆持有股數大於零，但以已知股數除發行股數得到的百分比小於 0.01%。後者重建的是官方顯示精度的 `0.00`，不是宣稱實際持有股數為零。一般未知值不做全面填補；本批恰好沒有其餘模糊缺值。

另對 2020-01-02、2026-09-04 實抓 TWSE 官方回應，逐一對上同券、同發行股數、同持有股數，兩日均含「真零」及「正持有股數、官方百分比數字零」案例。每個改動儲存日期、券碼、舊值、新值、股數、推導比率與依據。原始 bytes 保存在隔離 `zero_repair/inputs`，來源回應 hash 與每檔 before/after hash 見 `repair_manifest.json`。這是已知 parser bug 的重建，不是全 1,618 日重新向官方逐值下載。

所有檔案維持原列序、券碼、發行股數、持有股數及原本非缺失的百分比。稍早已正式補齊的四個日期保留 CURRENT 來源；2026-08-07 仍為 2,249 列，不會倒退回舊 889 列。

同一完整 9/4 as-of 日 K 逐檔只讀一次、兩分支共用，重新計算 1,901 檔 × 14 項 canonical 外資／周轉率特徵，共 26,614 格，變動零、最大差零。最新 `Issued_Shares` map 完全相同；外資 1/3/5/10/20 日累計来自日 K 的法人買賣超，並不使用本次修復的百分比。這是受影響輸入群的等價驗證，不是新的模型推論或全流程重跑。

最新 API `Foreign_Pct` 的 13 檔由 unknown 變為 0：00625K、00636K、00641R、00643K、00657K、00668K、00684R、00707R、910861、911622、911868、9928、9941A。9/4 before SHA 為 `bd04d8be6eb6a614145fa3f0f2618070104edd075a05bf70807d5a12250c20da`，after 為 `a5415b80b1ee27cc654498f14311cc362415b3a3cd41145ff4f106bfba968b1d`。

原始 dataset cache 的 ownership glob mtime 會因 promotion 失效；`app._load_foreign_ownership` 每次呼叫讀取最新檔，並沒有 process map cache。外層 API 回應是否已更新仍交由正式 API 驗收確認，不能把模型群數值相同當成 API 驗收。本腳本不寫正式 cache、snapshot、prediction、DB 或帳本。正式 promotion 由 root 逐檔比對 CURRENT before SHA，任何衝突停止並重新 staging；root 已於 15:13:56 完成 1,618 檔 promotion，正式 receipt 見 `promotion.json`。

驗證 receipt：`foreign_zero_stage`、`foreign_zero_latest_ab`、`foreign_zero_parser_tests` 均 PASS；20 個 focused tests 驗證數字零、缺值保留、錯日期、欄位錯置、混合單位、duplicate 與模糊資料不得重建。

官方來源：[TWSE 外資持股](https://www.twse.com.tw/zh/trading/foreign/mi-qfiis.html)，實抓 API 為相同官網 `/rwd/zh/fund/MI_QFIIS` 的指定日期回應。
