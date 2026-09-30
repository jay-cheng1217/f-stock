# Strategy Profit Search Validation

## Verdict
- Overall validation: `PASS`
- Strategy: `chip_momentum_high_top5_liquid_open_ge_10_volume_ge_250k_amount_ge_20m_all`
- Recomputed full cumulative net return: `+320.50%`
- Recomputed train cumulative net return: `+129.05%`
- Recomputed holdout cumulative net return: `+83.59%`
- Recomputed full cumulative net excess return: `+33.06%`
- Recomputed max drawdown: `-4.97%`

## Checks
- Independent picks match committed best picks: `PASS`
- Monthly arithmetic matches committed monthly report: `PASS`
- Summary compounding matches committed summary JSON: `PASS`
- Trade target formula Close[t+20] / Open[t+1] - 1 matches selected rows: `PASS`
- Liquidity/filter constraints pass: `PASS`

## Inputs
- Dataset: `F:\stock\ml\models\dataset_cache_20260614.parquet`
- Summary JSON: `F:\stock\ml\reports\strategy_profit_search_20260618_existing_chip_proxy.json`
- Monthly CSV: `F:\stock\ml\reports\strategy_profit_search_20260618_existing_chip_proxy_best_monthly.csv`
- Picks CSV: `F:\stock\ml\reports\strategy_profit_search_20260618_existing_chip_proxy_best_picks.csv`
