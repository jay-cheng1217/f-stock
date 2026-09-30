# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-26`
- chipk_asof_date: `2026-06-26`
- model_buy_count: `22`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `8`
- watch: `10`
- conflict/do-not-chase: `4`
- top22 support_or_strong_rate: `40.9%`
- top22 weak_rate: `9.1%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 4939 | 亞電 | 建議買進 | +1.31% | 0.695 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 3093 | 港建* | 建議買進 | +3.35% | 0.510 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 4971 | IET-KY | 建議買進 | +1.20% | 0.310 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 8996 | 高力 | 建議買進 | +0.29% | -0.160 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 2059 | 川湖 | 建議買進 | +3.53% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6139 | 亞翔 | 建議買進 | +2.75% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 8046 | 南電 | 建議買進 | +0.59% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 2476 | 鉅祥 | 建議買進 | +0.42% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 8121 | 越峰 | 建議買進 | +0.84% | 0.547 | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 5 | 2 | 0.487 |
| 6449 | 鈺邦 | 建議買進 | +3.66% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 3 | 4 | 0.525 |
| 2308 | 台達電 | 建議買進 | +3.53% | - | weak | distribution_pressure | AVOID | 5 | 5 | 5 | 0.000 |
| 5289 | 宜鼎 | 建議買進 | +2.00% | - | weak | distribution_pressure | AVOID | 5 | 5 | 4 | 0.100 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6426 | 統新 | 建議買進 | +1.43% | 0.274 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 3 | 0.438 |
| 6903 | 巨漢 | 建議買進 | +1.74% | 0.204 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 5386 | 青雲 | 建議買進 | +6.45% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6265 | 方土昶 | 建議買進 | +4.32% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 2472 | 立隆電 | 建議買進 | +2.64% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 2 | 4 | 0.487 |
| 3141 | 晶宏 | 建議買進 | +0.63% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 4741 | 泓瀚 | 建議買進 | +0.62% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 5 | 3 | 0.263 |
| 2428 | 興勤 | 建議買進 | +0.47% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 3081 | 聯亞 | 建議買進 | +0.17% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6727 | 亞泰金屬 | 建議買進 | +0.16% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
