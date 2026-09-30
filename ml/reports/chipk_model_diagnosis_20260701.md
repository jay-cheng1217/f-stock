# ChipK Model Diagnosis

## Scope
- prediction_date: `2026-07-02`
- chipk_asof_date: `2026-07-01`
- model_buy_count: `71`
- status: diagnostic only, not historical backtest

## Summary
- confirmed: `27`
- watch: `30`
- conflict/do-not-chase: `14`
- top30 support_or_strong_rate: `46.7%`
- top30 weak_rate: `6.7%`

## Confirmed
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 4952 | 凌通 | 建議買進 | +2.07% | 0.789 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 8150 | 南茂 | 建議買進 | +1.71% | 0.772 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 6488 | 環球晶 | 建議買進 | +8.08% | 0.609 | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 5328 | 華容 | 建議買進 | +3.43% | 0.446 | strong | sustained_accumulation | CONFIRM_ENTRY | 2 | 2 | 2 | 0.750 |
| 6174 | 安碁 | 建議買進 | +0.97% | 0.381 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 6538 | 倉和 | 建議買進 | +3.58% | 0.278 | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 2383 | 台光電 | 建議買進 | +4.25% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 8121 | 越峰 | 建議買進 | +3.82% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 4 | 3 | 2 | 0.537 |
| 2476 | 鉅祥 | 建議買進 | +1.13% | - | supportive | constructive_support | SUPPORTIVE_ENTRY | 3 | 2 | 2 | 0.688 |
| 6166 | 凌華 | 建議買進 | +1.07% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 8016 | 矽創 | 建議買進 | +0.94% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 6156 | 松上 | 建議買進 | +0.84% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 4526 | 東台 | 建議買進 | +0.83% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 3264 | 欣銓 | 建議買進 | +0.77% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 2 | 2 | 0.812 |
| 6510 | 精測 | 建議買進 | +0.71% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 6173 | 信昌電 | 建議買進 | +0.61% | - | strong | sustained_accumulation | CONFIRM_ENTRY | 1 | 1 | 2 | 0.900 |
| 8932 | 智通* | 建議買進 | +0.58% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 4 | 2 | 0.512 |
| 6877 | 鏵友益 | 建議買進 | +0.50% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |
| 6789 | 采鈺 | 建議買進 | +0.46% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 3 | 3 | 2 | 0.600 |
| 3443 | 創意 | 建議買進 | +0.40% | - | supportive | mixed_or_unconfirmed | SUPPORTIVE_ENTRY | 2 | 3 | 3 | 0.562 |

## Conflict / Do Not Chase
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 6548 | 長科* | 建議買進 | +9.38% | 0.551 | neutral | short_term_chase_trap | DO_NOT_CHASE | 2 | 4 | 4 | 0.375 |
| 3265 | 台星科 | 建議買進 | +5.56% | 0.281 | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 4 | 2 | 0.575 |
| 3374 | 精材 | 建議買進 | +4.30% | 0.158 | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 4 | 2 | 0.575 |
| 8042 | 金山電 | 建議買進 | +2.62% | 0.095 | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 2 | 4 | 0.550 |
| 4989 | 榮科 | 建議買進 | +10.58% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 2 | 4 | 0.550 |
| 5309 | 系統電 | 建議買進 | +5.56% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 8043 | 蜜望實 | 建議買進 | +2.98% | - | supportive | mixed_or_unconfirmed | DO_NOT_CHASE | 1 | 2 | 4 | 0.613 |
| 3324 | 雙鴻 | 建議買進 | +2.61% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 5289 | 宜鼎 | 建議買進 | +1.80% | - | weak | mixed_or_unconfirmed | AVOID | 3 | 5 | 4 | 0.225 |
| 5475 | 德宏 | 建議買進 | +1.68% | - | weak | distribution_pressure | AVOID | 4 | 5 | 4 | 0.163 |
| 8299 | 群聯 | 建議買進 | +1.63% | - | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |
| 2441 | 超豐 | 建議買進 | +1.32% | - | neutral | short_term_chase_trap | DO_NOT_CHASE | 2 | 5 | 4 | 0.287 |
| 8289 | 泰藝 | 建議買進 | +0.93% | - | neutral | mixed_or_unconfirmed | DO_NOT_CHASE | 2 | 3 | 4 | 0.463 |
| 1708 | 東鹼 | 建議買進 | +0.33% | - | weak | distribution_pressure | AVOID | 5 | 4 | 4 | 0.188 |

## Watch
| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |
|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|
| 5299 | 杰力 | 建議買進 | +4.09% | 1.025 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6829 | 千附精密 | 建議買進 | +1.58% | 0.667 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 6175 | 立敦 | 建議買進 | +0.61% | 0.604 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 3081 | 聯亞 | 建議買進 | +2.99% | 0.432 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 6405 | 悅城 | 建議買進 | +6.05% | 0.382 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 3317 | 尼克森 | 建議買進 | +2.95% | 0.370 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 4966 | 譜瑞-KY | 建議買進 | +0.98% | 0.315 | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 4 | 0.400 |
| 3581 | 博磊 | 建議買進 | +5.15% | 0.234 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 4741 | 泓瀚 | 建議買進 | +2.71% | 0.129 | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 3 | 0.412 |
| 2369 | 菱生 | 建議買進 | +1.79% | 0.104 | neutral | short_term_fade | WATCH | 5 | 4 | 2 | 0.388 |
| 2426 | 鼎元 | 建議買進 | +6.27% | - | neutral | short_term_fade | WATCH | 4 | 5 | 2 | 0.362 |
| 3114 | 好德 | 建議買進 | +4.29% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 5228 | 鈺鎧 | 建議買進 | +2.04% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 3 | 3 | 0.500 |
| 6584 | 南俊國際 | 建議買進 | +1.91% | - | strong | constructive_pullback | WAIT_FOR_TURN | 4 | 1 | 2 | 0.713 |
| 4939 | 亞電 | 建議買進 | +1.82% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 4 | 3 | 0.350 |
| 3691 | 碩禾 | 建議買進 | +1.60% | - | neutral | mixed_or_unconfirmed | WATCH | 4 | 3 | 4 | 0.338 |
| 2425 | 承啟 | 建議買進 | +1.58% | - | supportive | constructive_pullback | WAIT_FOR_TURN | 4 | 2 | 2 | 0.625 |
| 3556 | 禾瑞亞 | 建議買進 | +1.52% | - | neutral | mixed_or_unconfirmed | WATCH | 5 | 3 | 4 | 0.275 |
| 2454 | 聯發科 | 建議買進 | +1.29% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |
| 6265 | 方土昶 | 建議買進 | +1.04% | - | neutral | mixed_or_unconfirmed | WATCH | 3 | 4 | 4 | 0.312 |

## Data Boundary
- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.
- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.
- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.
