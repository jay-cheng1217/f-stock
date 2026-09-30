# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-30`
- chipk_asof_date: `2026-06-30`
- model_buy_count: `36`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `10`
- watch: `17`
- conflict/do-not-chase: `9`
- top30 support_or_strong_rate: `36.7%`
- top30 weak_rate: `10.0%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6538 | 倉和 | 建議買進 | +0.54% | 0.136 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 2 | 0.662 |
| 2425 | 承啟 | 建議買進 | +1.21% | 0.047 | strong | sustained_accumulation | CONFIRM_ENTRY | 2 | 2 | 2 | 0.750 |
| 8121 | 越峰 | 建議買進 | +3.34% | -0.032 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 3044 | 健鼎 | 建議買進 | +1.95% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 2399 | 映泰 | 建議買進 | +1.90% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 2428 | 興勤 | 建議買進 | +1.73% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 2476 | 鉅祥 | 建議買進 | +1.63% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |
| 3088 | 艾訊 | 建議買進 | +1.52% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 2 | 0.662 |
| 5228 | 鈺鎧 | 建議買進 | +1.33% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 6903 | 巨漢 | 建議買進 | +0.65% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 3556 | 禾瑞亞 | 建議買進 | +0.06% | 0.206 | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 3 | 4 | 0.463 |
| 8150 | 南茂 | 建議買進 | +0.36% | 0.173 | neutral | short_term_chase_trap | DO_NOT_CHASE | 1 | 4 | 4 | 0.438 |
| 8043 | 蜜望實 | 建議買進 | +3.10% | 0.061 | neutral | short_term_chase_trap | DO_NOT_CHASE | 1 | 4 | 4 | 0.438 |
| 8086 | 宏捷科 | 建議買進 | +1.62% | 0.024 | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 6548 | 長科* | 建議買進 | +3.01% | - | weak | distribution_pressure | AVOID | 5 | 5 | 4 | 0.100 |
| 5475 | 德宏 | 建議買進 | +0.55% | - | weak | distribution_pressure | AVOID | 4 | 5 | 4 | 0.163 |
| 2441 | 超豐 | 建議買進 | +0.47% | - | neutral | short_term_chase_trap | DO_NOT_CHASE | 2 | 5 | 4 | 0.287 |
| 5289 | 宜鼎 | 建議買進 | +0.20% | - | weak | distribution_pressure | AVOID | 4 | 5 | 4 | 0.163 |
| 2308 | 台達電 | 建議買進 | +0.17% | - | weak | distribution_pressure | AVOID | 4 | 5 | 5 | 0.062 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 5299 | 杰力 | 建議買進 | +1.12% | 0.201 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 6265 | 方土昶 | 建議買進 | +1.48% | 0.116 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 6877 | 鏵友益 | 建議買進 | +2.43% | 0.061 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 2472 | 立隆電 | 建議買進 | +0.82% | 0.004 | supportive | constructive_pullback | WAIT_FOR_TURN | 4 | 2 | 2 | 0.625 |
| 3081 | 聯亞 | 建議買進 | +2.09% | -0.057 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 3374 | 精材 | 建議買進 | +4.87% | - | neutral | short_term_fade | WATCH | 5 | 4 | 2 | 0.388 |
| 4939 | 亞電 | 建議買進 | +2.74% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 3 | 0.412 |
| 5386 | 青雲 | 建議買進 | +2.12% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 6449 | 鈺邦 | 建議買進 | +1.89% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 3 | 4 | 0.275 |
| 3114 | 好德 | 建議買進 | +1.50% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 4973 | 廣穎 | 建議買進 | +1.01% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 6204 | 艾華 | 建議買進 | +0.93% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6197 | 佳必琪 | 建議買進 | +0.75% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 8299 | 群聯 | 建議買進 | +0.46% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 2 | 4 | 0.487 |
| 2368 | 金像電 | 建議買進 | +0.33% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 6789 | 采鈺 | 建議買進 | +0.10% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 3 | 0.412 |
| 2495 | 普安 | 建議買進 | +0.08% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
