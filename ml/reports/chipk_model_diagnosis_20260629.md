# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-26`
- chipk_asof_date: `2026-06-29`
- model_buy_count: `22`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `11`
- watch: `8`
- conflict/do-not-chase: `3`
- top22 support_or_strong_rate: `54.5%`
- top22 weak_rate: `13.6%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 4939 | 亞電 | 建議買進 | +1.31% | 0.695 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 8121 | 越峰 | 建議買進 | +0.84% | 0.547 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 3093 | 港建* | 建議買進 | +3.35% | 0.510 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6903 | 巨漢 | 建議買進 | +1.74% | 0.204 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 8996 | 高力 | 建議買進 | +0.29% | -0.160 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 2 | 0.662 |
| 2059 | 川湖 | 建議買進 | +3.53% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6139 | 亞翔 | 建議買進 | +2.75% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 2472 | 立隆電 | 建議買進 | +2.64% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 3141 | 晶宏 | 建議買進 | +0.63% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 2428 | 興勤 | 建議買進 | +0.47% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 2476 | 鉅祥 | 建議買進 | +0.42% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6265 | 方土昶 | 建議買進 | +4.32% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 2308 | 台達電 | 建議買進 | +3.53% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 5 | 0.125 |
| 5289 | 宜鼎 | 建議買進 | +2.00% | - | weak | distribution_pressure | AVOID | 4 | 5 | 4 | 0.163 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 4971 | IET-KY | 建議買進 | +1.20% | 0.310 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6426 | 統新 | 建議買進 | +1.43% | 0.274 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 5386 | 青雲 | 建議買進 | +6.45% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6449 | 鈺邦 | 建議買進 | +3.66% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 2 | 4 | 0.487 |
| 4741 | 泓瀚 | 建議買進 | +0.62% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 3 | 0.412 |
| 8046 | 南電 | 建議買進 | +0.59% | - | strong | constructive_pullback | WAIT_FOR_TURN | 4 | 1 | 2 | 0.713 |
| 3081 | 聯亞 | 建議買進 | +0.17% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6727 | 亞泰金屬 | 建議買進 | +0.16% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
