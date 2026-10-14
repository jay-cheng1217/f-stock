# -*- coding: utf-8 -*-
"""BT-rere-daytrade-ratio(回測票 #25):當沖比率能不能當「短沖主力」的免費代理,用來否決 rere 候選?

背景(PM 2026-10-14 放棄付費分點來源後「都做」):原相 3227 那次靠籌碼K截圖才看出「前幾大買方全是隔日沖分點」。
分點資料不買,改看免費的個股當沖量(FinMind 免費資料集 TaiwanStockDayTrading,2019 年起,上市上櫃皆有)。
假設:訊號日前後當沖比率偏高 = 短線資金在玩,rere 這種要抱 60 根的型態結果較差。

資料:FinMind TaiwanStockDayTrading 的 Volume(當日沖銷成交股數),逐檔抓、存 ml/reports/cache/daytrade_finmind/<代號>.csv
  (研究快取,不進版控、不是憑證來源)。缺資料不補零:當天沒有紀錄 = 缺值,不參與判定。
特徵(資料日 T 收盤後即公布,當晚可用):
  dt1 = T 日當沖股數 / T 日成交股數
  dt5 = T−4~T 當沖股數合計 / 同期成交股數合計(五天都要有資料)
假設方向:越高越差。
母體與報酬:scripts/rere_candidate_panel.build_panel()(與 #20/#23/#26 相同;次日收盤進、60 根、還原、不停損)。

主判定(否決規則,2026-10-14 寫死,先於執行提交;四項全過 = PASS):
  規則:每個訊號日,候選池中 dt5 有資料的比例 >= 80% 才計;dt5 >= 當天候選 dt5 第 80 百分位者列為「否決組」。
  指標 = 否決組平均報酬 − 當天全部候選平均報酬(逐日算再平均)。
  1. 逐日平均 <= −1.5pp
  2. 逐年平均為負的年份 >= 6/7(2020-2026)
  3. 2020-2022 與 2023-2026 兩段都 <= −1.0pp
  4. 否決組低於當天候選平均的天數比例 >= 55%
次要(只列出,不作判定依據):dt1、dt5 用 #20 的五項排序判準(方向 −)。
判決用途:PASS → 只建議先以卡片標籤「當沖比率偏高」並行觀察(不直接剔除),正式否決需 PM 核可。
FAIL → 不採用,分點類否決維持人工截圖。反方向即使很強,也只能當下一張事前登記票的假設。
限制:不含已下市股票;樣本日重疊、不做顯著性檢定;當沖量含現股當沖與先賣後買,無法區分是哪些分點在做。

用法:
  python -X utf8 scripts/backtest_rere_daytrade_ratio.py --fetch   # 抓/補當沖歷史(可中斷續抓;每小時 <= 550 次)
  python -X utf8 scripts/backtest_rere_daytrade_ratio.py           # 回測(worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_rere_daytrade_ratio.csv + stdout
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rere_candidate_panel import BASE, MIN_POOL, REPO, build_panel  # noqa: E402

CACHE = BASE / "ml" / "reports" / "cache" / "daytrade_finmind"
API = "https://api.finmindtrade.com/api/v4/data"
PAUSE_SECONDS = 6.6            # 約 545 次/小時,留餘裕給正式流程
TOP_N = 3


def _fetch_window_open(now: datetime) -> bool:
    """交易日 06:30-14:30 不抓:避開早上 Phase-2 的 FinMind 用量與盤中紅線。"""
    from scripts.taiwan_trading_calendar import is_taiwan_trading_day
    if not is_taiwan_trading_day(now.date()):
        return True
    return not (now.replace(hour=6, minute=30) <= now < now.replace(hour=14, minute=30))


def fetch(tickers: list[str]) -> None:
    import requests
    from dotenv import dotenv_values

    token = (os.environ.get("FINMIND_TOKEN") or dotenv_values(BASE / ".env", encoding="utf-8-sig").get("FINMIND_TOKEN") or "").strip()
    if not token:
        raise SystemExit("FINMIND_TOKEN 未設定")
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = [tk for tk in tickers if not (CACHE / f"{tk}.csv").exists()]
    print(f"[fetch] 需要 {len(tickers)} 檔,已快取 {len(tickers) - len(todo)},待抓 {len(todo)}")
    errors = 0
    for i, tk in enumerate(todo, 1):
        if not _fetch_window_open(datetime.now()):
            print(f"[fetch] {datetime.now():%H:%M} 進入交易日 06:30-14:30 禁抓時段,停止;剩 {len(todo) - i + 1} 檔下次續抓")
            return
        try:
            r = requests.get(API, headers={"Authorization": f"Bearer {token}"}, timeout=90,
                             params={"dataset": "TaiwanStockDayTrading", "data_id": tk,
                                     "start_date": "2019-12-01", "end_date": date.today().isoformat()})
            payload = r.json()
        except Exception as exc:  # noqa: BLE001 - 網路錯誤稍後重試
            payload, r = {"status": None, "msg": type(exc).__name__}, None
        if r is None or r.status_code != 200 or payload.get("status") != 200:
            errors += 1
            msg = str(payload.get("msg", ""))[:120].replace(token, "[REDACTED]")
            print(f"[fetch] {tk} 失敗({errors}):http={getattr(r, 'status_code', None)} {msg}")
            if errors >= 5:
                print("[fetch] 連續失敗 5 次,停止;稍後重跑會從未完成處續抓")
                return
            time.sleep(120)
            continue
        errors = 0
        rows = payload.get("data") or []
        frame = pd.DataFrame(rows, columns=["date", "stock_id", "Volume", "BuyAmount", "SellAmount"])
        frame.rename(columns={"Volume": "dt_volume"})[["date", "dt_volume", "BuyAmount", "SellAmount"]].to_csv(
            CACHE / f"{tk}.csv", index=False, encoding="utf-8")
        if i % 50 == 0 or i == len(todo):
            print(f"[fetch] {datetime.now():%H:%M} 已完成 {i}/{len(todo)}")
        time.sleep(PAUSE_SECONDS)


def attach_features(D: pd.DataFrame, series: dict) -> pd.DataFrame:
    parts = []
    for tk, g in D.groupby("ticker"):
        f = CACHE / f"{tk}.csv"
        s = series[tk][["date", "volume"]].copy()
        if f.exists():
            dt = pd.read_csv(f, dtype={"date": str})
            dt["date"] = dt["date"].str[:10]
            s = s.merge(dt[["date", "dt_volume"]], on="date", how="left")
        else:
            s["dt_volume"] = np.nan
        s["dt1"] = s["dt_volume"] / s["volume"]
        dv5 = s["dt_volume"].rolling(5, min_periods=5).sum()          # 五天任一缺值 → 缺值
        s["dt5"] = dv5 / s["volume"].rolling(5, min_periods=5).sum()
        parts.append(g.merge(s[["date", "dt1", "dt5"]], on="date", how="left"))
    X = pd.concat(parts, ignore_index=True)
    X[["dt1", "dt5"]] = X[["dt1", "dt5"]].where(X[["dt1", "dt5"]].le(1.5))   # 明顯錯誤的比值(>150%)當缺值
    return X


def veto_test(X: pd.DataFrame) -> dict:
    out = []
    for day, g in X.groupby("date"):
        have = g["dt5"].notna()
        if len(g) < MIN_POOL or have.mean() < 0.8:
            continue
        cut = g.loc[have, "dt5"].quantile(0.8)
        vetoed = g[have & (g["dt5"] >= cut)]
        if vetoed.empty:
            continue
        out.append({"date": day, "diff": vetoed["fwd"].mean() - g["fwd"].mean(), "n_veto": len(vetoed), "pool": len(g)})
    r = pd.DataFrame(out)
    r["year"] = r["date"].str[:4]
    r["half"] = np.where(r["year"] <= "2022", "2020-22", "2023-26")
    yearly = r.groupby("year")["diff"].mean()
    halves = r.groupby("half")["diff"].mean()
    checks = [r["diff"].mean() <= -1.5, int((yearly < 0).sum()) >= 6,
              bool((halves <= -1.0).all()) and len(halves) == 2, (r["diff"] < 0).mean() >= 0.55]
    return {"test": "veto dt5 >= P80", "days": len(r), "mean_pp": r["diff"].mean(), "below_pool_days": (r["diff"] < 0).mean() * 100,
            "h1": halves.get("2020-22", np.nan), "h2": halves.get("2023-26", np.nan),
            "years_negative": int((yearly < 0).sum()), "years": len(yearly),
            "yearly": " ".join(f"{y}:{v:+.2f}" for y, v in yearly.items()), "avg_vetoed": r["n_veto"].mean(),
            "checks": "".join("✓" if ok else "✗" for ok in checks), "verdict": "PASS" if all(checks) else "FAIL"}


def ranking_test(X: pd.DataFrame, feature: str) -> dict:
    out = []
    for day, g in X.groupby("date"):
        x = g[[feature, "fwd"]].dropna()
        if len(x) < MIN_POOL:
            continue
        out.append({"date": day, "ic": -x[feature].rank().corr(x["fwd"].rank()),
                    "top": x.sort_values(feature).head(TOP_N)["fwd"].mean() - x["fwd"].mean()})
    r = pd.DataFrame(out)
    r["year"] = r["date"].str[:4]
    r["half"] = np.where(r["year"] <= "2022", "2020-22", "2023-26")
    yearly = r.groupby("year")["ic"].mean()
    halves = r.groupby("half")["top"].mean()
    checks = [r["ic"].mean() >= 0.03, int((yearly > 0).sum()) >= 6, r["top"].mean() >= 1.5,
              bool((halves >= 1.0).all()) and len(halves) == 2, (r["top"] > 0).mean() >= 0.55]
    return {"test": f"ranking {feature}(−)", "days": len(r), "mean_pp": r["top"].mean(), "ic": r["ic"].mean(),
            "h1": halves.get("2020-22", np.nan), "h2": halves.get("2023-26", np.nan),
            "years_same_sign": int((yearly > 0).sum()), "years": len(yearly),
            "yearly": " ".join(f"{y}:{v:+.3f}" for y, v in yearly.items()),
            "checks": "".join("✓" if ok else "✗" for ok in checks), "verdict": "PASS" if all(checks) else "FAIL"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="抓/補當沖歷史快取,不跑回測")
    args = ap.parse_args()
    D, series = build_panel()
    tickers = sorted(D["ticker"].unique())
    if args.fetch:
        fetch(tickers)
        return
    missing = [tk for tk in tickers if not (CACHE / f"{tk}.csv").exists()]
    if missing:
        raise SystemExit(f"當沖快取未抓完:缺 {len(missing)}/{len(tickers)} 檔,先跑 --fetch")
    X = attach_features(D, series)
    print(f"=== BT-rere-daytrade-ratio 候選股票日 {len(X)},訊號日 {X.date.nunique()},{X.date.min()}~{X.date.max()};"
          f"dt5 有值 {X.dt5.notna().mean() * 100:.1f}%;dt5 中位 {X.dt5.median() * 100:.1f}% ===")
    rows = [veto_test(X), ranking_test(X, "dt1"), ranking_test(X, "dt5")]
    v = rows[0]
    print(f"\n[主判定:否決 dt5 >= 當日 P80] {v['days']} 天,每天平均否決 {v['avg_vetoed']:.1f} 檔")
    print(f"  否決組 − 當天候選平均 = {v['mean_pp']:+.2f}pp;前段/後段 {v['h1']:+.2f}/{v['h2']:+.2f}pp;"
          f"為負年份 {v['years_negative']}/{v['years']};低於候選平均天數 {v['below_pool_days']:.1f}%")
    print(f"  逐年:{v['yearly']}")
    print(f"  判準 1-4:{v['checks']} → {v['verdict']}")
    for r in rows[1:]:
        print(f"\n[次要:{r['test']}] 平均IC {r['ic']:+.3f};前3名(最低)超額 {r['mean_pp']:+.2f}pp;前段/後段 {r['h1']:+.2f}/{r['h2']:+.2f};"
              f"同向年數 {r['years_same_sign']}/{r['years']};判準 1-5:{r['checks']}(只列出,不判定)")
        print(f"  逐年 IC:{r['yearly']}")
    out = REPO / "ml" / "reports" / "bt_rere_daytrade_ratio.csv"
    pd.DataFrame(rows).round(4).to_csv(out, index=False, encoding="utf-8-sig")
    print("\n判決(主判定):", v["verdict"], "| 彙總:", out)


if __name__ == "__main__":
    main()
