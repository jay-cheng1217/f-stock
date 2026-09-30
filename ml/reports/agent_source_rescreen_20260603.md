# Agent Source Library Rescreen (20260603)

## Verdict

- Existing Main League remains unchanged: new sources are not auto-promoted.
- Current admitted agents: 9.
- Existing converted but failed/watchlist agents: 20.
- New P0 Candidate Lab sources: 5.
- New P1 design candidates: 9.
- Public context health: ok (17/17 OK).

## Admission Rule

A source can enter Candidate Lab/Watchlist when it can be mapped to deterministic Taiwan-stock rules using point-in-time data. It can enter Main League only after the existing no-lookahead admission gate passes.

## P0 Candidate Lab

| Repo | Candidate Agent | Needed Data | Why |
| --- | --- | --- | --- |
| chrisworsey55/atlas-gic | taifex_macro_regime_beta_overlay | TAIFEX public context + existing daily_k beta/volatility. | Public TAIFEX put/call, institutional positioning, and futures OI now support a market-regime/beta overlay candidate. |
| tejtw/TEJ_API_Python_Efficient_Frontier_ProgramSample | efficient_frontier_allocator | daily_k returns, sector caps, realized covariance. | Current daily_k return covariance is enough for an allocation-style paper agent, as long as unequal weights are explicitly audited. |
| tejtw/TEJ_API_Python_EPS_dividend_check | dividend_eps_quality | EPS, dividend/ex-right events, valuation, adjusted-return policy. | New TWSE/TPEx ex-right context plus local EPS/valuation can support dividend-quality and corporate-action-safe selection. |
| tejtw/TEJ_API_Python_VaRStandard_ProgramSample | var_budget_momentum | daily_k returns, TAIFEX regime flags, realized volatility. | Existing daily returns plus new TAIFEX regime context can support a VaR-aware risk-budget sleeve. |
| tejtw/TEJ_API_Python_EPS | eps_growth_value | EPS, PE/PB/dividend yield, point-in-time availability date. | Local EPS/financial update path exists; can become a point-in-time EPS growth/value stock-selection candidate after coverage audit. |

## P1 Design Candidates

| Repo | Candidate Agent | Needed Data | Why |
| --- | --- | --- | --- |
| rkuo2000/AI-stocks | ai_news_technical_risk_veto | daily_k indicators, official news, leakage-safe feature mapping. | Can use existing technical features and official news as advisory/risk-veto inputs, but LLM/news output must not be the sole trade signal. |
| firmai/financial-machine-learning | afml_triple_barrier_ranker | same-snapshot labels, daily_k, public risk context. | AFML ideas can be implemented with local labels, but need a leakage-controlled label pipeline before contest entry. |
| robertmartin8/MachineLearningStocks | ml_price_feature_ranker | daily_k, public context features, model artifact governance. | Existing daily_k plus public context can train an interpretable ML ranker; requires fixed-snapshot A/B before admission. |
| 0xfdf/toraniko | factor_diagnostics_ranker | factor library mapping, feature IC report, no-lookahead backtest. | Can become a factor-ranking candidate using current technical/fundamental/context features; not a direct imported model. |
| jjakimoto/finance_ml | finance_ml_event_label_ranker | event labels, daily_k, public context, leakage audit. | Event-label and cross-sectional ML ideas are feasible after local label definitions are locked. |
| moyuweiqing/A-stock-prediction-algorithm-based-on-machine-learning | a_share_style_ml_ranker_tw | Taiwan feature mapping, same-snapshot A/B, backtest. | A-share ML pattern can be adapted to Taiwan features, but China-specific fields need replacement. |
| tejtw/TEJ_API_Python_Crossing_price | price_crossing_event_swing | daily_k, volume confirmation, no-lookahead backtest. | Crossing-price logic is feasible with daily_k; likely overlaps existing MA/breakout agents and should start in Watchlist. |
| tejtw/TEJ_API_Python_FinancialdatawithReceivable | receivable_quality_risk | receivable metrics, revenue, income statement, announcement dates. | Can become an accounting-quality risk agent if MOPS point-in-time financial coverage is verified. |
| tejtw/TEJ_API_Python_FinancialdatawithLoan | leverage_quality_risk | balance sheet fields, cash-flow/debt proxies, announcement dates. | Can become a balance-sheet leverage/loan-risk candidate after local financial coverage audit. |

## Existing Main League Sources

| Repo | Admitted Agents | Watchlist Agents |
| --- | --- | --- |
| kevin801221/stock-strategies-only | institutional_flow | foreign_flow_rider, trust_flow_rider, dealer_flow_swing, defensive_flow |
| sacahan/CasualTrader | volume_breakout, quiet_breakout | volume_surge_swing, short_swing_momentum |
| matthewHsieh/Stock | institutional_flow | foreign_flow_rider, trust_flow_rider, dealer_flow_swing |
| edtechre/pybroker | ml_edge_proxy | risk_parity_momentum, long_compounder |
| stefan-jansen/machine-learning-for-trading | ml_edge_proxy, high_beta_momentum | long_compounder |
| AI4Finance-Foundation/FinRL-Trading | ml_edge_proxy, high_beta_momentum |  |
| StockSharp/StockSharp | volume_breakout, quiet_breakout, atr_breakout | volume_surge_swing |
| je-suis-tm/quant-trading | volume_breakout, quiet_breakout | volume_surge_swing |
| fmzquant/strategies | ma_fast_trend, atr_breakout | dual_ma_trend, ma_slow_trend, short_swing_momentum |
| ranaroussi/qtpylib | ma_fast_trend, bb_upper_trend | dual_ma_trend, ma_slow_trend, mean_reversion_rsi, rsi_strength, kd_strength |
| DaveSkender/Stock.Indicators | atr_breakout, bb_upper_trend | macd_acceleration, rsi_strength, kd_strength |
| tejtw/TQuant-Lab | vam_fast_5d | tquant_vam_momentum, vam_slow_20d, gap_safe_momentum |
| tejtw/FactorLibrary-manual | vam_fast_5d | tquant_vam_momentum, vam_slow_20d |

## Existing Watchlist Sources

| Repo | Failed Agents | Why |
| --- | --- | --- |
| gbeced/pyalgotrade | dual_ma_trend, short_swing_momentum | Mapped agent(s) exist but failed backtest: dual_ma_trend, short_swing_momentum. |
| tensortrade-org/tensortrade | mean_reversion_rsi, contrarian_reversal | Mapped agent(s) exist but failed backtest: mean_reversion_rsi, contrarian_reversal. |
| huseinzol05/Stock-Prediction-Models | mean_reversion_rsi, rsi_strength, kd_strength, contrarian_reversal | Mapped agent(s) exist but failed backtest: mean_reversion_rsi, rsi_strength, kd_strength, contrarian_reversal. |
| jankrepl/deepdow | risk_parity_momentum, low_beta_momentum | Mapped agent(s) exist but failed backtest: risk_parity_momentum, low_beta_momentum. |
| dcajasn/Riskfolio-Lib | low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum, defensive_flow | Mapped agent(s) exist but failed backtest: low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum, defensive_flow. |
| skfolio/skfolio | low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum, defensive_flow | Mapped agent(s) exist but failed backtest: low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum, defensive_flow. |
| cvxgrp/cvxportfolio | low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum | Mapped agent(s) exist but failed backtest: low_vol_quality_proxy, risk_parity_momentum, low_beta_momentum. |

## Advisory / Infra Only

| Repo | Family | Decision |
| --- | --- | --- |
| TauricResearch/TradingAgents | LLM trading committee | not a direct trading agent |
| hsliuping/TradingAgents-CN | LLM trading committee | not a direct trading agent |
| HKUDS/Vibe-Trading | LLM trading committee | not a direct trading agent |
| ZhuLinsen/daily_stock_analysis | news intelligence | not a direct trading agent |
| ErikThiart/ai-stock-dashboard | dashboard / technical UI | not a direct trading agent |
| quantopian/zipline | backtesting engine | not a direct trading agent |
| QuantConnect/Lean | backtesting engine | not a direct trading agent |
| scrtlabs/catalyst | backtesting engine | not a direct trading agent |
| Lumiwealth/lumibot | trading bot framework | not a direct trading agent |
| coding-kitties/investing-algorithm-framework | framework | not a direct trading agent |
| grananqvist/Awesome-Quant-Machine-Learning-Trading | awesome list | not a direct trading agent |
| cbailes/awesome-deep-trading | awesome list | not a direct trading agent |
| PacktPublishing/Hands-On-Machine-Learning-for-Algorithmic-Trading | ML trading book | not a direct trading agent |
| PacktPublishing/Machine-Learning-for-Algorithmic-Trading-Second-Edition_Original | ML trading book | not a direct trading agent |
| Ceruleanacg/Personae | agent personas | not a direct trading agent |
| TraderAlice/OpenAlice | agent trading | not a direct trading agent |
| chrisconlan/algorithmic-trading-with-python | technical trading reference | not a direct trading agent |
| nickmccullum/algorithmic-trading-python | technical trading reference | not a direct trading agent |
| JerBouma/AlgorithmicTrading | algorithmic trading reference | not a direct trading agent |
| boyboi86/AFML | financial ML | not a direct trading agent |
| pipiku915/FinMem-LLM-StockTrading | LLM memory trading | not a direct trading agent |
| tejtw/TEJ_TOOL_API | TEJ data API | not a direct trading agent |
| tejtw/TQuant-manual | Taiwan TQuant docs | not a direct trading agent |
| tejtw/zipline-tej | TEJ backtesting engine | not a direct trading agent |
| tejtw/EN-TEJAPI | TEJ data docs | not a direct trading agent |
| tejtw/exchange_calendars | calendar tooling | not a direct trading agent |
| tejtw/pyfolio-tej | performance analytics | not a direct trading agent |
| tejtw/TEJAPI_Python_Medium_Application | TEJ tutorials | not a direct trading agent |
| tejtw/TEJAPI_Python_Medium_Quant | TEJ quant tutorials | not a direct trading agent |
| tejtw/TEJAPI_Python_Medium_DataAnalysis | TEJ data analysis | not a direct trading agent |
| tejtw/WelcomeToTejApi | TEJ onboarding | not a direct trading agent |
| tejtw/TEJAPI_Python_Medium_Rookies | TEJ tutorials | not a direct trading agent |

## Still Blocked

| Repo | Family | Why |
| --- | --- | --- |
| TradeMaster-NTU/TradeMaster | RL trading | Still needs a validated Taiwan RL environment; public context helps state features but not the simulator. |
| BlackArbsCEO/Adv_Fin_ML_Exercises | financial ML exercises | No safe deterministic Taiwan-stock mapping was identified in this automated rescreen. |
| Rachnog/Deep-Trading | deep learning trading | Requires sequence-model runtime and retraining validation before contest conversion. |
| 0xemmkty/QuantMuse | quant research | No safe deterministic Taiwan-stock mapping was identified in this automated rescreen. |
| brokermr810/QuantDinger | quant framework | Needs a Taiwan data adapter and local execution mapping. |
| 51bitquant/bitquant | quant framework | Framework adapter is not present. |
| dzitkowskik/StockPredictionRNN | RNN stock prediction | Needs sequence training pipeline, GPU/runtime validation, and leakage audit. |
| JordiCorbilla/stock-prediction-deep-neural-learning | deep learning stock prediction | Needs local retraining and model governance. |
| llSourcell/Reinforcement_Learning_for_Stock_Prediction | RL stock prediction | Needs a validated Taiwan RL environment. |
| Quantweb3-com/NexusTrader | trading framework | Framework not adapted to the local paper contest. |
| fulifeng/Temporal_Relational_Stock_Ranking | relational stock ranking | Still needs Taiwan relational graph/supply-chain features. |
| sebastianheinz/stockprediction | stock prediction | No local validated retraining path yet. |
| kimber-chen/Tensorflow-for-stock-prediction | TensorFlow stock prediction | Needs TensorFlow training/runtime validation and leakage audit. |
| timestocome/Test-stock-prediction-algorithms | prediction algorithm benchmark | No safe deterministic Taiwan-stock mapping was identified in this automated rescreen. |
| zshicode/Attention-CLX-stock-prediction | attention stock prediction | Needs attention-model training/runtime validation and Taiwan feature mapping. |
| saeed349/Deep-Reinforcement-Learning-in-Trading | deep RL trading | Needs a validated Taiwan RL environment. |
| CFMTech/Deep-RL-for-Portfolio-Optimization | deep RL portfolio | Needs portfolio RL simulator and action-space validation. |
| tejtw/TEJ_API_Python_WarrantTStandard_ProgramSample | TEJ warrant sample | Warrant domain is outside the current stock-only arena. |
| tejtw/TEJ_API_Python_RealEstateTransfer_ProgramSample | real estate data | Real estate data is outside current stock arena scope. |
