# 估值錯日來源族修復

L1 抓取來源修复與 L0 影響稽核。範圍固定於既有 1,622 日檔指紋稽核所指出的 21 組、57 日期，加同一報告內重複代碼日；不擴展為無界歷史重抓。查詢雙市場指定日期官方 API，保留 TLS 驗證、回應原始 bytes、HTTP status、實際 URL、知識時間及 SHA。期別、欄名、重複 keys、有限數值與來源完整性沿用 canonical contract。

八個 TPEx 回應中的字串 `null` 是官方未知值；解析後保留 NaN，與真實數字 0 區分。先前失敗回應保留，再由相同原始回應重播修正 parser，無須重抓。官方當下公司名稱可能已改名，不足以證明歷史名稱錯誤：同 ticker/market key 保留原歷史 Name，新恢復 key 才採官方目前名稱。20 個只有名稱改變的日期不發布。

37 個日期有實際數值或 key 差異，90,070 個既有數值格改變，恢復 1,415 個 ticker/market keys，移除 508 個錯日 keys。依 `scripts/audit_valuation_family_latest_features.py` 以同一 union DuckDB 的 2,031 個實際個股日期、全部估值歷史，逐股呼叫原 `compute_valuation_features`；10,155 個最新特徵格 exact equality（含 NaN）全數零差。未更動模型、rolling、as-of、截面公式或策略。

2026-09-06 21:58 已以 reviewed promotion plan 發布 37 檔，逐檔 before/after SHA、原始備份與 receipt 在 `output/data_layer_consistency_20260906/source_promotion/valuation_fingerprint_family_20260906/`。計畫 SHA `2dcd6a320cf2f0f84cff1adf9c8664f26b921e8eb21f4b379b4fcfc5e3e5ae13`。來源真值為本次查詢所得修订資料，不宣稱歷史原公告時可得。最新特徵零差也不代表歷史訓練資料或歷史策略報酬零差。

驗證：`python -B -X utf8 -m pytest tests/test_valuation_fingerprint_repair.py tests/test_backfill_valuation_completeness.py -q`，33 passed。新增 fixture 包含官方 null/zero、保留歷史名稱、排除純改名日、實際新增/移除 key 與 same-market duplicate 拒絕。
