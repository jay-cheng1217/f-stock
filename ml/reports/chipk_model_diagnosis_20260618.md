# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-06-17`
- chipk_asof_date: `2026-06-18`
- model_buy_count: `57`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `21`
- watch: `18`
- conflict/do-not-chase: `18`
- top30 support_or_strong_rate: `46.7%`
- top30 weak_rate: `13.3%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6465 | 威潤 | 建議買進 | +1.73% | 0.895 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 4534 | 慶騰 | 建議買進 | +1.80% | 0.796 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6204 | 艾華 | 建議買進 | +2.83% | 0.449 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 4739 | 康普 | 建議買進 | +0.99% | 0.134 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 6265 | 方土昶 | 建議買進 | +1.14% | -0.026 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 3105 | 穩懋 | 建議買進 | +1.09% | -0.100 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 4931 | 新盛力 | 建議買進 | +2.45% | -0.230 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 5475 | 德宏 | 建議買進 | +7.76% | - | supportive | near_term_accumulation | SUPPORTIVE_ENTRY | 2 | 2 | 3 | 0.650 |
| 3037 | 欣興 | 建議買進 | +5.50% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |
| 2383 | 台光電 | 建議買進 | +3.13% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 6213 | 聯茂 | 建議買進 | +3.00% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 2428 | 興勤 | 建議買進 | +2.70% | - | supportive | near_term_accumulation | SUPPORTIVE_ENTRY | 2 | 2 | 3 | 0.650 |
| 8121 | 越峰 | 建議買進 | +2.68% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |
| 3044 | 健鼎 | 建議買進 | +1.16% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 6670 | 復盛應用 | 建議買進 | +1.12% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 2 | 1 | 2 | 0.838 |
| 6446 | 藥華藥 | 建議買進 | +1.02% | - | strong | mixed_or_unconfirmed | CONFIRM_ENTRY | 1 | 3 | 2 | 0.725 |
| 2368 | 金像電 | 建議買進 | +0.93% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 4939 | 亞電 | 建議買進 | +0.72% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 6166 | 凌華 | 建議買進 | +0.52% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |
| 3023 | 信邦 | 建議買進 | +0.28% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6175 | 立敦 | 建議買進 | +5.59% | 0.695 | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 2 | 4 | 0.613 |
| 5328 | 華容 | 建議買進 | +1.35% | 0.528 | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 8046 | 南電 | 建議買進 | +5.27% | 0.416 | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 8299 | 群聯 | 建議買進 | +0.79% | 0.294 | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 3537 | 堡達 | 建議買進 | +1.93% | 0.072 | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 4 | 3 | 0.537 |
| 3042 | 晶技 | 建議買進 | +3.28% | -0.220 | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 2472 | 立隆電 | 建議買進 | +3.52% | - | neutral | short_term_chase_trap | DO_NOT_CHASE | 2 | 4 | 4 | 0.375 |
| 4973 | 廣穎 | 建議買進 | +2.69% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 6672 | 騰輝電子-KY | 建議買進 | +2.23% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 2 | 4 | 0.550 |
| 6197 | 佳必琪 | 建議買進 | +2.17% | - | neutral | short_term_chase_trap | DO_NOT_CHASE | 1 | 4 | 4 | 0.438 |
| 2308 | 台達電 | 建議買進 | +1.84% | - | weak | distribution_pressure | AVOID | 5 | 5 | 4 | 0.100 |
| 2476 | 鉅祥 | 建議買進 | +1.69% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 4 | 2 | 0.575 |
| 6274 | 台燿 | 建議買進 | +1.35% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 2 | 4 | 0.550 |
| 3374 | 精材 | 建議買進 | +0.96% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 2 | 4 | 0.613 |
| 3088 | 艾訊 | 建議買進 | +0.84% | - | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 5 | 3 | 0.388 |
| 6290 | 良維 | 建議買進 | +0.43% | - | weak | distribution_pressure | AVOID | 4 | 5 | 4 | 0.163 |
| 4760 | 勤凱 | 建議買進 | +0.28% | - | weak | distribution_pressure | AVOID | 4 | 4 | 4 | 0.250 |
| 5468 | 凱鈺 | 建議買進 | +0.03% | - | weak | distribution_pressure | AVOID | 5 | 5 | 5 | 0.000 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6683 | 雍智科技 | 建議買進 | +1.98% | 0.669 | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 5228 | 鈺鎧 | 建議買進 | +9.08% | 0.528 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 4908 | 前鼎 | 建議買進 | +0.67% | 0.441 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6426 | 統新 | 建議買進 | +1.72% | 0.317 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 3406 | 玉晶光 | 建議買進 | +0.68% | 0.051 | neutral | mixed_or_unconfirmed | WATCH | 5 | 3 | 2 | 0.475 |
| 5386 | 青雲 | 強力買進 | +12.29% | -0.143 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6435 | 大中 | 建議買進 | +2.34% | -0.212 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 5289 | 宜鼎 | 建議買進 | +5.00% | -0.251 | neutral | mixed_or_unconfirmed | WATCH | 5 | 3 | 3 | 0.375 |
| 6727 | 亞泰金屬 | 建議買進 | +5.25% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 3675 | 德微 | 建議買進 | +3.77% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 2327 | 國巨* | 建議買進 | +2.87% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 3556 | 禾瑞亞 | 建議買進 | +1.37% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 3290 | 東浦 | 建議買進 | +0.96% | - | neutral | short_term_fade | WATCH | 4 | 4 | 2 | 0.450 |
| 3443 | 創意 | 建議買進 | +0.63% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6139 | 亞翔 | 建議買進 | +0.62% | - | supportive | constructive_pullback | WAIT_FOR_TURN | 5 | 2 | 2 | 0.562 |
| 3141 | 晶宏 | 建議買進 | +0.44% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 8040 | 九暘 | 建議買進 | +0.40% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 4971 | IET-KY | 建議買進 | +0.07% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
