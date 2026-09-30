# 2026-03-20 融資資料修補候選與影響驗證

尚未寫入正式資料。TPEx 當日快取雖為 `stat=ok`，實際為 0 列；官方重取為 889 列。再查明總表把 ETF 前導零丟失，006203–006208 的部分數值誤落 6203–6208，其中 6 檔股票共 10 個餘額欄位不符官方。修正增量讀表的 Ticker 字串型別，並讓 TWSE cleaner 保留 75 個真實英數 ETF 代碼；不變更模型股票篩選條件。

雙市場重建當日 2,142 列（TWSE 1,253、TPEx 889），舊總表當日為 1,176 列。候選刪去 19 個遺失前導零的錯誤鍵、補上 985 個缺鍵並修正 10 個錯值；其他日期所有值不變。全表列數 3,013,611 → 3,014,577。

日 K 比對覆蓋雙市場所有官方證券：1,800 檔的兩個融資券欄位皆未知；46 檔 ETF 有 63 個不正確的 0。共 1,846 份候選，只修改現有 3/20 行的兩個欄位，其他行、非融資數值文字、BOM 及換行保留。另有 8 檔已相符、47 檔 ETF 缺 margin 欄位、9 檔沒有當日價格棒、232 檔無 CSV；後三類不新增欄位或假價格棒。

同一份凍結 CSV 分別使用修補前後兩個欄位，執行真正 `compute_institutional_features`：1,526 檔、4,573 個歷史 ticker-date 有特徵差異，其中 3,180 個通過 canonical 歷史 as-of universe 條件。影響包括融資/融券 5/10 筆變化、融資券比及籌碼背離；最晚影響日為 4/16。1,823 個實際 9/4 行及所有檔案各自最新行差異皆為 0。`prob_edge` 為 N/A：此處沒有重跑模型推論、訓練或績效回測，不能宣稱舊模型已修正。

歷史初查有 28 種前導零別名、43,131 筆待官方逐日核對，包含合法股票代號，不能直接視為 43,131 筆錯值。2020 起的 1,622 個官方交易日雙市場原始檔均存在；TPEx 逐檔讀取只有 3/20 少於 50 列，其他均為 tables 格式。完整語意/逐值對帳尚未執行，因此全歷史重建仍需獨立驗證。

12 個 focused tests 通過；實際 TWSE parser 的 1,253 列與嚴格具名 parser 完全一致，包含 1,178 個數字代碼及 75 個英數代碼。候選、每份來源 SHA、舊值/新值、特徵差異及 generic promotion plan 位於 `output/margin_20260320_repair_full_v2_20260906/`。promotion 需通過來源原始 SHA 與報告 evidence SHA 防護，並由 root 統一處理；本 agent 不改 DB、模型、帳本、服務或排程。

可重現命令：

```powershell
python -B -m pytest tests/test_margin_day_repair.py tests/test_margin_source_completeness.py -q
python -B scripts/audit_margin_day_repair.py --day 2026-03-20 --tpex-raw output/new_stock_sources_20260906/raw_margin_overrides/raw_margin_tpex_20260320.json --output-dir output/margin_day_fresh_audit
```

原始失敗與 TPEx-only 探索候選保留作 provenance；以 `margin_20260320_repair_full_v2_20260906` 為本次最終全量證據。原始資料缺漏會改變歷史研究/未來重訓的輸入；這不等於允許自動重訓或改動 rere 原始規則。
