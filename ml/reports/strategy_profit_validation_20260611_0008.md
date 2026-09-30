# Strategy Profit Search Validation

## Verdict
- Overall validation: `PASS`
- Strategy: `chip_momentum_high_top5_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all`
- Recomputed full cumulative net return: `+299.81%`
- Recomputed train cumulative net return: `+129.05%`
- Recomputed holdout cumulative net return: `+74.55%`
- Recomputed full cumulative net excess return: `+29.88%`
- Recomputed max drawdown: `-4.97%`

## Checks
- Independent picks match committed best picks: `PASS`
- Monthly arithmetic matches committed monthly report: `PASS`
- Summary compounding matches committed summary JSON: `PASS`
- Trade target formula Close[t+20] / Open[t+1] - 1 matches selected rows: `PASS`
- Liquidity/filter constraints pass: `PASS`

## Inputs
- Dataset: `F:\stock\ml\models\dataset_cache_20260610.parquet`
- Summary JSON: `F:\stock\ml\reports\strategy_profit_search_20260610_2359.json`
- Monthly CSV: `F:\stock\ml\reports\strategy_profit_search_20260610_2359_best_monthly.csv`
- Picks CSV: `F:\stock\ml\reports\strategy_profit_search_20260610_2359_best_picks.csv`
