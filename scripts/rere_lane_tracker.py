# -*- coding: utf-8 -*-
"""rere lane 驗證帳本:每日記錄候選 → 逐日對照實際走勢,驗證 lane 是否如回測有效。

回測基準(2026-07-15 重定):60日 平均 +4.58%、勝率 35.8%(停損+除息還原,與帳本同慣例;
scripts/backtest_rere_lane_baseline.py 可重現)。舊 +9.40%/50.6% 為無停損 legacy,作廢。
只以已有完整 60 交易日觀察機會的進場 cohort 評估；提前停損不能先於仍持有的同梯贏家評分。

- record:把當日 entry_list JSON 裡的 rere lane 候選寫入帳本(依 signal_date+ticker 去重)。
- check :用日K最新收盤更新每筆(entry=訊號日次一交易日收盤,與回測一致;
         狀態:持有中/破停損=失敗/60日到期結算),輸出 latest 報告。

檔案:ml/reports/rere_lane_ledger.csv(帳本)、ml/reports/rere_lane_tracking_latest.md(日報)
用法:python scripts/rere_lane_tracker.py record --plan logs/entry_list_20260702.json
      python scripts/rere_lane_tracker.py check
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
LEDGER = BASE_DIR / "ml" / "reports" / "rere_lane_ledger.csv"
REPORT = BASE_DIR / "ml" / "reports" / "rere_lane_tracking_latest.md"
DAILY = BASE_DIR / "日K資料"
HOLD_DAYS = 60
# 並列模擬(2026-10-07 PM 核可;不改正式帳本與現行停損規則):
#   _nostop  = 同一批訊號不停損、抱到第 60 棒(或最新)的結果(BT-rere-stop-width 的前瞻對照)
#   campaign = 她「已公開」持有的標的 ∩ 系統訊號(公開日 <= 訊號日才算,避免事後得知的偷看)
CAMPAIGNS = BASE_DIR / "ml" / "data" / "rere_public_campaigns.csv"
# 基準 2026-07-15 重定(PM核可):scripts/backtest_rere_lane_baseline.py adj 模式
# = 與本帳本同慣例(MA60×0.97停損+除息還原)。舊 +9.40%/50.6% 為無停損純持有之
# legacy 數字,不可重現且慣例不符,已作廢。純持有參考值:+6.77%/45.1%。
BASELINE = {"win": 35.8, "mean": 4.58}


def _load_ledger() -> pd.DataFrame:
    if LEDGER.exists():
        text_columns = ["signal_date", "source_date", "ticker", "name", "entry_date", "last_date", "status", "duplicate_of", "subtype", "recorded_kind", "cohort_asof_date"]
        d = pd.read_csv(LEDGER, dtype={column: str for column in text_columns})
        d["ticker"] = d["ticker"].str.zfill(4)
        for column in ("source_date", "duplicate_of", "subtype", "recorded_kind", "cohort_asof_date"):
            if column not in d.columns:
                d[column] = ""
        if "followup_sessions" not in d.columns:
            d["followup_sessions"] = 0
        for column in text_columns:
            if column in d.columns:
                d[column] = d[column].fillna("").astype(str)
        return d
    return pd.DataFrame(columns=[
        "signal_date", "source_date", "ticker", "name", "zone_low", "zone_high", "stop",
        "entry_date", "entry_close", "last_date", "last_close",
        "days_held", "ret_pct", "status", "duplicate_of", "subtype", "recorded_kind",
        "followup_sessions", "cohort_asof_date"])


def _subtype(row: dict) -> str:
    explicit = str(row.get("subtype") or row.get("ptype") or "")
    if explicit in ("shakeout", "ignition", "shallow", "shakeout_v2", "shallow_v2"):
        return explicit
    description = f"{row.get('status', '')} {row.get('reason', '')}"
    if "發動" in description:
        return "ignition"
    if "淺洗盤型v2" in description:   # v2(2026-10-07):不要求外資拐點,帳本分開累積
        return "shallow_v2"
    if "蹲點型v2" in description:
        return "shakeout_v2"
    if "淺洗盤" in description:
        return "shallow"
    if "蹲點" in description:
        return "shakeout"
    return "unknown"


def _write_ledger(led: pd.DataFrame) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    temporary = LEDGER.with_suffix(LEDGER.suffix + ".tmp")
    led.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, LEDGER)


def record(plan_path: str) -> None:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    led = _load_ledger()
    sig_date = str(plan.get("trade_date", "")).replace("/", "-") or datetime.now().strftime("%Y-%m-%d")
    added = 0
    for r in plan.get("rows", []):
        if r.get("lane") != "rere" and "rere lane" not in str(r.get("status", "")) and "rere lane" not in str(r.get("reason", "")):
            continue
        # 舊 plan 沒有 kind 時維持相容；明確否決/觀察列絕不可當成已接受候選。
        if str(r.get("kind") or "").lower() not in ("", "go", "small"):
            continue
        m = re.search(r"\d{4}", str(r.get("stock", "")))
        if not m:
            continue
        tk = m.group(0)
        source_date = str(r.get("source_date") or sig_date).replace("/", "-")
        existing_sources = led["source_date"].fillna("").astype(str)
        existing_sources = existing_sources.where(existing_sources.ne(""), led["signal_date"])
        same_source = (existing_sources == source_date) & (led["ticker"] == tk)
        if same_source.any() or ((led["signal_date"] == sig_date) & (led["ticker"] == tk)).any():
            continue
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", str(r.get("zone", "")))]
        stop = re.findall(r"\d+\.?\d*", str(r.get("stop", "")))
        led.loc[len(led)] = {
            "signal_date": sig_date, "source_date": source_date,
            "subtype": _subtype(r), "recorded_kind": str(r.get("kind") or "legacy_unspecified"),
            "ticker": tk, "name": str(r.get("stock", ""))[5:9],
            "zone_low": nums[0] if nums else np.nan, "zone_high": nums[1] if len(nums) > 1 else np.nan,
            "stop": float(stop[0]) if stop else np.nan,
            "entry_date": "", "entry_close": np.nan, "last_date": "", "last_close": np.nan,
            "days_held": 0, "ret_pct": np.nan, "status": "pending_entry", "duplicate_of": "",
            "followup_sessions": 0, "cohort_asof_date": ""}
        added += 1
    _write_ledger(led)
    print(f"[rere-tracker] record: +{added} 筆(帳本共 {len(led)} 筆)")


def _load_div_calendar() -> pd.DataFrame:
    p = BASE_DIR / "ml" / "data" / "ex_dividend_calendar.csv"
    if not p.exists():
        return pd.DataFrame(columns=["stock_id", "date", "dv"])
    cal = pd.read_csv(p, dtype={"stock_id": str}, encoding="utf-8-sig")
    cal["dv"] = pd.to_numeric(cal.get("stock_and_cache_dividend"), errors="coerce").fillna(0.0)
    cal["date"] = cal["date"].astype(str).str[:10]
    return cal[["stock_id", "date", "dv"]]


_DIV_CAL = _load_div_calendar()


def _mark_legacy_duplicates(led: pd.DataFrame) -> int:
    """保留重複列供稽核，但不讓休市重跑灌大前瞻樣本數。"""
    required = ["ticker", "entry_date", "entry_close", "stop", "zone_low", "zone_high"]
    if led.empty or any(column not in led.columns for column in required):
        return 0
    eligible = led["entry_date"].fillna("").astype(str).ne("")
    candidates = led.loc[eligible].sort_values(["signal_date", "ticker"])
    duplicate_mask = candidates.duplicated(required, keep="first")
    duplicate_indices = [
        index
        for index in candidates.index[duplicate_mask]
        if led.loc[index, "status"] != "duplicate_signal"
    ]
    for index in duplicate_indices:
        row = led.loc[index]
        first = candidates[
            (candidates["ticker"] == row["ticker"])
            & (candidates["entry_date"] == row["entry_date"])
            & (candidates["entry_close"] == row["entry_close"])
            & (candidates["stop"] == row["stop"])
            & (candidates["zone_low"] == row["zone_low"])
            & (candidates["zone_high"] == row["zone_high"])
        ].iloc[0]
        led.loc[index, "status"] = "duplicate_signal"
        led.loc[index, "ret_pct"] = np.nan
        led.loc[index, "duplicate_of"] = f"{first['signal_date']}:{first['ticker']}"
    return len(duplicate_indices)


def comparable_cohort(led: pd.DataFrame) -> pd.DataFrame:
    """Include early stops only once their original entry has a full follow-up horizon."""
    followup = pd.to_numeric(led.get("followup_sessions", pd.Series(0, index=led.index)), errors="coerce")
    return led[(followup >= HOLD_DAYS) & led["status"].isin(["stopped_out", "matured"])].copy()


def _shadow_paths() -> tuple[Path, Path]:
    """影子檔跟著帳本/報告路徑走,隔離驗證(--ledger/--report)時不會寫到正式目錄。"""
    return LEDGER.with_name(LEDGER.stem + "_nostop.csv"), REPORT.with_name("rere_public_campaign_book.csv")


def no_stop_shadow(led: pd.DataFrame, frames: dict[str, pd.DataFrame], as_of: str | None = None) -> pd.DataFrame:
    """同一批已進場訊號的不停損結果:進場價與除息還原口徑和正式帳本相同,只是不套停損。"""
    rows = []
    for _, row in led.iterrows():
        if str(row["status"]) in ("duplicate_signal", "pending_entry"):
            continue
        tk = str(row["ticker"])
        if tk not in frames:
            path = DAILY / f"{tk}.csv"
            if not path.exists():
                continue
            frame = pd.read_csv(path)
            frame["Date"] = pd.to_datetime(frame["Date"], errors="raise").dt.strftime("%Y-%m-%d")
            frame = frame.sort_values("Date").reset_index(drop=True)
            if as_of is not None:
                frame = frame.loc[frame["Date"] <= as_of].reset_index(drop=True)
            frames[tk] = frame
        d = frames[tk]
        after = d.index[d["Date"] >= str(row["signal_date"])]
        if len(after) == 0:
            continue
        e_idx = after[0]
        c = pd.to_numeric(d["Close"], errors="coerce")
        entry = float(c.iloc[e_idx])
        if not np.isfinite(entry) or entry <= 0:
            continue
        cum_div = pd.Series(0.0, index=d.index)
        for _, ev in _DIV_CAL[_DIV_CAL["stock_id"] == tk].iterrows():
            if ev["date"] > d["Date"].iloc[e_idx]:
                cum_div += (d["Date"] >= ev["date"]).astype(float) * ev["dv"]
        last_idx = min(len(d) - 1, e_idx + HOLD_DAYS)
        last_close = float((c + cum_div).iloc[last_idx])
        if not np.isfinite(last_close):
            continue
        days = int(last_idx - e_idx)
        rows.append({
            "signal_date": row["signal_date"], "source_date": row.get("source_date", ""), "ticker": tk,
            "name": row.get("name", ""), "subtype": (str(row.get("subtype") or "") or "unknown"),
            "entry_date": d["Date"].iloc[e_idx], "entry_close": round(entry, 2),
            "rule_status": row["status"], "rule_ret_pct": row["ret_pct"],
            "nostop_last_date": d["Date"].iloc[last_idx], "nostop_days": days,
            "nostop_ret_pct": round((last_close / entry - 1) * 100, 2),
            "nostop_status": "matured" if days >= HOLD_DAYS else "holding",
        })
    return pd.DataFrame(rows)


def campaign_book(shadow: pd.DataFrame, campaigns_path: Path | None = None) -> tuple[pd.DataFrame, list[str]]:
    """她已公開的標的 ∩ 系統訊號。只收「公開日 <= 訊號日」且未公開出場者;另回傳公開後系統一直沒有訊號的標的。"""
    path = Path(campaigns_path or CAMPAIGNS)
    if shadow.empty or not path.exists():
        return pd.DataFrame(), []
    camp = pd.read_csv(path, dtype=str).fillna("")
    camp["ticker"] = camp["ticker"].str.zfill(4)
    merged = shadow.merge(camp, on="ticker", how="inner", suffixes=("", "_camp"))
    ok = (merged["first_public_date"] <= merged["signal_date"]) & (
        merged["exit_public_date"].eq("") | (merged["signal_date"] <= merged["exit_public_date"]))
    book = merged[ok].copy()
    hit = set(book["ticker"])
    missed = [f"{r['ticker']} {r['name']}(公開 {r['first_public_date']})" for _, r in camp.iterrows()
              if r["kind"] == "campaign" and r["ticker"] not in hit]
    return book, missed


def _stat_line(label: str, values: pd.Series) -> str:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if not len(values):
        return f"- {label}: 0 筆"
    return (f"- {label}: {len(values)} 筆，平均 {values.mean():+.2f}%，中位 {values.median():+.2f}%，"
            f"勝率 {(values > 0).mean()*100:.1f}%，≥+20% {int((values >= 20).sum())} 筆，≤−20% {int((values <= -20).sum())} 筆")


def check(as_of: str | None = None) -> None:
    led = _load_ledger()
    if led.empty:
        print("[rere-tracker] 帳本為空")
        return
    duplicate_count = _mark_legacy_duplicates(led)
    frames: dict[str, pd.DataFrame] = {}
    for i, row in led.iterrows():
        led.loc[i, "followup_sessions"] = 0
        led.loc[i, "cohort_asof_date"] = ""
        status_before = str(row["status"])
        held_before = pd.to_numeric(row.get("days_held"), errors="coerce")
        legacy_day0_stop = status_before == "stopped_out" and held_before == 0
        legacy_post_expiry_stop = status_before == "stopped_out" and held_before > HOLD_DAYS
        if status_before == "duplicate_signal":
            continue
        p = DAILY / f"{row['ticker']}.csv"
        if not p.exists():
            continue
        if str(row["ticker"]) not in frames:
            frame = pd.read_csv(p)
            frame["Date"] = pd.to_datetime(frame["Date"], errors="raise").dt.strftime("%Y-%m-%d")
            frame = frame.sort_values("Date").reset_index(drop=True)
            if frame["Date"].duplicated().any():
                raise ValueError(f"Duplicate trading dates: {p}")
            if as_of is not None:
                frame = frame.loc[frame["Date"] <= as_of].reset_index(drop=True)
            frames[str(row["ticker"])] = frame
        d = frames[str(row["ticker"])]
        c = pd.to_numeric(d["Close"], errors="coerce")
        # 與回測一致:訊號由前一日收盤算出,entry=trade_date 當天收盤(>=)
        after = d.index[d["Date"] >= str(row["signal_date"])]
        if len(after) == 0:
            if as_of is not None:
                led.loc[i, ["entry_date", "entry_close", "last_date", "last_close", "days_held", "ret_pct", "status"]] = ["", np.nan, "", np.nan, 0, np.nan, "pending_entry"]
            continue  # 還沒有訊號日之後的收盤
        e_idx = after[0]
        led.loc[i, "followup_sessions"] = len(d) - 1 - e_idx
        led.loc[i, "cohort_asof_date"] = d["Date"].iloc[-1]
        if as_of is None and (status_before == "matured" or (status_before == "stopped_out" and not legacy_day0_stop and not legacy_post_expiry_stop)):
            continue
        entry = float(c.iloc[e_idx])
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(c.iloc[e_idx:]).all():
            raise ValueError(f"Invalid close for rere verification: {p}")
        # 除息還原:進場後除權息的權值+息值加回收盤價,避免除息缺口誤觸停損/低估報酬
        cum_div = pd.Series(0.0, index=d.index)
        for _, ev in _DIV_CAL[_DIV_CAL["stock_id"] == row["ticker"]].iterrows():
            if ev["date"] > d["Date"].iloc[e_idx]:
                cum_div += (d["Date"] >= ev["date"]).astype(float) * ev["dv"]
        adj_c = c + cum_div
        # 到期後的價格不能回頭推翻已完成的 60 日持有結果。
        last_idx = min(len(d) - 1, e_idx + HOLD_DAYS)
        last_close = float(adj_c.iloc[last_idx])
        days = int(last_idx - e_idx)
        ret = (last_close / entry - 1) * 100
        stop = row["stop"]
        seg = adj_c.iloc[e_idx + 1:last_idx + 1]
        status = "holding"
        mark_idx = last_idx
        if pd.notna(stop) and (seg < float(stop)).any():
            status = "stopped_out"
            hit = seg[seg < float(stop)].index[0]
            last_close = float(adj_c.loc[hit]); ret = (last_close / entry - 1) * 100
            days = int(hit - e_idx)
            mark_idx = hit
        elif days >= HOLD_DAYS:
            status = "matured"
            m_idx = e_idx + HOLD_DAYS
            last_close = float(adj_c.iloc[m_idx]); ret = (last_close / entry - 1) * 100
            days = HOLD_DAYS
            mark_idx = m_idx
        led.loc[i, ["entry_date", "entry_close", "last_date", "last_close",
                    "days_held", "ret_pct", "status"]] = [
            d["Date"].iloc[e_idx], round(entry, 2), d["Date"].iloc[mark_idx],
            round(last_close, 2), days, round(ret, 2), status]
    _write_ledger(led)

    # 報告
    active = led[led["status"].isin(["holding"])]
    closed = led[led["status"].isin(["stopped_out", "matured"])]
    duplicates = led[led["status"] == "duplicate_signal"]
    cohort = comparable_cohort(led)
    lines = [f"# rere lane 前瞻驗證(更新 {datetime.now():%Y-%m-%d %H:%M})", "",
             f"- 舊歷史參考(2026-07-15):60日 平均 **+{BASELINE['mean']}%** / 勝率 **{BASELINE['win']}%**；舊樣本口徑有差異，不作自動降權及格線。",
             "- 報酬/停損為**除息還原毛報酬**，尚未扣交易成本；帳本是候選觀察驗證，並非實際成交或資金組合。",
             f"- 帳本:{len(led)} 筆(持有中 {len(active)}、已結 {len(closed)}、重複排除 {len(duplicates)})", ""]
    if len(closed):
        w = (closed["ret_pct"] > 0).mean() * 100
        lines += [f"## 提前結算觀察 {len(closed)} 筆:平均 {closed['ret_pct'].mean():+.2f}% / 勝率 {w:.0f}%",
                  "僅描述已結算者；存活者尚未到期時，不能拿這組勝率判定策略優劣。", ""]
    lines += [f"## 完整 60 日觀察 cohort：{len(cohort)} 筆", ""]
    if len(cohort):
        values = pd.to_numeric(cohort["ret_pct"], errors="coerce")
        lines += [f"平均 {values.mean():+.2f}% / 勝率 {(values > 0).mean()*100:.1f}% / 進場日 {cohort['entry_date'].nunique()} 日。",
                  "含同梯提前停損與到期持倉；重複標的和重疊持有不等於獨立樣本。", ""]
        for subtype, group in cohort.groupby(cohort["subtype"].replace("", "unknown").fillna("unknown")):
            returns = pd.to_numeric(group["ret_pct"], errors="coerce")
            lines.append(f"- {subtype}: {len(group)} 筆，平均 {returns.mean():+.2f}%，勝率 {(returns > 0).mean()*100:.1f}%")
    lines += ["", "結論：" + ("累積中，完整 cohort 未滿 30 筆，不下策略判決。" if len(cohort) < 30 else "可開始按型態、同期間市場及交易成本檢視；待可比基準與相依樣本分析後再決定權重。"), ""]
    # --- 並列模擬:不停損 + 她已公開標的(不改正式帳本、不作自動判決)---
    shadow = no_stop_shadow(led, frames, as_of)
    nostop_path, campaign_path = _shadow_paths()
    if len(shadow):
        shadow.to_csv(nostop_path, index=False, encoding="utf-8-sig")
        done = shadow[shadow["nostop_status"] == "matured"]
        lines += ["## 並列模擬：同一批訊號不停損（抱到第 60 棒或最新）", "",
                  "現行規則（收盤破 MA60×0.97 停損）不變；此處只並列對照。未扣成本；樣本重疊；不含已下市股票。", "",
                  _stat_line("全部已進場｜照規則", shadow["rule_ret_pct"]), _stat_line("全部已進場｜不停損", shadow["nostop_ret_pct"]),
                  _stat_line("完整 60 棒｜照規則", done["rule_ret_pct"]), _stat_line("完整 60 棒｜不停損", done["nostop_ret_pct"]), ""]
        book, missed = campaign_book(shadow)
        if len(book):
            book.to_csv(campaign_path, index=False, encoding="utf-8-sig")
            lines += ["## 並列模擬：她已公開的標的 ∩ 系統訊號（公開日 ≤ 訊號日）", "",
                      "樣本極小、她只公開部分標的且偏向獲利者；僅供觀察，不是選股依據。", "",
                      _stat_line("照規則", book["rule_ret_pct"]), _stat_line("不停損", book["nostop_ret_pct"]), "",
                      "| 訊號日 | 股票 | 類型 | 進場收盤 | 照規則 | 不停損 | 公開日 |", "|---|---|---|---|---|---|---|"]
            for _, r in book.sort_values(["signal_date", "ticker"]).iterrows():
                lines.append(f"| {r['signal_date']} | {r['ticker']} {r['name']} | {r['kind']} | {r['entry_close']} | "
                             f"{r['rule_ret_pct']}%（{r['rule_status']}） | {r['nostop_ret_pct']}% | {r['first_public_date']} |")
            lines.append("")
        if missed:
            lines += ["公開後系統一直沒有訊號的標的：" + "、".join(missed), ""]
    lines += ["| 訊號日 | 股票 | 進場收盤 | 最新 | 天數 | 報酬 | 狀態 |", "|---|---|---|---|---|---|---|"]
    for _, r in led.sort_values(["signal_date", "ticker"]).iterrows():
        lines.append(f"| {r['signal_date']} | {r['ticker']} {r['name']} | {r['entry_close']} | "
                     f"{r['last_close']} | {r['days_held']} | {r['ret_pct']}% | {r['status']} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"[rere-tracker] check: 持有中 {len(active)}、已結 {len(closed)}、"
        f"本次新標重複 {duplicate_count} → {REPORT.name}"
    )


def main() -> int:
    global LEDGER, REPORT, DAILY
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["record", "check"])
    ap.add_argument("--plan", default=None, help="record 模式:entry_list JSON 路徑(預設當日)")
    ap.add_argument("--ledger", type=Path, help="隔離驗證用帳本路徑；省略時使用正式帳本")
    ap.add_argument("--report", type=Path, help="隔離驗證報告路徑")
    ap.add_argument("--daily-dir", type=Path, help="隔離日K快照")
    ap.add_argument("--as-of", help="check 只讀到指定 YYYY-MM-DD，歷史 replay 須另設 --ledger/--report")
    a = ap.parse_args()
    if a.as_of and (a.ledger is None or a.report is None):
        ap.error("Historical replay requires separate --ledger and --report paths")
    if a.as_of and (a.ledger.resolve() == LEDGER.resolve() or a.report.resolve() == REPORT.resolve()):
        ap.error("Historical replay may not overwrite production ledger or report")
    if a.as_of:
        a.as_of = datetime.strptime(a.as_of, "%Y-%m-%d").date().isoformat()
    if a.ledger is not None:
        LEDGER = a.ledger
    if a.report is not None:
        REPORT = a.report
    if a.daily_dir is not None:
        DAILY = a.daily_dir
    if a.mode == "record":
        plan = a.plan or str(BASE_DIR / "logs" / f"entry_list_{datetime.now():%Y%m%d}.json")
        if not os.path.exists(plan):
            print(f"[rere-tracker] 無當日 plan:{plan}(跳過)")
            return 0
        record(plan)
    else:
        check(as_of=a.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
