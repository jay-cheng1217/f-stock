# -*- coding: utf-8 -*-
"""rere 公開標的跟單帳本(2026-10-12 PM 核可;研究帳本,不產生任何訊號、不改任何規則)。

問題:她公開貼文的標的,外部的人「看到貼文、次一交易日收盤買進」到底有沒有贏過市場?
2026-10-12 第一次量測:10 檔等權 +7.4%,同期全市場等權 +6.3%,超額 +1.1pp、5/10 贏過市場——樣本太小,所以持續記帳累積。

規則(每天由 ml/data/rere_public_campaigns.csv 整本重算,可重現):
  進場 = 首次公開日的次一交易日收盤;該日一字鎖漲停(最高=最低且漲幅 >= 9%)視為買不到,順延,最多 5 日。
  出場 = 她公開出場(exit_public_date)的次一交易日收盤;否則第 60 根;都未到則以最新收盤計。
  報酬用公司行動連續還原(ml.corporate_actions)。
  市場 = 同一段期間、進場日 20 日均量 >= 500 張的普通股等權平均。超額 = 報酬 − 市場。
限制:名單只含她公開的標的(她只公開部分、偏向獲利者),公開日由截圖人工登錄;跟單價不是她的成本,她的部位與損益不可得。

用法:python -X utf8 scripts/rere_public_follow_tracker.py     (排程步驟;任何錯誤都只印警告並以 0 結束,不擋名單與儀表板)
輸出:ml/reports/rere_public_follow_book.csv、ml/reports/rere_public_follow_latest.md
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)
DAILY = BASE / "日K資料"
CAMPAIGNS = BASE / "ml" / "data" / "rere_public_campaigns.csv"
BOOK = BASE / "ml" / "reports" / "rere_public_follow_book.csv"
REPORT = BASE / "ml" / "reports" / "rere_public_follow_latest.md"
HOLD, LIQ_MIN, MAX_ENTRY_DELAY = 60, 500_000, 5


def load_bars(daily_dir: Path, start: str, actions: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """4 碼普通股自 start 起的日K:index=日期字串,欄位 adj(還原收盤)/close/high/low/liquid。"""
    from ml.corporate_actions import backward_adjustment_multiplier

    action_tickers = set(actions["stock_id"].astype(str)) if actions is not None else set()
    bars: dict[str, pd.DataFrame] = {}
    for path in sorted(Path(daily_dir).glob("*.csv")):
        tk = path.stem
        if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
            continue
        try:
            k = pd.read_csv(path, usecols=["Date", "High", "Low", "Close", "VOL_MA_20"])
        except Exception:  # noqa: BLE001 - 單檔壞掉不影響帳本
            continue
        k["Date"] = k["Date"].astype(str).str[:10]
        k = k[k["Date"] >= start].reset_index(drop=True)
        if len(k) < 2:
            continue
        close = pd.to_numeric(k["Close"], errors="coerce")
        mult = (backward_adjustment_multiplier(k["Date"], tk, actions).to_numpy(float)
                if tk in action_tickers else np.ones(len(k)))
        bars[tk] = pd.DataFrame({"adj": close.to_numpy(float) * mult, "close": close.to_numpy(float),
                                 "high": pd.to_numeric(k["High"], errors="coerce").to_numpy(float),
                                 "low": pd.to_numeric(k["Low"], errors="coerce").to_numpy(float),
                                 "liquid": (pd.to_numeric(k["VOL_MA_20"], errors="coerce") >= LIQ_MIN).to_numpy()},
                                index=k["Date"].to_numpy())
    return bars


def build_follow_book(campaigns: pd.DataFrame, bars: dict[str, pd.DataFrame], hold: int = HOLD) -> pd.DataFrame:
    adj = pd.DataFrame({tk: b["adj"] for tk, b in bars.items()}).sort_index()
    liquid = pd.DataFrame({tk: b["liquid"] for tk, b in bars.items()}).sort_index().fillna(False).astype(bool)

    def market(start_day: str, end_day: str) -> float:
        pool = liquid.columns[liquid.loc[start_day].to_numpy()]
        ratio = adj.loc[end_day, pool] / adj.loc[start_day, pool] - 1
        return float(ratio.dropna().mean() * 100) if len(pool) else float("nan")

    rows = []
    for c in campaigns.fillna("").itertuples():
        tk = str(c.ticker).zfill(4)
        row = {"ticker": tk, "name": c.name, "kind": c.kind, "first_public_date": c.first_public_date,
               "exit_public_date": c.exit_public_date, "status": "no_data"}
        b = bars.get(tk)
        if b is None:
            rows.append(row)
            continue
        days = list(b.index)
        after = [i for i, d in enumerate(days) if d > c.first_public_date]
        entry_i = None
        for i in after[:MAX_ENTRY_DELAY]:
            locked = (i > 0 and b["high"].iloc[i] == b["low"].iloc[i]
                      and b["close"].iloc[i] >= b["close"].iloc[i - 1] * 1.09)
            if not locked:
                entry_i = i
                break
        if entry_i is None:
            row["status"] = "pending_entry" if not after else "unfillable"
            rows.append(row)
            continue
        last_i = len(days) - 1
        exit_i = None
        if c.exit_public_date:
            later = [i for i, d in enumerate(days) if d > c.exit_public_date and i >= entry_i]
            exit_i = later[0] if later else None
        end_i = min(x for x in (entry_i + hold, exit_i, last_i) if x is not None)
        status = ("exited_public" if exit_i is not None and end_i == exit_i
                  else "matured60" if end_i == entry_i + hold else "holding")
        entry_day, end_day = days[entry_i], days[end_i]
        ret = (b["adj"].iloc[end_i] / b["adj"].iloc[entry_i] - 1) * 100
        mkt = market(entry_day, end_day)
        row.update({"status": status, "entry_date": entry_day, "entry_close": float(b["close"].iloc[entry_i]),
                    "last_date": end_day, "last_close": float(b["close"].iloc[end_i]), "bars_held": end_i - entry_i,
                    "ret_pct": round(ret, 2), "market_pct": round(mkt, 2), "excess_pct": round(ret - mkt, 2),
                    "entry_delayed": entry_i != after[0]})
        if entry_i + 20 <= last_i:
            day20 = days[entry_i + 20]
            ret20 = (b["adj"].iloc[entry_i + 20] / b["adj"].iloc[entry_i] - 1) * 100
            row.update({"ret20_pct": round(ret20, 2), "market20_pct": round(market(entry_day, day20), 2)})
        rows.append(row)
    return pd.DataFrame(rows)


def _line(label: str, book: pd.DataFrame) -> str:
    done = book.dropna(subset=["ret_pct"]) if "ret_pct" in book.columns else book.iloc[0:0]
    if not len(done):
        return f"- {label}:0 筆"
    return (f"- {label}:{len(done)} 筆,平均 {done.ret_pct.mean():+.1f}%,同期市場 {done.market_pct.mean():+.1f}%,"
            f"超額 {done.excess_pct.mean():+.1f}pp,贏過市場 {int((done.excess_pct > 0).sum())}/{len(done)}")


def render(book: pd.DataFrame, as_of: str) -> str:
    lines = [f"# rere 公開標的跟單帳本(資料至 {as_of})", "",
             "規則:公開貼文次一交易日收盤買進、等權;她公開出場則次日收盤跟出,否則抱 60 根,未到期以最新收盤計。",
             "市場 = 同期間全市場等權(進場日均量 ≥ 500 張)。**這不是她的損益**:她的成本、部位、進出場都看不到;",
             "名單只含她公開的標的,樣本小且偏向她願意公開的。研究帳本,不是選股依據。", "",
             _line("全部", book), _line("長抱(campaign)", book[book["kind"] == "campaign"]),
             _line("事件/試單", book[book["kind"] != "campaign"]), ""]
    if "ret20_pct" in book.columns and book["ret20_pct"].notna().any():
        b20 = book.dropna(subset=["ret20_pct"])
        lines += [f"- 進場後 20 根:{len(b20)} 筆,平均 {b20.ret20_pct.mean():+.1f}%,同期市場 {b20.market20_pct.mean():+.1f}%", ""]
    lines += ["| 公開日 | 股票 | 類型 | 進場日 | 進場收盤 | 最新/出場 | 根數 | 報酬 | 市場 | 超額 | 狀態 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in book.sort_values("first_public_date").itertuples():
        if pd.isna(getattr(r, "ret_pct", np.nan)):
            lines.append(f"| {r.first_public_date} | {r.ticker} {r.name} | {r.kind} | — | — | — | — | — | — | — | {r.status} |")
            continue
        lines.append(f"| {r.first_public_date} | {r.ticker} {r.name} | {r.kind} | {r.entry_date} | {r.entry_close} | "
                     f"{r.last_date} {r.last_close} | {int(r.bars_held)} | {r.ret_pct:+.1f}% | {r.market_pct:+.1f}% | "
                     f"{r.excess_pct:+.1f}pp | {r.status} |")
    return "\n".join(lines) + "\n"


def run() -> pd.DataFrame:
    sys.path.insert(0, str(REPO))
    from ml.corporate_actions import load_action_calendar

    campaigns = pd.read_csv(CAMPAIGNS, dtype=str).fillna("")
    start = (pd.Timestamp(campaigns["first_public_date"].min()) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    bars = load_bars(DAILY, start, actions)
    book = build_follow_book(campaigns, bars)
    as_of = max(b.index[-1] for b in bars.values())
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    book.to_csv(BOOK, index=False, encoding="utf-8-sig")
    REPORT.write_text(render(book, as_of), encoding="utf-8")
    return book


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        book = run()
        print("[follow-book]", _line("全部", book).lstrip("- "), "→", REPORT.name)
    except Exception as exc:  # noqa: BLE001 - 研究帳本失敗不可擋名單/儀表板
        print(f"[follow-book] WARN skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
