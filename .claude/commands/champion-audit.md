---
description: Run the mandatory Champion production deployment audit.
argument-hint: "[--signal-file ml/models/unified_signals_YYYY-MM-DD.csv]"
---

# Champion Audit

Run this before PM signs off on any Champion production deployment that changes
model version, entry filter, exit policy, or feature-processing behavior.

```bash
python scripts/champion_audit.py $ARGUMENTS
```

Rules:

- PASS is required before deployment sign-off.
- Any failed check blocks PM acceptance until the failing item is fixed or PM
  explicitly records an exception.
- C1 is informational after REQ-016/018/019 CL3 closure: below-MA5 entries are
  expected to be handled by asymmetric_v2 MA5_BREAK and do not block sign-off.
- The script writes:
  - `ml/reports/champion_audit_YYYYMMDD.md`
  - `ml/reports/champion_audit_YYYYMMDD.json`

The first version checks:

- C1 MA5 entry quality observation (below-MA5 counts; INFO only)
- C2 Two-Stage rank completeness
- C3 sector-conditioned OVERHEAT gate
- C4 guardrail columns
- C5 ETF leakage
- C6 production flags
- C7 pinned Stage1/Stage2 model metadata
- C8 test suite
- C9 Champion DB schema
- C10 supply-chain/theme group concentration cap
