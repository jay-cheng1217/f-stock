# Champion 全新紙上帳本驗收 — 2026-09-06

PASS：使用實體副本、已安裝 guard、9/4 新產 unified CSV 與正式 nightly policy/config。只改 SQLite 目標為全新驗收檔。

- 首次：新增 run 1、新增 pending 1、marks 0；保留配置權重 0.998500。
- 第二次：新增 run 0、新增 position 0、略過已鎖定 run 1、新增配置 0.0。
- run/position/mark 唯一鍵、完整業務欄位及 SQLite integrity/FK 檢查通過。僅排除 canonical pending refresh 的 updated_at 稽核時間。
- 既有副本 Champion/Shadow SQLite 雜湊完全相同；guard 無拒絕事件。
- Policy：asymmetric_v2；rule：unified-v2-order-sim:paper:champion。

行情截止 9/4，因此 9/7 尚未成交。這次實際驗證新增 pending、refresh 與 run lock；marks=0 是正確等待狀態，尚未驗到未來成交後的 mark/exit 寫入。配置權重不代表已成交。

輸入逐欄 gate、行情雜湊、兩次 canonical 回傳與資料庫路徑詳見 portfolio_acceptance.json。
