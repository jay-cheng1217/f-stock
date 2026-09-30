# RD-006 Minimal Reporting Labels Design (2026-05-17)

## Purpose

This document designs the approved minimal reporting-definition fix for RD-006.

PM verdict on RD-006 diagnostic:

- Root cause: C1 target/proxy basis mismatch.
- Fix scope: `minimal_reporting_definition`.
- Medium reporting refactor: held.
- Ledger schema/write-path change: rejected.

This design is for PM sign-off before RD implementation.

## Non-Goals

- Do not change B1/B2/B3/B4 bucket definitions.
- Do not change existing JSON field names, types, or meanings.
- Do not mutate `paper_portfolio_v2_champion.db`.
- Do not change production model, weights, sector caps, 14 guardrails, schedules, or RD-002 Stage B thresholds.
- Do not auto-retrofit historical artifacts.

## Implementation Surface

RD should add a small labeling helper and wire it into NAV attribution / Phase B style report outputs.

Recommended files:

- `scripts/attribution_reporting_labels.py` (new helper)
- `scripts/rd005_phase_b_timing_split.py` (add labels to generated JSON/MD)
- optionally `scripts/champion_nav_attribution.py` only if PM wants the labels in base Champion NAV attribution artifacts immediately
- `tests/test_rd006_attribution_labels.py` (new tests)

No DB migration is allowed.

## New JSON Fields

Add a new top-level object:

```json
"reporting_definition_labels": {
  "c1_basis_label": "nav_unexplained_target",
  "b4_dominance_flag": true,
  "cross_window_sign_stability": "insufficient_windows",
  "label_version": "rd006_minimal_v1"
}
```

### `c1_basis_label`

Allowed values:

- `nav_unexplained_target`
- `observable_timing_proxy`

Default for RD-005 Phase B / NAV unexplained residual reports:

- `nav_unexplained_target`

Meaning:

- `nav_unexplained_target`: report reconciles to a NAV attribution target bucket.
- `observable_timing_proxy`: report shows only observable B1/B2/B3 timing proxy quantities and does not claim to explain a NAV unexplained target.

### `b4_dominance_flag`

Definition:

```text
abs(B4) > abs(B1 + B2 + B3 signed sum)
```

Strict greater-than is required.

Boundary:

- if `abs(B4) == abs(B1+B2+B3)`, flag is `false`.

Meaning:

- `true`: B4 dominates the observable timing proxy sum; PM/SA must not treat the report as a clean timing signal.
- `false`: B4 does not dominate under this rule; the timing proxy may be read, with normal caveats.

### `cross_window_sign_stability`

Allowed values:

- `same_sign`
- `sign_flip`
- `mixed_or_zero`
- `insufficient_windows`

Rules:

- If fewer than two comparable windows are supplied, set `insufficient_windows`.
- Do not encode insufficient data as `false`.
- If all non-zero B4 values have the same sign, set `same_sign`.
- If at least one positive and at least one negative B4 exist, set `sign_flip`.
- If zeros prevent a clean same-sign or sign-flip classification, set `mixed_or_zero`.

### `label_version`

Use:

- `rd006_minimal_v1`

This allows future report readers to distinguish artifacts created before and after 2026-05-17.

## Historical Artifact Policy

Do not regenerate existing historical reports automatically.

Add documentation or release notes stating:

- artifacts generated before 2026-05-17 may not include `reporting_definition_labels`
- absence of the object means `unknown_pre_rd006_labels`, not `false`

If PM later approves retrofit, regenerate only explicitly approved windows first.

## Markdown Output

Reports that include the new labels should add a short section:

```markdown
## Reporting Definition Labels
- C1 basis label: `nav_unexplained_target`
- B4 dominance flag: `true`
- Cross-window sign stability: `insufficient_windows`
- Label version: `rd006_minimal_v1`
```

No existing report section should be renamed.

## Tests

Add `tests/test_rd006_attribution_labels.py`.

Required cases:

1. `b4_dominance_flag` is `true` when `abs(B4) > abs(B1+B2+B3)`.
2. `b4_dominance_flag` is `false` when exactly equal.
3. `cross_window_sign_stability` returns `insufficient_windows` for zero or one comparable window.
4. `cross_window_sign_stability` returns `sign_flip` for one positive and one negative B4.
5. `cross_window_sign_stability` returns `same_sign` for two positive or two negative non-zero B4 values.
6. `c1_basis_label` defaults to `nav_unexplained_target`.
7. Existing field values are preserved when labels are appended.

## Acceptance Criteria

PM can approve RD implementation if:

- the helper has deterministic behavior
- the new object is additive only
- tests cover PM boundary cases
- generated RD-005 Phase B style report includes the labels
- no DB/schema/production logic changes are included

## RD Dispatch After PM Sign-Off

RD may implement only the minimal label helper and tests.

Expected output after RD implementation:

- updated script(s)
- `tests/test_rd006_attribution_labels.py`
- regenerated current-window artifact only if PM explicitly requests it in the sign-off

Do not start medium refactor or schema work without a new PM verdict.
