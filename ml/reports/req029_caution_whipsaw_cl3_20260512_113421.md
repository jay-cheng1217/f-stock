# REQ-029 CAUTION Whipsaw Exit Overlay CL3

Generated: 2026-05-12T11:34:21.257928+00:00
Window: 2024-01 ~ 2026-04
Control re-entry rate (≤3d): 0.9%

## Summary by Variant

| Variant | Alpha | MDD | Sharpe | Calmar | N | Hold_days | WinRate | MA5_breaks |
|---------|------:|----:|-------:|-------:|--:|----------:|--------:|-----------:|
| Control_AsymmetricV2 | 1.57% | -7.69% | 2.121 | 4.068 | 840 | 4.9 | 44.88% | 704 (83.81%) |
| Variant_A_min_hold_3d | 1.29% | -7.69% | 2.153 | 4.179 | 840 | 5.4 | 45.24% | 633 (75.36%) |
| Variant_B_ma5_confirm_2d | 1.34% | -7.69% | 2.083 | 4.219 | 840 | 5.5 | 44.17% | 624 (74.29%) |
| Variant_C_reentry_lock_3d | 1.51% | -7.69% | 2.093 | 3.927 | 836 | 4.9 | 44.86% | 701 (83.85%) |

## CL3 Results

### Variant_A_min_hold_3d — ✅ PASS

| Check | Value | Threshold | Result |
|-------|------:|----------:|--------|
| alpha_not_regress_0.5pp | -0.28% | -0.50% | PASS |
| mdd_not_worse | 0.00% | -0.10% | PASS |
| sharpe_not_regress_5pct | 215.32% | ≥95% of 212.06% | PASS |
| calmar_not_regress_5pct | 417.95% | ≥95% of 406.82% | PASS |

### Variant_B_ma5_confirm_2d — ✅ PASS

| Check | Value | Threshold | Result |
|-------|------:|----------:|--------|
| alpha_not_regress_0.5pp | -0.23% | -0.50% | PASS |
| mdd_not_worse | -0.00% | -0.10% | PASS |
| sharpe_not_regress_5pct | 208.30% | ≥95% of 212.06% | PASS |
| calmar_not_regress_5pct | 421.93% | ≥95% of 406.82% | PASS |

### Variant_C_reentry_lock_3d — ✅ PASS

| Check | Value | Threshold | Result |
|-------|------:|----------:|--------|
| alpha_not_regress_0.5pp | -0.06% | -0.50% | PASS |
| mdd_not_worse | -0.00% | -0.10% | PASS |
| sharpe_not_regress_5pct | 209.27% | ≥95% of 212.06% | PASS |
| calmar_not_regress_5pct | 392.70% | ≥95% of 406.82% | PASS |

## Per-Regime Breakdown

| Variant | Regime | N | Avg Return | Avg Alpha | Alpha Δ vs Control | Win Rate |
|---------|--------|--:|----------:|----------:|-------------------:|---------:|
| Control_AsymmetricV2 | CAUTION | 240 | 3.13% | 1.31% | 0.00% | 48.33% |
| Control_AsymmetricV2 | OPEN | 360 | 3.08% | 1.75% | 0.00% | 50.00% |
| Control_AsymmetricV2 | CLOSED | 240 | 0.29% | 1.56% | 0.00% | 33.75% |
| Variant_A_min_hold_3d | CAUTION | 240 | 3.32% | 0.33% | -0.98% | 49.58% |
| Variant_A_min_hold_3d | OPEN | 360 | 3.08% | 1.75% | 0.00% | 50.00% |
| Variant_A_min_hold_3d | CLOSED | 240 | 0.29% | 1.56% | 0.00% | 33.75% |
| Variant_B_ma5_confirm_2d | CAUTION | 240 | 3.39% | 0.49% | -0.81% | 45.83% |
| Variant_B_ma5_confirm_2d | OPEN | 360 | 3.08% | 1.75% | 0.00% | 50.00% |
| Variant_B_ma5_confirm_2d | CLOSED | 240 | 0.29% | 1.56% | 0.00% | 33.75% |
| Variant_C_reentry_lock_3d | CAUTION | 240 | 3.13% | 1.31% | 0.00% | 48.33% |
| Variant_C_reentry_lock_3d | OPEN | 356 | 2.90% | 1.64% | -0.11% | 50.00% |
| Variant_C_reentry_lock_3d | CLOSED | 240 | 0.29% | 1.56% | 0.00% | 33.75% |
