# 財報與月營收完整性檢查修復

分級 L1，僅修正來源稽核，未改模型特徵/策略/門檻。現有來源修復契約要求內容、期別與雙市場，不能僅以列數驗收。

- 財報新增 schema、股票唯一性、雙市場、Year/Season 與檔名一致、各市場必要值非全空檢查。逐季稽核不再忽略完全缺檔或 header-only 檔。
- 月營收改查實際應到月份及前月的有限數值，不再假設「最新一列就是應到月」或「有應到月就一定有前月」。早公布下個月的公司仍計入應到月覆蓋。
- TDCC 週五基準原本重複減掉一週，可能放過兩週舊檔；修為上一個週五。2330 千張大戶 0/NaN 也會觸發語意不變量。

驗證：`tests/test_fundamentals_audit_content.py`、`tests/test_quarterly_fundamental_completeness.py`、`tests/test_backfill_eps_completeness.py` 合計 27 passed。

2026-09-06 真實來源執行 `run_audit(heal=False)` 回傳 `[]`，78 份 2020Q1–2026Q2 季EPS/損益/資產負債來源均存在且非空，新增期別與市場檢查通過。最新 Q2 EPS/資產負債各 1832 列、損益 1825 列。這表示本次檢查通過，不代表以 80% 門檻證明每家企業都已申報，也未補造個別缺值。未執行 heal、資料重抓、訓練或帳本更新。
