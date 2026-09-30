# RD-002 Stage B Ex-Ante Slippage Proxy Offline Evaluation

Offline evaluation only. No production scheduler, order gate, model, database schema, or Champion attribution path was changed.

## Scope Locks
- Target label: `realized_entry_slippage` from the RD-002 Stage A trade/slippage frame.
- NAV unexplained, B1/B2/B3/B4, `b4_dominance_flag`, and `cross_window_sign_stability` are forbidden as target/features.
- Post-entry realized returns are forbidden as proxy features and are used only for avoided-bad-chase / missed-good-chase accounting.
- 6419 is treated as an execution-slippage case study only, not selection alpha or allocation evidence.

## Summary
- Trades evaluated: 135
- Candidate proxies: 5
- Selected by offline net skip oracle: `hybrid_rule_proxy`
- Recommendation: **OFFLINE_PROXY_APPROVED_SHADOW_REVIEW_HELD_FOR_PM_CONDITIONS**
- Shadow/production blocker: accepted Champion baseline required before shadow/production promotion
- PM condition 1 status: any candidate pass = False
- PM condition 2 SA recommendation: Path A: collect missing liquidity observations before shadow review

## Candidate Metrics
| candidate | Spearman | boot 90% lower | top quintile capture | best threshold | net skip | avoided bad | missed good | avoid-miss CI LB | shadow gate | PM CI gate |
|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|
| `chase_intensity_proxy` | +0.750 | +0.685 | +40.58% | top_40pct | +11.87% | +11.48% | +13.90% | -12.09% | False | False |
| `liquidity_impact_proxy` | +0.404 | +0.286 | +60.59% | top_30pct | +13.97% | +12.79% | +10.13% | -5.94% | True | False |
| `volatility_exhaustion_proxy` | +0.173 | +0.021 | +16.82% | top_40pct | +12.38% | +12.66% | +6.26% | +0.43% | False | True |
| `open_stress_proxy` | -0.683 | -0.736 | +6.16% | top_30pct | +6.96% | +9.24% | +3.56% | +1.40% | False | True |
| `hybrid_rule_proxy` | +0.737 | +0.668 | +59.88% | top_40pct | +14.52% | +14.35% | +13.84% | -9.44% | True | False |

## Selected Candidate Detail
- Candidate: `hybrid_rule_proxy`
- Best threshold: top_40pct at score >= 0.5324
- Net skip oracle: +14.52%
- Slippage saved: +14.01%
- Avoided bad chase: +14.35%
- Missed good chase: +13.84%
- Avoided minus missed: +0.51%
- Avoided-minus-missed bootstrap 90% CI: -9.44% to +10.68%
- PM condition 1 pass: False

## 6419 Case Appendix
The table below lists only ex-ante feature values and proxy verdicts available before the 2026-05-12 entry. The known +9.85% entry slippage is the offline validation label, not a feature.

| candidate | proxy score | best cutoff | would flag | order type | price vs MA20 | rank 20d | prob edge |
|---|---:|---:|---|---|---:|---:|---:|
| `chase_intensity_proxy` | 0.8068 | 0.4838 | True | INTRADAY_CHASE | -0.35% | 3 | +9.06% |
| `liquidity_impact_proxy` | 0.7094 | 0.6125 | True | INTRADAY_CHASE | -0.35% | 3 | +9.06% |
| `volatility_exhaustion_proxy` | 0.2776 | 0.5428 | False | INTRADAY_CHASE | -0.35% | 3 | +9.06% |
| `open_stress_proxy` | 0.1463 | 0.8008 | False | INTRADAY_CHASE | -0.35% | 3 | +9.06% |
| `hybrid_rule_proxy` | 0.5841 | 0.5324 | True | INTRADAY_CHASE | -0.35% | 3 | +9.06% |

## Feature Coverage Notes
- `order_type`, target size, planned reference price, price-vs-MA20, rank, score, and probability edge are available for the current frame.
- ADV, spread, intraday volume curve, and limit-up/limit-down state are not available in this dataset.
- Liquidity impact is therefore a partial order-size proxy, not a true market-liquidity model.
- SA recommends Path A before shadow review: collect missing liquidity observations under a timebox, then rerun this offline report.

## Adoption Status
- Offline design: complete.
- Shadow candidate: held until PM conditions pass and Champion baseline is accepted.
- PM condition 1 is not met for the selected hybrid proxy if its bootstrap lower bound is not above zero.
- PM condition 2 requires PM sign-off on the liquidity path decision before shadow review.
- Production candidate: not authorized by this report.
- If PM rejects the current partial liquidity proxy, the next RD step is expanded observation fields, not a production rule.
