# Final Web HTTP acceptance: FAILED  Data as of 2026-09-04. Live listener PID 46920.  Daily health and latest ingest generation/counts are checked through the protected read-only /api/pipeline/status. /api/health_check is the legacy replay endpoint.  Predicted returns: API percent = Python round(CSV ratio * 100, 2). Latest close rounds to 2 decimals; probabilities to 4. Recommendations must match exactly.  Requests: 6. Comparisons: 27. Failures: 3.  Full responses: output/data_layer_consistency_20260906/final_web_http. Model identities, hashes, timings and comparisons are in the paired JSON. No model, source, ledger or service changes were requested.

Failures:
- explain.2603.pred_return_20d differs from certified CSV contract
- explain.3605.pred_return_20d differs from certified CSV contract
- explain.2330.pred_return_20d differs from certified CSV contract
