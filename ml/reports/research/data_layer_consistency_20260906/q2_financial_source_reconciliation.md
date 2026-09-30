# 2026Q2 財報來源補齊與 BS 欄位修復

狀態：候選已凍結，正式資料未 promotion，完整模型 C 分支待驗。

7 家原差額不是營益分析 parser 漏讀。官方 t163sb06 明列金融控股、銀行票券、證券期貨及異業不適用；2812 屬銀行，1409/1718/2207/2905 在官方異業損益表，5864/6026 在官方證券損益表。7 檔在本地 2020Q1–2026Q2 的 26 季營益表均無列；實際 canonical latest fundamental probe 的 77 個欄位全為 NaN，因此沒有偷偷沿用舊營益季度。不能把其他業別稅前損益冒充一般公司的營業毛利率。

本次真缺口：

| 來源 | 原本 | 官方當前 | 保留已申報歷史後候選 | 新增 ticker |
|---|---:|---:|---:|---:|
| financial | 1,825 | 1,935 | 1,937 | 112 |
| EPS | 1,832 | 1,972 | 1,974 | 142 |
| BS | 1,832 | 1,972 | 1,974 | 142 |

三表均保留現在已終止上市、但已有 Q2 申報列的 2867/5371。新 84 個股與新增財報交集為 4178/7717/7823/7827；142 新 EPS/BS 含 69 個既有 latest-eligible 股票，112 新 financial 含 42 個。現有 1806 有官方修正：EPS -0.07→-0.06、稅前利益率 -0.61→-0.25%、稅後利益率 -1.86→-1.50%，以及 BS 資產/權益/淨值。1721/4747/6949 另有官方公司名稱更新，與數值變更分列。

BS 原解析器按一般公司固定位置讀所有業別。銀行實際 61 欄、金控 58 欄、保險 53 欄、異業 22 欄；例如 2801 真 BVPS 為 19.52，舊 parser 讀成 916389（無形資產），2812 真 BVPS 15.95 被讀成 509701。已修為具名欄位定位，展開 rowspan/colspan，缺少流動資產/負債科目的金融業保留 NaN。

同一份官方 HTML 前後對照：32 檔、216 個原始欄位改正，其他業別原始值不變；全部 1,972 行通過資產－負債－權益誤差不超過 2 仟元的檢查。初步 audit 曾列 41 檔/225 格，其中 9 家證券商只是 audit 的 Parent_Equity 別名缺字造成假陽性，已排除，並非新增修復範圍。

真正 canonical feature probe 使用同一份日 K frame，其他歷史季度來源逐檔凍結，只替換 Q2 三表：147 檔最新特徵共 2,864 格不同，其中 75 檔符合現行資料長度/成交量/價格條件。模型 prob_edge、排名及 entry-list 影響由獨立 C 分支驗證；原 84-source A/B 不變。此處沒有重訓、改模型計算、門檻、rere 規則或帳本。

24 個 focused tests 通過，包含六種官方資料 fixture、會計等式、真 BVPS、金融科目不適用、跨欄跨列標頭、schema failure 與保留過往申報 ticker。最終候選位於 `output/financial_q2_repaired_20260906/candidate/`，SHA/官方 HTML/完整差異位於同目錄上一層 `manifest.json`、`latest_feature_probe.json`。

期別證據限制：financial 的 h2 明列「115年度第二季」；EPS/BS 的 h2 僅列「上市/櫃公司第二季資料」，非資料 HTML 沒有年份。已記錄真正 POST 年=115、季=2 與原始回應，另有當日官方 OpenAPI 明列 115/2 的核對樣本；不能聲稱 EPS/BS header 證明年份。全部修訂知識日期為 2026-09-06，不回填成當初就已知道。其他歷史季度缺漏/BS 舊錯位尚未回補，不宣稱完整歷史已修復。

重播（完全使用同一份已凍結 HTML）：

```powershell
python -B scripts/stage_q2_financial_reconciliation.py --output-dir output/q2_fresh_replay --financial-raw-dir output/financial_seven_source_audit_20260906 --frozen-manifest output/financial_q2_reconciliation_v2_20260906/manifest.json
python -B -m pytest tests/test_balance_sheet_named_headers.py tests/test_balance_sheet_source_errors.py tests/test_quarterly_fundamental_completeness.py -q
```

官方語意依據：[財務比較使用說明](https://mopsfin.twse.com.tw/terms)、[TWSE 異業損益表](https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim)、[TWSE 銀行損益表](https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi)。
