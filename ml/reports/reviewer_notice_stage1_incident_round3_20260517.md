# Reviewer Notice — Stage1 Incident Round 3

Option B failed. The regenerated Stage1 + retrained Stage2 path produced materially different Champion behavior and cannot restore the v1.2 baseline.

Key evidence:

- INC-Stage1-001: regenerated Stage1 + retrained Stage2 had Jaccard 0.429 vs current production.
- INC-Stage1-002: full Champion v3 rebuild had Jaccard 0.132 vs the current incident pin.
- v3 CL3 failed: monthly alpha -1.31pp, MDD -2.50pp, Sharpe / Calmar both below gate.
- v3 Stage1 holdout median was -0.0264, far below the 0.055 threshold.

PM/RD have decided to stop trying to restore the pre-cleanup Champion behavior. The current production pin is now treated as **Champion v2.6 incident edition**.

Operational consequences:

- All v1.2 freeze attribution / selection RCA numbers remain **pre-cleanup forensic baseline only**.
- The old v1.2 selection -11pp number is retired as an active decision baseline.
- #6 Selection RCA and #2 Stage B remain paused until the post-incident v2.6 / v1.3 baseline has enough accepted data.
- #2 Stage A oracle diagnostic may continue because it does not depend on the model pin.
- REQ-034 daily risk-cut report continues and marks the incident model era.

Next work item:

**INC-Stage1-003 — Stage1 Alpha Decay Investigation**

This is research-only. It will investigate whether Stage1 decay is caused by training-window sensitivity, feature drift, regime mismatch, target distribution shift, or pipeline integrity problems. It will not change production. Any remediation path must become a separate CL3 ticket.

Please do not continue #6 RCA prep work on the v1.2 baseline. Plan to redo the RCA on the accepted post-incident baseline once enough data exists.

