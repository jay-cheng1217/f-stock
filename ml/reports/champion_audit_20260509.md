# Champion Audit — 2026-05-09

- Signal file: `F:\stock\ml\models\unified_signals_2026-05-08.csv`
- Overall: **FAIL**

| Check | Result | Detail |
|---|---|---|
| C1 MA5 進場品質觀測 | ℹ️ | INFO only: below-MA5 7/7 = 100.00%; below -3.00% 6/7 = 85.71%; deep <= -5.00% 5/7 = 71.43%; asymmetric_v2 handles below-MA5 entries with MA5_BREAK |
| C2 Two-Stage rank 完整性 | ❌ | NaN 3/7 = 42.86% <= 5% |
| C3 OVERHEAT gate 正常 | ✅ | 0 active signals exceed sector threshold |
| C4 Guardrail 欄位存在 | ✅ | required columns present |
| C5 ETF 洩漏 | ✅ | ETF hits = 0 |
| C6 Production flags | ✅ | effective flags are enabled |
| C7 Stage1/Stage2 model meta 鎖定 | ✅ | stage1/stage2 meta match CL3 pinned versions |
| C8 Tests | ✅ | 131 passed, 247 warnings in 34.17s |
| C9 DB schema 完整性 | ✅ | required unified_positions columns present |

## Failed Checks

- `C2` Two-Stage rank 完整性: NaN 3/7 = 42.86% <= 5%
