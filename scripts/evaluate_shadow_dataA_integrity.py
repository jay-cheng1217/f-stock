"""Research-only target-aligned comparison. Never rewrites the source ledger/model.

Signal at t close, model target Open[t+1] -> Close[t+20]. Complete paired daily
Top30 cohorts only; corporate-action label factors; explicit friction scenarios.
Legacy ret20_pct is retained solely for reconciliation, never called tradable PnL.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from ml.corporate_actions import cumulative_action_factor, load_action_calendar


def target_return(frame: pd.DataFrame, ticker: str, signal_date: str, calendar: pd.DataFrame) -> dict:
    d = frame.copy()
    d["Date"] = pd.to_datetime(d["Date"])
    d = d.sort_values("Date").reset_index(drop=True)
    if d["Date"].duplicated().any():
        return {"status": "duplicate_dates"}
    hits = d.index[d["Date"].eq(pd.Timestamp(signal_date))]
    if len(hits) != 1:
        return {"status": "missing_signal_bar"}
    t = int(hits[0])
    if t + 20 >= len(d):
        return {"status": "immature"}
    if d.loc[t:t+20, "Date"].diff().dt.days.gt(30).any():
        return {"status": "long_suspension"}
    entry, exit_ = float(d.loc[t+1, "Open"]), float(d.loc[t+20, "Close"])
    if not (entry > 0 and exit_ > 0 and np.isfinite(entry + exit_)):
        return {"status": "invalid_price"}
    factors = cumulative_action_factor(d["Date"], ticker, calendar)
    gross = exit_ / entry * factors.iloc[t+1] / factors.iloc[t+20] - 1
    row = {"status": "mature", "entry_date": str(d.loc[t+1, "Date"].date()),
           "exit_date": str(d.loc[t+20, "Date"].date()), "gross_pct": gross*100}
    for bps in (0, 10, 25):
        fee, tax, slip = .001425, .003, bps / 10000
        row[f"net_{bps}bps_pct"] = ((1 + gross) * (1-fee-tax-slip)/(1+fee+slip)-1)*100
    return row


def paired_cohorts(rows: pd.DataFrame) -> pd.DataFrame:
    """A day is comparable only when BOTH whole selected baskets have outcomes."""
    records = []
    for day, d in rows.groupby("date"):
        sides = {s: d[d["side"].eq(s)] for s in ("shadow", "champion")}
        if any(len(x) != 30 or x["ticker"].nunique() != 30 or not x["status"].eq("mature").all()
               for x in sides.values()):
            continue
        r = {"date": day}
        for side, sub in sides.items():
            r[f"{side}_gross_pct"] = float(sub["gross_pct"].mean())
            r[f"{side}_net_pct"] = float(sub["net_10bps_pct"].mean())
        r["delta_pp"] = r["shadow_net_pct"] - r["champion_net_pct"]
        records.append(r)
    return pd.DataFrame(records)


def run(as_of: str, output: Path) -> dict:
    ledger_path = BASE / "ml/reports/shadow_dataA_ledger.csv"
    raw = ledger_path.read_bytes()
    from io import BytesIO
    ledger = pd.read_csv(BytesIO(raw), dtype={"ticker": str})
    calendar = load_action_calendar()
    if calendar.empty:
        raise ValueError("corporate-action factors missing")
    rows, sources = [], {}
    for ticker, subset in ledger.groupby("ticker"):
        p = BASE / "日K資料" / f"{ticker}.csv"
        if not p.exists():
            rows.extend({**r, "status": "missing_file"} for r in subset.to_dict("records"))
            continue
        data = p.read_bytes()
        sources[str(p.relative_to(BASE))] = hashlib.sha256(data).hexdigest()
        d = pd.read_csv(BytesIO(data))
        d = d[pd.to_datetime(d["Date"]) <= pd.Timestamp(as_of)]
        for r in subset.to_dict("records"):
            rows.append({**r, **target_return(d, ticker, r["date"], calendar)})
    evaluated = pd.DataFrame(rows)
    paired = paired_cohorts(evaluated)
    payload = {"as_of": as_of, "ledger_sha256": hashlib.sha256(raw).hexdigest(),
               "rows": len(evaluated), "status_counts": evaluated["status"].value_counts().to_dict(),
               "paired_dates": len(paired), "decision": "keep_current_model_no_promotion"}
    if len(paired):
        payload["paired_mean"] = paired.select_dtypes("number").mean().to_dict()
        # Nonoverlapping signal cohorts using actual market sessions, no iid trade claim.
        index = pd.read_csv(BASE / "大盤指數/index_TWII.csv")
        sessions = pd.DatetimeIndex(pd.to_datetime(index["Date"]).drop_duplicates().sort_values())
        chosen, last = [], -1000
        for i, r in paired.sort_values("date").iterrows():
            pos = sessions.get_indexer([pd.Timestamp(r["date"])])[0]
            if pos >= last+20:
                chosen.append(i); last = pos
        non = paired.loc[chosen]
        payload["nonoverlap_dates"] = len(non)
        payload["nonoverlap_mean_delta_pp"] = float(non["delta_pp"].mean()) if len(non) else None
    output.mkdir(parents=True, exist_ok=True)
    evaluated.to_csv(output / "shadow_target_replay.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(output / "shadow_paired_cohorts.csv", index=False, encoding="utf-8-sig")
    (output / "shadow_target_replay.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for p in (BASE / "ml/data/ex_dividend_calendar.csv", BASE / "ml/data/corporate_action_price_factors.csv"):
        sources[str(p.relative_to(BASE))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (output / "shadow_inputs.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    lines = ["# dataA / Champion：修正交易時點的研究比較", "", f"資料截至 {as_of}，來源帳本與模型原檔未修改。",
             "", "- 訊號後一日開盤進、訊號後第20日收盤出；公司行動用既有 label factor；毛報酬與淨報酬分列。",
             "- 淨報酬假設一般股票賣出稅0.3%、每邊手續費0.1425%、每邊10bps滑價；另輸出0/25bps情境。",
             "- 僅比較同訊號日雙邊各30檔皆已成熟的完整籃子，缺值不得只刪輸家。",
             "- 這是分數Top30比較，未複製主lane型態、盤中守穩、實際部位、Champion組合出場，不能當作帳戶績效。",
             "- 舊帳本的生成日期未完整保留來源證據，歷史混日風險仍在；新分數供應已改逐股日期契約。",
             "- 重疊訊號日彼此相關；非重疊 cohort 太少時無法主張模型優劣有統計把握，不自動升級。", "", "```json", json.dumps(payload, indent=2), "```", ""]
    (output / "shadow_target_replay.md").write_text("\n".join(lines), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.as_of, args.output), indent=2))
