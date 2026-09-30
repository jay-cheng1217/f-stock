# REQ-034-v3 Synthetic Boundary Appendix (2026-05-17)

## Scope
- Source dry-run date: 2026-05-16
- Synthetic rows are not real holdings.
- Synthetic rows are not included in red/yellow/green counts.
- Each case maps to a real coverage gap observed in the 12 holding dry-run.

## Synthetic Cases
| synthetic case | bucket | real coverage gap | conditions |
|---|---|---|---|
| synthetic_case_yellow_trim_watch_boundary | yellow | Real 12 holdings produced no yellow sample; this validates report rendering only. | OVERHEAT_MA20, NEWS_NEGATIVE_ALERT |
| synthetic_case_etf_rule_subset_boundary | yellow | Real 12 holdings include no ETF; this validates ETF coverage expectation only. | ETF_V2_ALPHA_MODEL_UNSUPPORTED, technical_rule_subset |
| synthetic_case_prediction_universe_fallback_boundary | yellow | Real 12 holdings all had prediction payloads; this validates fallback rendering only. | MODEL_UNAVAILABLE_REVIEW_REQUIRED |
