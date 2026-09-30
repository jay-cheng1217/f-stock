# Champion Audit — 2026-05-16

- Signal file: `F:\stock\ml\models\unified_signals_2026-05-15.csv`
- Overall: **FAIL**

| Check | Result | Detail |
|---|---|---|
| C1 MA5 進場品質 | ❌ | no usable price_vs_ma5 values |
| C2 Two-Stage rank 完整性 | ❌ | NaN 0/0 = 100.00% <= 5% |
| C3 OVERHEAT gate 正常 | ✅ | 0 active signals exceed sector threshold |
| C4 Guardrail 欄位存在 | ✅ | required columns present |
| C5 ETF 洩漏 | ✅ | ETF hits = 0 |
| C6 Production flags | ✅ | effective flags are enabled |
| C7 Stage1/Stage2 model meta 鎖定 | ✅ | stage1/stage2 meta and model binaries match CL3 pinned versions |
| C8 Tests | ❌ | skipped by --skip-tests |
| C9 DB schema 完整性 | ✅ | required unified_positions columns present |
| C10 Group concentration cap | ✅ | no active signals |

## Failed Checks

- `C1` MA5 進場品質: no usable price_vs_ma5 values
- `C2` Two-Stage rank 完整性: NaN 0/0 = 100.00% <= 5%
- `C8` Tests: skipped by --skip-tests
