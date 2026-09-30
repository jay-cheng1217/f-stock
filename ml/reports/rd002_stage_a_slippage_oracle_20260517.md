# RD-002 Stage A Slippage Oracle Diagnostic

Diagnostic only. This report uses realized slippage to estimate an upper bound; it is forbidden to use realized slippage directly in Stage B.

## Summary
- Trades with slippage data: 135
- Slippage-only oracle upper bound: +15.97%
- Price-improvement offset: +2.89%
- Net realized slippage drag: -13.08%
- Stage B kill switch: +0.50%
- Recommendation: **ALLOW_STAGE_B_DESIGN**

## By Order Type
| order_type | trades | avg slippage | slippage cost | avg effective return |
|---|---:|---:|---:|---:|
| INTRADAY_CHASE | 54 | +4.08% | +14.29% | -0.39% |
| OPEN | 81 | -0.45% | +1.68% | -1.08% |

## Oracle Threshold Table
| threshold | trades blocked | slippage saved | avoided bad chase | missed good chase | net skip oracle |
|---:|---:|---:|---:|---:|---:|
| +1.00% | 54 | +14.29% | +11.48% | +13.90% | +11.87% |
| +1.50% | 53 | +14.23% | +11.48% | +13.82% | +11.89% |
| +2.00% | 47 | +13.64% | +9.55% | +13.71% | +9.49% |
| +2.50% | 40 | +12.89% | +7.51% | +13.71% | +6.69% |
| +3.00% | 35 | +11.67% | +5.22% | +13.71% | +3.18% |
| +3.50% | 28 | +10.49% | +3.49% | +13.42% | +0.55% |
| +4.00% | 21 | +9.04% | +2.36% | +12.39% | -0.99% |

## Stage B Guardrails
- If the oracle upper bound is below +0.50pp, Stage B should be killed and not designed.
- If Stage B proceeds, it must use an ex-ante `predicted_slippage_proxy`.
- Stage B must report avoided-bad-chase and missed-good-chase separately.
