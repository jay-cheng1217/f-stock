# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-25`
- chipk_asof_date: `2026-06-25`
- model_buy_count: `34`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `8`
- watch: `15`
- conflict/do-not-chase: `11`
- top30 support_or_strong_rate: `30.0%`
- top30 weak_rate: `23.3%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 3406 | 玉晶光 | 建議買進 | +2.30% | 0.398 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 8046 | 南電 | 建議買進 | +1.98% | 0.056 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 2472 | 立隆電 | 建議買進 | +3.40% | - | strong | near_term_accumulation | CONFIRM_ENTRY | 1 | 2 | 3 | 0.713 |
| 2476 | 鉅祥 | 建議買進 | +2.11% | - | strong | constructive_support | CONFIRM_ENTRY | 3 | 1 | 2 | 0.775 |
| 2059 | 川湖 | 建議買進 | +1.53% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6139 | 亞翔 | 建議買進 | +0.73% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 3034 | 聯詠 | 建議買進 | +0.25% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 6121 | 新普 | 建議買進 | +0.24% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 2 | 1 | 1 | 0.938 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6426 | 統新 | 建議買進 | +1.46% | 0.314 | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 3 | 4 | 0.463 |
| 6265 | 方土昶 | 建議買進 | +6.17% | 0.115 | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 8358 | 金居 | 建議買進 | +4.17% | -0.024 | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 5386 | 青雲 | 建議買進 | +7.88% | - | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 3 | 4 | 0.463 |
| 3044 | 健鼎 | 建議買進 | +2.24% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 8043 | 蜜望實 | 建議買進 | +1.85% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 2415 | 錩新 | 建議買進 | +1.49% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 2308 | 台達電 | 建議買進 | +0.53% | - | weak | distribution_pressure | AVOID | 4 | 5 | 5 | 0.062 |
| 6449 | 鈺邦 | 建議買進 | +0.46% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 2327 | 國巨* | 建議買進 | +0.12% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 2 | 4 | 0.613 |
| 6451 | 訊芯-KY | 建議買進 | +0.11% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6204 | 艾華 | 建議買進 | +2.18% | 0.349 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 3114 | 好德 | 建議買進 | +1.36% | 0.072 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6548 | 長科* | 建議買進 | +2.41% | -0.009 | neutral | short_term_fade | WATCH | 5 | 4 | 2 | 0.388 |
| 8121 | 越峰 | 建議買進 | +0.09% | -0.016 | neutral | mixed_or_unconfirmed | WATCH | 3 | 5 | 2 | 0.425 |
| 8088 | 品安 | 建議買進 | +1.17% | -0.026 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 3 | 0.412 |
| 4971 | IET-KY | 建議買進 | +0.61% | -0.090 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 2428 | 興勤 | 建議買進 | +4.88% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 5289 | 宜鼎 | 建議買進 | +2.21% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 4 | 3 | 0.287 |
| 5228 | 鈺鎧 | 建議買進 | +1.80% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 8299 | 群聯 | 建議買進 | +1.69% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 2 | 4 | 0.487 |
| 5410 | 國眾 | 建議買進 | +0.60% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 5484 | 慧友 | 建議買進 | +0.16% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 3529 | 力旺 | 建議買進 | +0.08% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 2399 | 映泰 | 建議買進 | +0.03% | - | neutral | short_term_fade | WATCH | 5 | 4 | 2 | 0.388 |
| 6727 | 亞泰金屬 | 建議買進 | +0.03% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
