# SA Decision: Stage B Liquidity Path Before Shadow Review (2026-05-18)

## Decision

Recommend **Path A: collect missing liquidity observations before shadow
review**.

Stage B offline evaluation remains approved, but shadow review should stay held
until:

1. The selected proxy passes PM condition 1:
   `(avoided_bad - missed_good)` bootstrap 90% lower bound > 0.
2. Liquidity feature gaps are reduced under the timebox below and the offline
   report is rerun.
3. Champion baseline is accepted.

## Why Not Path B Now

Path B would accept the current partial liquidity proxy with a limitation label.
That is too thin for shadow review because the selected `hybrid_rule_proxy`
fails PM condition 1:

| metric | value |
|---|---:|
| avoided_bad - missed_good | +0.51pp |
| bootstrap 90% lower bound | -9.44pp |
| bootstrap 90% upper bound | +10.68pp |

The signal still ranks realized slippage well, but its business-action side
effect is not statistically confirmed. The safest next move is to improve the
ex-ante liquidity basis rather than promote a partial proxy.

## Path A Timebox

Timebox: **2 RD days for data availability mapping + mock feature backfill**.

Required output:

- `ml/reports/rd002_stage_b_liquidity_path_20260518.md`
- `ml/reports/rd002_stage_b_liquidity_path_20260518.json`
- updated offline evaluation rerun if at least ADV or limit-state features can
  be backfilled safely

## Field Plan

| field | path | expected source | production impact |
|---|---|---|---|
| ADV / 20D average traded value | backfill first | existing daily K OHLCV if complete | none |
| spread proxy | observe or defer | order timestamp + best bid/ask if source exists; otherwise unavailable | none |
| intraday volume curve | observe or defer | intraday bars or first-hour volume if source exists | none |
| limit-up/down state | backfill first | daily OHLCV plus exchange limit rules / near-limit proxy | none |

If ADV and limit-state can be derived from existing data, RD may add them to
offline evaluation only. Spread and intraday volume require source confirmation
before implementation.

## Boundaries

- No production scheduler, order gate, model, DB schema, or Champion attribution
  path changes.
- No change to the +0.50pp Stage B production kill switch.
- NAV/B4/RD-006 reporting labels remain forbidden as Stage B target/features.
- Post-entry realized returns remain forbidden as proxy features.
- 6419 remains an execution-slippage case study only.

## PM Ask

Please sign Path A for RD data availability mapping and mock feature backfill.
Path B is not recommended until the selected proxy's avoided-vs-missed bootstrap
lower bound is positive or PM explicitly accepts an advisory-only partial proxy.
