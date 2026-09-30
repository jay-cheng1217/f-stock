# 74項基準保護範圍驗收

基準時間：**2026-09-06T14:40:45.357378+08:00**，HEAD `b74d7cc9bd1b4ca0dd315013188edfbf075d10dd`。基準檔 SHA `e71295794fd9730f9327ced27458c3807fda341101c81605577919394c41f49c`。
本次觀測：2026-09-06T22:42:26.121545+08:00 至 2026-09-06T22:42:26.208357+08:00。比較以當時捕獲的檔案bytes／SHA為準，不以HEAD為準。

結果：**66項模型／pins／selection與registry／帳本全部不變；74項共72項SHA相同、2項變更（1項授權衍生預測、1項授權使用端程式），0檔遺失。** 7項衍生檔中正式20D預測已更新，其餘6項在此读取時間尚未更新，不表示其內容已符合最新來源，也不代表後續不能更新。

| 類別 | 數量 | 本次結果 |
|---|---:|---|
|pins|6|全部SHA與基準相同|
|authorized_derived|7|1份正式20D預測更新，其餘6份SHA相同|
|model|54|全部SHA與基準相同|
|model_selection_registry|2|全部SHA與基準相同|
|ledger|4|全部SHA與基準相同|
|strategy_consumer|1|授權provenance讀者變更；核心策略AST完全相同|

唯一策略使用端程式變更為 `scripts/generate_entry_candidates.py`：新增可信snapshot讀者、以prediction成功證書讀CSV，無有效證書時明確拒絕。`generate`、`_technical`、`_pattern_ok`、`_rere_lane_ok`、`_apply_data_quality` 五個完整函式AST與基準逐值相同。實際改動僅 `_snapshot_asof_date`、`_load_predictions`、`_snapshot_fields`，新增 `_verified_snapshot`。這是已授權來源一致性防護；正式新預測／canonical仍由root另行驗收。

`entry_filter_ledger.csv`、`rere_lane_ledger.csv`、`shadow_dataA_ledger.csv` 在基準擷取時就已對HEAD顯示dirty；本次SHA均與基準完全相同，不能把原有修改歸因本輪。`paper_portfolio.db`同樣與基準相同。

下表逐項列出分類及結果；完整64位baseline/current SHA、bytes、存在性與逐檔觀測時間見同名JSON。

| 路徑 | 分類 | SHA結果 |
|---|---|---|
|`archive/model_pins/lgbm_20260516_112903.txt.sha256.txt`|pins|相同|
|`archive/model_pins/lgbm_alpha_cls_v2_20260422_220036.txt.sha256.txt`|pins|相同|
|`archive/model_pins/lgbm_t1_20260417_191108.txt.sha256.txt`|pins|相同|
|`archive/model_pins/lgbm_two_stage_ranker_20260509_011500.txt.sha256.txt`|pins|相同|
|`archive/model_pins/lgbm_v2_20260508_200128.txt.sha256.txt`|pins|相同|
|`config/champion_pin_manifest.yaml`|pins|相同|
|`logs/entry_list_20260907.json`|authorized_derived|相同|
|`ml/models/dataA_predictions_2026-09-04.csv`|authorized_derived|相同|
|`ml/models/lgbm_20260425_111857.txt`|model|相同|
|`ml/models/lgbm_20260425_111857_meta.json`|model|相同|
|`ml/models/lgbm_20260509_103227.txt`|model|相同|
|`ml/models/lgbm_20260509_103227_meta.json`|model|相同|
|`ml/models/lgbm_20260516_112903.txt`|model|相同|
|`ml/models/lgbm_20260516_112903_meta.json`|model|相同|
|`ml/models/lgbm_alpha_cls_v2_20260422_220036.txt`|model|相同|
|`ml/models/lgbm_alpha_cls_v2_20260422_220036_meta.json`|model|相同|
|`ml/models/lgbm_t1_20260417_052120.txt`|model|相同|
|`ml/models/lgbm_t1_20260417_094352.txt`|model|相同|
|`ml/models/lgbm_t1_20260417_191108.txt`|model|相同|
|`ml/models/lgbm_t1_20260417_191108_meta.json`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260509_011500.txt`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260509_011500_meta.json`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260512_214551.txt`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260512_214551_meta.json`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260516_180000_inc001.txt`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260516_180000_inc001_meta.json`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260516_190000_v3.txt`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260516_190000_v3_meta.json`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260702_141205.txt`|model|相同|
|`ml/models/lgbm_two_stage_ranker_20260702_141205_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260508_200128.txt`|model|相同|
|`ml/models/lgbm_v2_20260508_200128_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260509_103417.txt`|model|相同|
|`ml/models/lgbm_v2_20260509_103417_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260512_214522.txt`|model|相同|
|`ml/models/lgbm_v2_20260512_214522_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260516_113451.txt`|model|相同|
|`ml/models/lgbm_v2_20260516_113451_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260523_172032.txt`|model|相同|
|`ml/models/lgbm_v2_20260523_172032_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260530_104515.txt`|model|相同|
|`ml/models/lgbm_v2_20260530_104515_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260606_135928.txt`|model|相同|
|`ml/models/lgbm_v2_20260606_135928_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260610_231825.txt`|model|相同|
|`ml/models/lgbm_v2_20260610_231825_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260614_233050.txt`|model|相同|
|`ml/models/lgbm_v2_20260614_233050_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260702_131745.txt`|model|相同|
|`ml/models/lgbm_v2_20260702_131745_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260711_123718.txt`|model|相同|
|`ml/models/lgbm_v2_20260711_123718_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260816_014222.txt`|model|相同|
|`ml/models/lgbm_v2_20260816_014222_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260817_003253.txt`|model|相同|
|`ml/models/lgbm_v2_20260817_003253_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260822_105105.txt`|model|相同|
|`ml/models/lgbm_v2_20260822_105105_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260829_105043.txt`|model|相同|
|`ml/models/lgbm_v2_20260829_105043_meta.json`|model|相同|
|`ml/models/lgbm_v2_20260905_114605.txt`|model|相同|
|`ml/models/lgbm_v2_20260905_114605_meta.json`|model|相同|
|`ml/models/model_selection.json`|model_selection_registry|相同|
|`ml/models/predictions_2026-09-04.csv`|authorized_derived|授權衍生預測更新；數值另驗|
|`ml/models/predictions_shadow_v23_2026-09-04.csv`|authorized_derived|相同|
|`ml/models/predictions_t1_2026-09-04.csv`|authorized_derived|相同|
|`ml/models/predictions_v4_rank15_2026-09-04.csv`|authorized_derived|相同|
|`ml/models/registry.json`|model_selection_registry|相同|
|`ml/models/unified_signals_2026-09-04.csv`|authorized_derived|相同|
|`ml/reports/entry_filter_ledger.csv`|ledger|相同|
|`ml/reports/rere_lane_ledger.csv`|ledger|相同|
|`ml/reports/shadow_dataA_ledger.csv`|ledger|相同|
|`paper_portfolio.db`|ledger|相同|
|`scripts/generate_entry_candidates.py`|strategy_consumer|授權provenance程式變更；策略AST相同|

此為上述時間區間的事實，不宣稱未來不變。未新增、刪除、改寫任何受稽核檔案；只寫本JSON／MD報告。衍生快照及新證書不在這74項基準內，已由獨立source bridge驗收。提交由root統一。

後續發現：第一輪正式20D driver未帶nightly pin環境，證書顯示V2實選9/5 weekly、ranker未啟用；該輪數值比較FAILED。模型檔與selection JSON未變不等於執行時選模相同。這項runtime問題已通知root，必須另行修正driver並驗證，不以本報告的靜態保護PASS取代。
