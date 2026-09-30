# SA Design: Stage B Ex-Ante Slippage Proxy v2 (2026-05-17)

## Decision Status

- PM dispatch accepted. No need to return this ticket to PM for rewrite.
- Stage B design may proceed offline.
- Shadow or production promotion is blocked until the post-incident Champion baseline is accepted.
- Do not change the Stage B kill switch at +0.50pp.
- This revision is SA's PM-ready design response after RD-006 closure.
- RD may start implementation only after PM signs this design.

## Background

RD-002 Stage A found:

| Item | Value |
|---|---:|
| Trades with slippage data | 135 |
| Slippage-only oracle upper bound | +15.97pp |
| Price-improvement offset | +2.89pp |
| Net realized slippage drag | -13.08pp |
| Stage B kill switch | +0.50pp |

The oracle is diagnostic only. Stage B must use ex-ante information.

RD-006 closed the NAV attribution residual line with a separate conclusion:
`NAV unexplained` / B4 residuals are reporting-definition artifacts caused by
target/proxy basis mismatch in attribution reporting. They are not valid Stage B
targets and must not be used to train, rank, or validate a slippage proxy.

Stage B stays in the execution layer: it predicts realized entry slippage before
an order is sent. RD-006 labels may be cited only as a boundary note that keeps
attribution artifacts out of this design.

## SA Decisions

### 0. Cross-Ticket Case Study: 6419

RD-005 A3 identified 6419 as a concrete execution-slippage case:

| Item | Value |
|---|---:|
| Order type | INTRADAY_CHASE |
| Entry slippage | +9.85% |
| Share of RD-005 formal holding-level residual | 56.29% |
| Position marked close return since entry | +29.24% |
| RD-006 boundary | Execution-layer case; not NAV/B4 attribution evidence |

SA classification:

- 6419 is an execution-slippage case study for Stage B proxy design.
- It must not be treated as evidence of structural selection alpha or as a production allocation signal.
- Offline Stage B reporting must show whether the proxy would have flagged 6419 before entry using only ex-ante fields.
- The case study should be grouped with RD-002 Stage A evidence: `INTRADAY_CHASE` had 54 observations and +14.29% cost in the oracle analysis.

### 0.1 RD-006 Boundary: Do Not Use NAV/B4 as Stage B Target

SA locks the target basis:

- Target label: realized entry slippage from RD-002 Stage A's trade/slippage frame.
- Positive target value means worse execution cost; price improvement is reported separately.
- Allowed inputs: order/replay fields and market features observable before order release.
- Forbidden as model/evaluation targets: `NAV unexplained`, B1/B2/B3/B4 attribution buckets, `b4_dominance_flag`, and `cross_window_sign_stability`.
- Forbidden as proxy features: any post-entry realized return, NAV attribution residual, or report-only RD-006 label.
- RD-006 may appear only in the report narrative to explain why attribution residuals are out of scope.

This prevents a reporting-definition artifact from being mistaken for an
execution-control signal.

### 1. Technical Route

Use a hybrid, rule-first proxy.

Rejected:

- Pure ML proxy: sample is too small and the Champion baseline is in incident-edition state.
- Pure hard rule: likely too brittle across market regimes and order types.

Selected:

- Build interpretable rule-derived proxy scores first.
- Evaluate whether a simple calibrated model improves ranking after enough samples exist.
- Keep the first production candidate explainable and monotonic.

### 2. Candidate Proxy Set

Minimum candidate set:

| Candidate | Description | Data Availability |
|---|---|---|
| Chase intensity | Intended entry price or open price vs prior close / MA5 / recent close, plus `order_type` | Available from order/replay and daily K |
| Liquidity impact | Order notional vs 20D average traded value, volume percentile, high-low range proxy | Mostly available from daily K and order size |
| Volatility / exhaustion | ATR%, 5D return, 20D return, close vs MA20/MA60, gap behavior | Available from daily K |
| Open stress | Open gap, limit-up/near-limit behavior, first available market print if present | Partial; use only if data exists |

RD may add candidates, but must keep at least these families comparable.
Any added candidate must be computable before order release and must not depend
on realized post-entry returns or NAV attribution outputs.

### 3. Evaluation Metrics

Use a ranking-first evaluation, not only MAE.

Required target:

- `realized_entry_slippage`: signed execution slippage from the Stage A input frame.
- `price_improvement_offset`: reported as an offset, not merged into the proxy score.
- `net_realized_slippage_drag`: reported in the Stage A summary shape for PM comparison.

Required metrics:

1. Spearman correlation between proxy and realized slippage.
2. Tail capture: share of realized slippage cost captured by top proxy quintile.
3. Avoided-bad-chase and missed-good-chase, reported separately.
4. Net skip oracle at candidate thresholds, using Stage A's reporting shape.
5. Calibration error for signed slippage as secondary evidence only.

Group metrics by:

- `order_type`.
- Date.
- Ticker.
- Liquidity bucket.

Required case-study appendix:

- 6419 must be reported as a named appendix row.
- The appendix must label it as execution slippage, not selection alpha.
- The appendix must list only ex-ante feature values available before the 2026-05-12 entry.
- The appendix must not recommend production allocation or selection changes.
- The appendix must explicitly state that 6419's NAV attribution context is not
  used as a training label or proxy feature.

### 4. Adoption Conditions

Offline design may use the current 135 trades.

Shadow candidate requires all of:

1. At least 100 trades with realized slippage and complete proxy features.
2. Top proxy quintile captures at least 40% of total positive slippage cost.
3. Spearman correlation is positive and bootstrap 90% lower bound is above 0.
4. Selected threshold has net skip oracle benefit above +0.50pp.
5. Avoided-bad-chase and missed-good-chase are both reported, with avoided-bad-chase larger than missed-good-chase.

Production candidate requires all shadow conditions plus:

1. At least 40 trading days or 100 new post-design trades in shadow.
2. Benefit remains positive in two contiguous subwindows.
3. No production baseline incident is active.
4. PM approval after SA review.

### 5. Baseline Dependency

Feature design and offline proxy evaluation do not require Stage1 binary stability.

Promotion to shadow/production does require an accepted Champion baseline because the business impact must be measured against a stable selection baseline.

If the baseline remains unstable for six months:

- Stage B may remain advisory-only.
- It may produce execution-risk annotations or suggested size haircuts.
- It must not hard-kill orders without a new PM-approved ticket.

### 6. Failure Path

Do not permanently archive Stage B after one failed proxy round.

If current candidates fail, first request expanded observation fields:

- Order timestamp.
- Planned price.
- Actual fill price.
- Best bid/ask or spread proxy.
- Intraday volume curve.
- Limit-up/limit-down state.

Permanent archival is allowed only after expanded data still fails the shadow criteria across a bounded observation window.

## RD-Ready Work Breakdown

RD is not cleared for production code.

When PM approves this design, RD should build offline evaluation only:

- `scripts/stage_b_slippage_proxy_design.py`
- `ml/reports/rd002_stage_b_proxy_design_20260517.md`
- `ml/reports/rd002_stage_b_proxy_design_20260517.json`
- Candidate threshold CSVs as needed.

Allowed implementation surface:

- New offline script/report artifacts listed above.
- Reads from the Stage A trade/slippage frame and existing market data snapshots.
- No production scheduler, order gate, model, database schema, or Champion
  attribution path changes.

RD task list:

1. Reuse Stage A trade/slippage input frame.
2. Add ex-ante candidate features only.
3. Evaluate all candidates with the required metrics.
4. Report avoided-bad-chase and missed-good-chase separately.
5. Recommend one proxy for shadow or recommend data expansion.
6. Do not change production thresholds or order behavior.
7. Include a 6419 appendix row with ex-ante features and the proxy verdict.
8. Include an RD-006 boundary note confirming NAV/B4 artifacts were not used.

## PM Sign-Off Checklist

This SA design answers the requested decisions:

| PM Question | SA Decision |
|---|---|
| Route | Hybrid rule-first proxy |
| Candidate set | Chase intensity, liquidity impact, volatility/exhaustion, open stress |
| Evaluation | Ranking-first metrics plus net skip oracle and 6419 appendix |
| Adoption | Offline -> shadow -> production gates, with baseline dependency locked |
| Baseline dependency | Offline can proceed now; shadow/production waits for accepted Champion baseline |
| Failure path | Request expanded observation fields before permanent archival |
| 6419 handling | Execution-slippage case study only; no allocation/selection action |
| RD-006 handling | NAV/B4 attribution artifacts excluded from Stage B target/features |

PM sign-off on this document authorizes RD offline evaluation only.
