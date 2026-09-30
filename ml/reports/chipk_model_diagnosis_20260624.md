# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-24`
- chipk_asof_date: `2026-06-24`
- model_buy_count: `37`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `7`
- watch: `23`
- conflict/do-not-chase: `7`
- top30 support_or_strong_rate: `16.7%`
- top30 weak_rate: `13.3%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 8932 | 智通* | 建議買進 | +0.72% | 0.542 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 1568 | 倉佑 | 建議買進 | +0.17% | 0.436 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 4 | 3 | 2 | 0.537 |
| 8046 | 南電 | 建議買進 | +3.00% | 0.249 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 2059 | 川湖 | 建議買進 | +7.75% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6761 | 穩得 | 建議買進 | +1.14% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 4 | 2 | 3 | 0.525 |
| 5475 | 德宏 | 建議買進 | +0.26% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 8088 | 品安 | 建議買進 | +0.03% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6426 | 統新 | 建議買進 | +0.17% | 0.446 | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 3 | 4 | 0.463 |
| 6265 | 方土昶 | 建議買進 | +5.59% | 0.095 | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 6139 | 亞翔 | 建議買進 | +2.46% | 0.052 | neutral | short_term_chase_trap | DO_NOT_CHASE | 1 | 5 | 4 | 0.350 |
| 8358 | 金居 | 建議買進 | +4.41% | - | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 6449 | 鈺邦 | 建議買進 | +1.85% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 8043 | 蜜望實 | 建議買進 | +1.17% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 4760 | 勤凱 | 建議買進 | +0.44% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 4971 | IET-KY | 建議買進 | +1.01% | 0.413 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6175 | 立敦 | 建議買進 | +0.20% | 0.343 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6204 | 艾華 | 建議買進 | +0.03% | 0.308 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 3114 | 好德 | 建議買進 | +2.27% | 0.250 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6548 | 長科* | 建議買進 | +0.91% | 0.035 | neutral | short_term_fade | WATCH | 5 | 4 | 2 | 0.388 |
| 5228 | 鈺鎧 | 建議買進 | +3.60% | -0.030 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 5386 | 青雲 | 建議買進 | +8.48% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 3675 | 德微 | 建議買進 | +3.24% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 5289 | 宜鼎 | 建議買進 | +2.58% | - | neutral | short_term_fade | WATCH | 5 | 5 | 2 | 0.300 |
| 2428 | 興勤 | 建議買進 | +2.43% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 3406 | 玉晶光 | 建議買進 | +1.95% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 4 | 3 | 0.350 |
| 2327 | 國巨* | 建議買進 | +1.73% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 2 | 4 | 0.425 |
| 6138 | 茂達 | 建議買進 | +1.45% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 2472 | 立隆電 | 建議買進 | +1.37% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 2 | 4 | 0.362 |
| 5410 | 國眾 | 建議買進 | +1.19% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6531 | 愛普* | 建議買進 | +1.06% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 3 | 4 | 0.275 |
| 8299 | 群聯 | 建議買進 | +1.05% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6510 | 精測 | 建議買進 | +0.86% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 6435 | 大中 | 建議買進 | +0.75% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 3044 | 健鼎 | 建議買進 | +0.42% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 4 | 3 | 0.287 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
