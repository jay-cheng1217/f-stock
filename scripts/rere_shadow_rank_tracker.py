# -*- coding: utf-8 -*-
"""rere 候選影子排序研究帳本(2026-10-12 PM 核可「做影子排序」;不產生訊號、不改名單與排序)。

由來:BT-rere-in-list-ranking(回測票 #20)在六年資料裡,同一天的 rere 候選中「散戶持股比 4 週減最多」的前 3 檔,
60 根平均比候選池多 3.00pp、IC 七年同向,但五項判準只過四項(前 3 名贏過池平均的天數 51.8% < 55%),依規定不能改排序。
這本帳用往後的資料做樣本外驗證。

每天做兩件事(整本可由名單檔重建;名單檔已備份的選股寫入 ml/reports/rere_shadow_rank_picks.csv):
  record:讀名單檔的 `shadow_rere_pool`(當日全部 rere 候選,套名額前),以名單資料日為準取集保週資料
          (資料日 +3 天才算已知、最近一週須在 21 天內),算 4 週變化,記下四組:
            retail_top3 = 散戶持股比 4 週變化最低的 3 檔(主假設)     whale_top3 = 千張大戶 4 週變化最高的 3 檔
            listed      = 當天實際上名單的 rere 卡(現行排序的結果)    pool       = 全部候選
          有集保數值的候選不足 8 檔時,當日只記 listed 與 pool,不排名。
  check :每筆以交易日收盤進場、抱 60 根(未到期以最新收盤計)、公司行動連續還原、不停損;
          逐日比較各組平均與候選池平均,再跨日平均。

樣本外採用判準(2026-10-12 寫死;在此之前只顯示「觀察中」):
  累積至少 60 個「排名日」且這些日子的前 3 名都滿 60 根後,retail_top3 必須同時滿足
    1. 逐日超額(對同日候選池)平均 >= +1.5pp
    2. 前半與後半樣本的平均超額都 > 0
    3. 平均報酬 >= listed(現行排序上榜者)
  才向 PM 提請改用集保排序;否則結案封存。
限制:候選池取自產生器套名額前的清單;上榜後被官方公告剔除的會排除,但名額外的候選不會被檢查;集保缺週以前第 4 筆近似。

用法:python -X utf8 scripts/rere_shadow_rank_tracker.py   (排程選用步驟;任何錯誤只印警告並以 0 結束)
輸出:ml/reports/rere_shadow_rank_picks.csv、rere_shadow_rank_book.csv、rere_shadow_rank_latest.md
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)
PLAN_DIR = BASE / "logs"
TDCC = BASE / "集保分散" / "tdcc_summary.csv"
PICKS = BASE / "ml" / "reports" / "rere_shadow_rank_picks.csv"
BOOK = BASE / "ml" / "reports" / "rere_shadow_rank_book.csv"
REPORT = BASE / "ml" / "reports" / "rere_shadow_rank_latest.md"
HOLD, MIN_POOL, TOP_N, MIN_RANK_DAYS = 60, 8, 3, 60
PICK_COLS = ["trade_date", "as_of_date", "group", "rank", "ticker", "name", "ptype", "signal_close",
             "retail4w", "whale4w", "pool_size", "ranked_pool_size"]
GROUP_LABEL = {"retail_top3": "散戶減最多前3(主假設)", "whale_top3": "大戶增最多前3", "listed": "實際上名單(現行排序)",
               "pool": "全部候選"}


def load_tdcc(path: Path = TDCC) -> dict[str, pd.DataFrame]:
    t = pd.read_csv(path, dtype={"Ticker": str, "Date": str})
    t["week"] = pd.to_datetime(t["Date"], format="%Y%m%d", errors="coerce")
    t = t.dropna(subset=["week"]).sort_values(["Ticker", "week"])
    t["retail"] = pd.to_numeric(t["Retail_Pct"], errors="coerce")
    t["whale"] = pd.to_numeric(t["Whale_Pct"], errors="coerce")
    return {tk: g[["week", "retail", "whale"]].reset_index(drop=True) for tk, g in t.groupby("Ticker")}


def tdcc_change(weekly: pd.DataFrame | None, as_of: str) -> tuple[float, float]:
    """(散戶 4 週變化, 大戶 4 週變化);資料日 +3 天 <= as_of 才算已知,最近一週須在 21 天內。"""
    if weekly is None or weekly.empty:
        return np.nan, np.nan
    day = pd.Timestamp(as_of)
    known = weekly[weekly["week"] + pd.Timedelta(days=3) <= day]
    if len(known) < 5 or (day - known["week"].iloc[-1]).days > 21:
        return np.nan, np.nan
    return (float(known["retail"].iloc[-1] - known["retail"].iloc[-5]),
            float(known["whale"].iloc[-1] - known["whale"].iloc[-5]))


def picks_from_plan(plan: dict, tdcc: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pool = plan.get("shadow_rere_pool") or []
    if not pool or not plan.get("as_of_date") or not plan.get("trade_date"):
        return pd.DataFrame(columns=PICK_COLS)
    cand = pd.DataFrame(pool)
    cand["ticker"] = cand["ticker"].astype(str).str.zfill(4)
    cand = cand.drop_duplicates("ticker")
    # 名單上已被官方公告剔除的不是候選(只涵蓋有上榜的;名額外的候選不會被檢查,屬已知限制)
    vetoed = {str(r.get("stock", "")).split()[0] for r in plan.get("rows", []) if "剔除" in str(r.get("status", ""))}
    cand = cand[~cand["ticker"].isin(vetoed)].reset_index(drop=True)
    if cand.empty:
        return pd.DataFrame(columns=PICK_COLS)
    cand["name"] = cand.get("stock", cand["ticker"]).astype(str).str.split(n=1).str[-1]
    changes = [tdcc_change(tdcc.get(tk), plan["as_of_date"]) for tk in cand["ticker"]]
    cand["retail4w"], cand["whale4w"] = [c[0] for c in changes], [c[1] for c in changes]
    ranked = cand.dropna(subset=["retail4w", "whale4w"])
    groups: list[tuple[str, pd.DataFrame]] = [("pool", cand)]
    listed = {str(r.get("stock", "")).split()[0] for r in plan.get("rows", [])
              if "rere" in str(r.get("status", "")) and "剔除" not in str(r.get("status", ""))}
    groups.append(("listed", cand[cand["ticker"].isin(listed)]))
    if len(ranked) >= MIN_POOL:
        groups.append(("retail_top3", ranked.sort_values(["retail4w", "ticker"]).head(TOP_N)))
        groups.append(("whale_top3", ranked.sort_values(["whale4w", "ticker"], ascending=[False, True]).head(TOP_N)))
    out = []
    for name, frame in groups:
        for rank, r in enumerate(frame.itertuples(), 1):
            out.append({"trade_date": plan["trade_date"], "as_of_date": plan["as_of_date"], "group": name, "rank": rank,
                        "ticker": r.ticker, "name": r.name, "ptype": getattr(r, "ptype", ""),
                        "signal_close": getattr(r, "close", np.nan), "retail4w": r.retail4w, "whale4w": r.whale4w,
                        "pool_size": len(cand), "ranked_pool_size": len(ranked)})
    return pd.DataFrame(out, columns=PICK_COLS)


def record(plan_paths: list[Path], tdcc: dict[str, pd.DataFrame], picks_path: Path = PICKS) -> pd.DataFrame:
    """把尚未記錄的交易日寫入選股檔(已記錄的交易日不重寫,保留當時的集保數值)。"""
    existing = (pd.read_csv(picks_path, dtype={"ticker": str, "trade_date": str, "as_of_date": str})
                if Path(picks_path).exists() else pd.DataFrame(columns=PICK_COLS))
    done = set(existing["trade_date"])
    new = []
    for path in plan_paths:
        try:
            plan = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(plan, dict) or plan.get("trade_date") in done:
            continue
        frame = picks_from_plan(plan, tdcc)
        if len(frame):
            new.append(frame)
            done.add(plan["trade_date"])
    if new:
        existing = pd.concat(([existing] if len(existing) else []) + new, ignore_index=True)
        Path(picks_path).parent.mkdir(parents=True, exist_ok=True)
        existing.to_csv(picks_path, index=False, encoding="utf-8-sig")
    return existing


def book_from_picks(picks: pd.DataFrame, bars: dict[str, pd.DataFrame], hold: int = HOLD) -> pd.DataFrame:
    """bars[ticker] = index 為日期字串、含 adj(還原收盤)欄的 DataFrame。"""
    rows = []
    for p in picks.itertuples():
        b = bars.get(str(p.ticker).zfill(4))
        rec = {"trade_date": p.trade_date, "group": p.group, "ticker": str(p.ticker).zfill(4), "name": p.name,
               "ret_pct": np.nan, "bars_held": 0, "matured": False}
        if b is not None and p.trade_date in b.index:
            i = list(b.index).index(p.trade_date)
            end = min(i + hold, len(b) - 1)
            if end > i:
                rec.update(ret_pct=round((b["adj"].iloc[end] / b["adj"].iloc[i] - 1) * 100, 2),
                           bars_held=end - i, matured=(end == i + hold))
        rows.append(rec)
    return pd.DataFrame(rows)


def summarize(book: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳(逐日各組平均含超額, 各組彙總)。只計已有報酬的列。"""
    done = book.dropna(subset=["ret_pct"])
    if done.empty:
        return pd.DataFrame(), pd.DataFrame()
    daily = done.groupby(["trade_date", "group"]).agg(ret=("ret_pct", "mean"), n=("ret_pct", "size"),
                                                      matured=("matured", "all")).reset_index()
    pool = daily[daily["group"] == "pool"].set_index("trade_date")["ret"]
    daily["pool_ret"] = daily["trade_date"].map(pool)
    daily["excess"] = daily["ret"] - daily["pool_ret"]
    out = []
    for group, g in daily.groupby("group"):
        m = g[g["matured"]]
        out.append({"group": group, "days": len(g), "mean_ret": g["ret"].mean(), "mean_excess": g["excess"].mean(),
                    "beat_pool_days": (g["excess"] > 0).mean() * 100, "matured_days": len(m),
                    "matured_excess": m["excess"].mean() if len(m) else np.nan})
    return daily, pd.DataFrame(out)


def verdict(daily: pd.DataFrame) -> str:
    if daily.empty:
        return "觀察中:尚無可計算的樣本"
    r = daily[(daily["group"] == "retail_top3") & daily["matured"]].sort_values("trade_date")
    if len(r) < MIN_RANK_DAYS:
        return f"觀察中:已滿 60 根的排名日 {len(r)}/{MIN_RANK_DAYS},未達判定門檻"
    half = len(r) // 2
    listed = daily[(daily["group"] == "listed") & daily["trade_date"].isin(r["trade_date"])]["ret"].mean()
    checks = [r["excess"].mean() >= 1.5, r["excess"].iloc[:half].mean() > 0 and r["excess"].iloc[half:].mean() > 0,
              r["ret"].mean() >= listed]
    return ("PASS:三項樣本外判準全過,可向 PM 提請改用集保排序" if all(checks)
            else f"FAIL:樣本外判準未全過({''.join('✓' if c else '✗' for c in checks)}),結案封存")


def render(daily: pd.DataFrame, summary: pd.DataFrame, picks: pd.DataFrame, as_of: str) -> str:
    lines = [f"# rere 候選影子排序研究帳本(資料至 {as_of})", "",
             "假設:同一天的 rere 候選裡,散戶持股比 4 週減最多的前 3 檔,60 根表現優於候選池(回測票 #20:六年 +3.00pp,4/5 判準)。",
             "這本帳只做樣本外驗證;**現行名單與排序不受影響**,這裡的股票不是進場建議。", "",
             f"**狀態:{verdict(daily)}**", ""]
    if summary.empty:
        lines += ["尚無已進場的樣本(第一批候選在下一個交易日收盤後才有進場價)。", ""]
    else:
        lines += ["| 組別 | 排名日數 | 平均報酬 | 對候選池超額 | 贏過候選池的日數 | 已滿 60 根日數 | 已滿者超額 |", "|---|---|---|---|---|---|---|"]
        order = {"retail_top3": 0, "whale_top3": 1, "listed": 2, "pool": 3}
        for r in summary.sort_values("group", key=lambda s: s.map(order)).itertuples():
            lines.append(f"| {GROUP_LABEL.get(r.group, r.group)} | {r.days} | {r.mean_ret:+.2f}% | {r.mean_excess:+.2f}pp | "
                         f"{r.beat_pool_days:.0f}% | {r.matured_days} | "
                         + (f"{r.matured_excess:+.2f}pp |" if pd.notna(r.matured_excess) else "— |"))
        lines.append("")
    latest = picks[picks["trade_date"] == picks["trade_date"].max()] if len(picks) else picks
    if len(latest):
        top = latest[latest["group"] == "retail_top3"]
        lines += [f"最新一日({latest['trade_date'].iloc[0]})候選 {int(latest['pool_size'].iloc[0])} 檔,"
                  f"有集保數值 {int(latest['ranked_pool_size'].iloc[0])} 檔。"
                  + ("散戶減最多前 3:" + "、".join(f"{r.ticker} {r.name}({r.retail4w:+.2f}pp)" for r in top.itertuples())
                     if len(top) else "有集保數值的候選不足 8 檔,當日不排名。"), ""]
    return "\n".join(lines) + "\n"


def run() -> str:
    sys.path.insert(0, str(REPO))
    from ml.corporate_actions import load_action_calendar
    from scripts.rere_public_follow_tracker import load_bars

    plans = sorted(p for p in PLAN_DIR.glob("entry_list_2*.json") if not p.name.endswith(".manifest.json"))
    picks = record(plans, load_tdcc())
    if picks.empty:
        REPORT.write_text(render(pd.DataFrame(), pd.DataFrame(), picks, "—"), encoding="utf-8")
        return "no picks yet"
    start = (pd.Timestamp(picks["as_of_date"].min()) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    bars = load_bars(BASE / "日K資料", start, actions)
    book = book_from_picks(picks, bars)
    daily, summary = summarize(book)
    book.to_csv(BOOK, index=False, encoding="utf-8-sig")
    as_of = max(b.index[-1] for b in bars.values())
    REPORT.write_text(render(daily, summary, picks, as_of), encoding="utf-8")
    return f"{picks['trade_date'].nunique()} 個交易日、{len(picks)} 筆選股;{verdict(daily)}"


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        print("[shadow-rank]", run(), "→", REPORT.name)
    except Exception as exc:  # noqa: BLE001 - 研究帳本失敗不可擋名單/儀表板
        print(f"[shadow-rank] WARN skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
