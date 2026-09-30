"""Read-only inventory of existing quarterly balance-sheet identity violations.

An identity violation confirms inconsistent cells, not which cell is wrong.
Missing operands remain unknown; source reconciliation is required to repair them.
"""
from pathlib import Path
import json
import hashlib
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    summaries, details = [], []
    for path in sorted((ROOT / "資產負債").glob("bs_*.csv")):
        if not re.fullmatch(r"bs_\d{4}Q[1-4]\.csv", path.name):
            continue
        df = pd.read_csv(path, dtype={"Ticker": str})
        values = df[["Total_Assets", "Total_Liabilities", "Total_Equity"]].apply(pd.to_numeric, errors="raise")
        complete = values.notna().all(axis=1)
        residual = values.Total_Assets - values.Total_Liabilities - values.Total_Equity
        mismatch = complete & residual.abs().gt(2)
        unknown = ~complete
        summaries.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "rows": len(df), "known_identity": int(complete.sum()),
                          "identity_violations": int(mismatch.sum()), "unknown_identity": int(unknown.sum())})
        for idx in df.index[mismatch | unknown]:
            details.append({"file": path.name, "ticker": df.at[idx, "Ticker"], "name": df.at[idx, "Name"] if "Name" in df else None,
                            "status": "IDENTITY_VIOLATION" if mismatch.at[idx] else "UNKNOWN_OPERAND",
                            **{col: None if pd.isna(values.at[idx, col]) else float(values.at[idx, col]) for col in values},
                            "residual": None if pd.isna(residual.at[idx]) else float(residual.at[idx])})
    payload = {"status": "READ_ONLY_INVENTORY", "identity_tolerance_reporting_units": 2,
               "scope": "Existing local CSV only; unknown is not proof of wrong values, no source fetch or production writes",
               "files": summaries, "issues": details}
    target = ROOT / "ml/reports/research/data_layer_consistency_20260906/balance_sheet_history_invariants.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"quarters": len(summaries), "violations": sum(r["identity_violations"] for r in summaries),
                      "unknown": sum(r["unknown_identity"] for r in summaries),
                      "affected_tickers": len({r["ticker"] for r in details}), "report": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
