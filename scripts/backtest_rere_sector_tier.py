# -*- coding: utf-8 -*-
"""BT-rere-sector-tier(回測票 #26):rere 名單的第一排序鍵「族群強度分級」到底有沒有用?

背景(PM 2026-10-14「都做」):rere 卡片排序 = 族群強度(strong 2 / neutral 1 / weak 0)+ 中性鍵(#23)。
族群強度由 scripts/sector_strength.py 每晚只算「當天」一份,從來沒有歷史版本,所以這個第一排序鍵從未被回測過。
本票照正式口徑重建每天的分級,檢驗同一天的候選裡,強勢族群是否真的比中性族群好、弱勢是否不比中性好。

分級重建(照正式程式,原樣複製,不改良):每檔「原始收盤」近 20 日報酬(收盤 / 20 根前收盤 − 1);
  各產業(ml/data/sector_mapping.csv)取當天有值成分股的中位數,成分股 < 5 檔的產業不分級;
  產業依中位數由高到低排名,pct = 1 − 名次 / (產業數 − 1);pct >= 0.66 = strong、<= 0.33 = weak、其餘 neutral;
  無產業對照或該產業未分級的股票視為 neutral(正式程式的預設)。
  敏感度(只列出、不判定):改用公司行動連續還原收盤算 20 日報酬的版本。
母體與報酬:scripts/rere_candidate_panel.build_panel()(與 #20/#23 相同;次日收盤進、60 根、還原、不停損)。

指標(逐日算再平均):
  強−中 = 當天 strong 候選平均報酬 − 當天 neutral 候選平均報酬(兩組當天都有候選才計)
  弱−中 = 當天 weak 候選平均報酬 − 當天 neutral 候選平均報酬(兩組當天都有候選才計)

事前判準(2026-10-14 寫死,先於執行提交;四項全過 = 分級值得留在第一排序鍵):
  1. 強−中 逐日平均 >= +1.0pp
  2. 強−中 逐年平均為正的年份 >= 6/7(2020-2026)
  3. 2020-2022 與 2023-2026 兩段的強−中 都 >= +0.5pp
  4. 弱−中 逐日平均 <= 0(弱勢不比中性好)
判決用途:PASS → 維持現行排序。FAIL → 建議把族群強度移出排序、改為完全中性順序(屬正式排序變更,需 PM 核可)。
反方向即使很強(強勢反而差),也只能當下一張事前登記票的假設,不能直接採用。
限制:不含已下市股票;樣本日重疊、不做顯著性檢定;產業對照為現行版本(歷史上公司改類別不回溯)。

用法:python -X utf8 scripts/backtest_rere_sector_tier.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
輸出:ml/reports/bt_rere_sector_tier.csv + stdout
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rere_candidate_panel import BASE, REPO, build_panel  # noqa: E402

D, SERIES = build_panel()
sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str)
SMAP = dict(zip(sec["Ticker"].str.zfill(4), sec["Sector"]))


def tiers(price_col: str) -> pd.DataFrame:
    """每天每檔的族群分級(照正式程式口徑);回傳 [date, ticker, tier]。"""
    frames = []
    for tk, s in SERIES.items():
        sector = SMAP.get(tk)
        if not sector:
            continue
        p = pd.Series(s[price_col].to_numpy(float))
        frames.append(pd.DataFrame({"date": s["date"].to_numpy(), "ticker": tk, "sector": sector,
                                    "r20": (p / p.shift(20) - 1).to_numpy(float)}))
    R = pd.concat(frames, ignore_index=True)
    R = R[np.isfinite(R["r20"]) & (R["date"] >= D["date"].min()) & (R["date"] <= D["date"].max())]
    med = R.groupby(["date", "sector"])["r20"].agg(["median", "size"]).reset_index()
    med = med[med["size"] >= 5].copy()
    med["rank"] = med.groupby("date")["median"].rank(ascending=False, method="first") - 1
    med["n"] = med.groupby("date")["median"].transform("size")
    med["pct"] = 1 - med["rank"] / (med["n"] - 1).clip(lower=1)
    med["tier"] = np.where(med["pct"] >= 0.66, "strong", np.where(med["pct"] <= 0.33, "weak", "neutral"))
    out = R[["date", "ticker", "sector"]].merge(med[["date", "sector", "tier"]], on=["date", "sector"], how="left")
    return out[["date", "ticker", "tier"]]


def evaluate(label: str, tier_frame: pd.DataFrame) -> dict:
    X = D.merge(tier_frame, on=["date", "ticker"], how="left")
    X["tier"] = X["tier"].fillna("neutral")
    g = X.groupby(["date", "tier"])["fwd"].mean().unstack()
    for col in ("strong", "neutral", "weak"):
        if col not in g:
            g[col] = np.nan
    days = pd.DataFrame({"sn": g["strong"] - g["neutral"], "wn": g["weak"] - g["neutral"]})
    days["year"] = days.index.str[:4]
    days["half"] = np.where(days["year"] <= "2022", "2020-22", "2023-26")
    sn = days["sn"].dropna()
    yearly = days.groupby("year")["sn"].mean()
    halves = days.groupby("half")["sn"].mean()
    share = X["tier"].value_counts(normalize=True) * 100
    checks = [sn.mean() >= 1.0, int((yearly > 0).sum()) >= 6,
              bool((halves >= 0.5).all()) and len(halves) == 2, days["wn"].dropna().mean() <= 0]
    return {"version": label, "cand_days": len(X), "signal_days": X["date"].nunique(),
            "strong_share": share.get("strong", 0.0), "neutral_share": share.get("neutral", 0.0), "weak_share": share.get("weak", 0.0),
            "strong_mean": X.loc[X.tier == "strong", "fwd"].mean(), "neutral_mean": X.loc[X.tier == "neutral", "fwd"].mean(),
            "weak_mean": X.loc[X.tier == "weak", "fwd"].mean(),
            "sn_days": len(sn), "sn_mean": sn.mean(), "sn_beat_days": (sn > 0).mean() * 100,
            "sn_h1": halves.get("2020-22", np.nan), "sn_h2": halves.get("2023-26", np.nan),
            "wn_days": int(days["wn"].notna().sum()), "wn_mean": days["wn"].dropna().mean(),
            "years_positive": int((yearly > 0).sum()), "years": len(yearly),
            "yearly_sn": " ".join(f"{y}:{v:+.2f}" for y, v in yearly.items()),
            "checks": "".join("✓" if ok else "✗" for ok in checks), "verdict": "PASS" if all(checks) else "FAIL"}


print(f"=== BT-rere-sector-tier 候選股票日 {len(D)},訊號日 {D.date.nunique()},{D.date.min()}~{D.date.max()};"
      f"候選池 60 根平均 {D.fwd.mean():+.2f}% ===")
rows = [evaluate("正式口徑(原始收盤)", tiers("close")), evaluate("敏感度(還原收盤,不判定)", tiers("adj_close"))]
for r in rows:
    print(f"\n[{r['version']}] 候選占比 強/中/弱 = {r['strong_share']:.0f}%/{r['neutral_share']:.0f}%/{r['weak_share']:.0f}%;"
          f"60 根平均 強 {r['strong_mean']:+.2f}% 中 {r['neutral_mean']:+.2f}% 弱 {r['weak_mean']:+.2f}%")
    print(f"  強−中 逐日平均 {r['sn_mean']:+.2f}pp({r['sn_days']} 天,強勝 {r['sn_beat_days']:.1f}%);"
          f"前段/後段 {r['sn_h1']:+.2f}/{r['sn_h2']:+.2f}pp;正的年份 {r['years_positive']}/{r['years']}")
    print(f"  弱−中 逐日平均 {r['wn_mean']:+.2f}pp({r['wn_days']} 天)")
    print(f"  逐年 強−中:{r['yearly_sn']}")
    print(f"  判準 1-4:{r['checks']} → {r['verdict']}")
out = REPO / "ml" / "reports" / "bt_rere_sector_tier.csv"
pd.DataFrame(rows).round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("\n判決(以正式口徑為準):", rows[0]["verdict"], "| 彙總:", out)
