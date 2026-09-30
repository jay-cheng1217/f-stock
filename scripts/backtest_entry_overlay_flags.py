# -*- coding: utf-8 -*-
"""BT-entry-overlay-flags(research-only,2026-09 進場卡矛盾訊號裁決票)。

背景:每日進場卡把 20D overlay 層的旗標(法人10日賣壓占量 >20%/>30%、不列入Top30、
V1V2分歧)無差別貼在短打進場區間旁,同一張卡同時說「可以買」與「別買」。
根因(2026-09-04 追查):entry_dashboard.py:642-644 把 champion predictions_*.csv 的
risk_tags 按 ticker 硬 join 到宇宙每一檔,不分 lane;產生器本身從未讀 risk_tags。
SKILL 鐵律:20D 模型是 overlay 不是進場許可;rere lane 更明訂模型不得否決。
本回測回答「法人賣壓旗標對各 lane 的持有期到底有沒有預測力」,據此決定卡片裁決。

假說:
- H1 主 lane(20D):法人10日賣壓占量 >30%(及 >20%)的進場,20日報酬/勝率是否劣於其餘
  → 若顯著且穩定劣化,主 lane 該旗標降 go→觀察;否則只保留為軟註記。
- H2 rere lane(60D 結構):同旗標下 60 日是否不劣(文件:模型不得否決 rere)。

口徑(與 backtest_structure_over_flow.py / 文件基準一致,可直接比較):
- 資料:日K資料/{ticker}.csv(混合價序列,見 STRATEGY_overview §四);4 碼數字股票
- 進場:訊號日次一交易日開盤;f20 = C[pos+21]/entry−1,f60 = C[pos+61]/entry−1;無停損
- 流動性:VOL_MA_20 ≥ 500,000 股;每檔同 lane 持有期內 cooldown(避免重疊樣本灌水)
- 母體(主 lane)= 產生器 `_pattern_ok()` 型態閘門(generate_entry_candidates.py:278-286,
  常數 :48-58,2026-09-04 已逐一確認):貼MA20 −2%~+8%、收>MA60、量比≤2.5、RSI 45~80、
  MACD 柱 3 日變化/收盤 ≥ −1%。註:實際主 lane 還多一層「模型建議買進且 pred>0」,
  該層無法 point-in-time 重算(預測檔僅 2026-03 起),故本母體為型態閘門的超集。
- 母體(rere lane)= `_rere_shakeout_ok()`(:305-314):10日高回落 ≥10% + |貼MA20| ≤5%
  + 量比<1.2 + 收>MA60 + 外資前5日合計<0 且當日>0(由賣轉買)。
- 旗標(卡片精確公式,ml/predict.py:765-776 / ml/features/entry.py:41-68):
  inst_sell_ratio = Σ10日(外資+投信)淨買賣 / Σ10日成交量;≤ −0.20 → 「>20% 偏高」,
  ≤ −0.30 → 「>30% 不列入Top30」。另附外資單獨 / 三大法人 兩版作穩健性對照。
- regime:TWII 收 ≥ 自身 MA60 為多頭(2022 空頭年反轉教訓,分 regime 檢視)

輸出:ml/reports/bt_entry_overlay_flags.csv(逐筆)+ 終端統計。不寫任何生產檔。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
INDEX_TWII = BASE / "大盤指數" / "index_TWII.csv"
OUT = BASE / "ml" / "reports" / "bt_entry_overlay_flags.csv"

# ---- 門檻常數(與產生器對齊;改動請先跑本回測)----
LIQ_MIN_VOL_MA20 = 500_000
MAIN_MA20_LO, MAIN_MA20_HI = -0.02, 0.08     # MA20_LOW / MA20_HIGH
MAIN_VOL_RATIO_MAX = 2.5                     # MAX_VOL_RATIO(NaN 放行,同產生器)
MAIN_RSI_LO, MAIN_RSI_HI = 45.0, 80.0        # RSI_LOW / RSI_HIGH(NaN 放行,同產生器)
MAIN_MACD_FLOOR_REL = -0.010                 # MACD_DELTA_FLOOR_REL(價格相對)
RERE_WASH_MIN = 0.10                         # RERE_WASH_MIN
RERE_MA20_ABS = 0.05                         # RERE_MA20_LOW/HIGH ±5%
RERE_VOL_RATIO_MAX = 1.2                     # RERE_VOL_MAX(蹲點型)
FLAG_SELL20, FLAG_SELL30 = -0.20, -0.30      # STRONG_BUY_MAX_INST_SELL_PCT / TOP30_EXCLUDE_INST_SELL_PCT
HOLD_MAIN, HOLD_RERE = 20, 60

FLAG_VARIANTS = (("r10_ft", "外資+投信(卡片公式)"), ("r10_for", "外資單獨"), ("r10_all", "三大法人"))
PRIMARY = "r10_ft"


def _num(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").values.astype(float)


def _twii_regime() -> tuple[np.ndarray, np.ndarray]:
    """回傳 (d8 陣列, bull 布林陣列):TWII 收盤 ≥ 自身 MA60 視為多頭。"""
    t = pd.read_csv(INDEX_TWII, dtype={"Date": str}).sort_values("Date").reset_index(drop=True)
    c = _num(t["Close"])
    ma60 = pd.Series(c).rolling(60).mean().values
    d8 = t["Date"].astype(str).str[:10].str.replace("-", "").values
    return d8, (c >= ma60)


def _regime_at(twii_d8: np.ndarray, twii_bull: np.ndarray, d8: str) -> float:
    pos = np.searchsorted(twii_d8, d8, side="right") - 1
    return np.nan if pos < 0 else float(bool(twii_bull[pos]))


def _ok_or_nan(cond: np.ndarray, x: np.ndarray) -> np.ndarray:
    """產生器慣例:指標為 NaN 時放行。"""
    return cond | ~np.isfinite(x)


def main() -> int:
    twii_d8, twii_bull = _twii_regime()
    rows: list[dict] = []
    files = sorted(p for p in DAILY.glob("*.csv") if p.stem.isdigit() and len(p.stem) == 4)
    print(f"掃描 {len(files)} 檔 …")
    need = ["Date", "Open", "High", "Close", "Volume", "MA_20", "MA_60", "RSI_14", "MACDh_12_26_9",
            "VOL_MA_20", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]
    for p in files:
        try:
            k = pd.read_csv(p, usecols=lambda c: c in need)
        except Exception:
            continue
        if len(k) < 130 or any(c not in k.columns for c in need):
            continue
        k = k.sort_values("Date").reset_index(drop=True)
        d8 = k["Date"].astype(str).str[:10].str.replace("-", "").values
        o, h, c, v = _num(k["Open"]), _num(k["High"]), _num(k["Close"]), _num(k["Volume"])
        ma20, ma60, rsi, vma = _num(k["MA_20"]), _num(k["MA_60"]), _num(k["RSI_14"]), _num(k["VOL_MA_20"])
        macdh = _num(k["MACDh_12_26_9"])
        fby = np.nan_to_num(_num(k["Foreign_BuySell"]))
        tby = np.nan_to_num(_num(k["Trust_BuySell"]))
        dby = np.nan_to_num(_num(k["Dealer_BuySell"]))
        n = len(k)

        with np.errstate(divide="ignore", invalid="ignore"):
            ma20d = c / ma20 - 1
            vratio = v / vma
            hi10 = pd.Series(h).rolling(10).max().values
            wash10 = c / hi10 - 1
            macd_d3_rel = (macdh - pd.Series(macdh).shift(3).values) / c
            s10 = lambda a: pd.Series(a).rolling(10).sum().values  # noqa: E731
            vol10 = s10(v)
            r_ft = s10(fby + tby) / vol10           # 卡片公式:外資+投信
            r_for = s10(fby) / vol10                # 外資單獨
            r_all = s10(fby + tby + dby) / vol10    # 三大法人
            f5prev = pd.Series(fby).rolling(5).sum().shift(1).values  # 前 5 日外資合計
        turn_buy = (fby > 0) & (f5prev < 0)                        # 由賣轉買(_rere_shakeout_ok)
        liquid = vma >= LIQ_MIN_VOL_MA20
        above60 = c > ma60

        main_sig = (liquid & above60
                    & (ma20d >= MAIN_MA20_LO) & (ma20d <= MAIN_MA20_HI)
                    & _ok_or_nan(vratio <= MAIN_VOL_RATIO_MAX, vratio)
                    & _ok_or_nan((rsi >= MAIN_RSI_LO) & (rsi <= MAIN_RSI_HI), rsi)
                    & _ok_or_nan(macd_d3_rel >= MAIN_MACD_FLOOR_REL, macd_d3_rel))
        rere_sig = (liquid & above60 & (wash10 <= -RERE_WASH_MIN) & (np.abs(ma20d) <= RERE_MA20_ABS)
                    & (vratio < RERE_VOL_RATIO_MAX) & turn_buy)

        for lane, sig, hold in (("main", main_sig, HOLD_MAIN), ("rere", rere_sig, HOLD_RERE)):
            last = -10**9
            for pos in np.where(np.nan_to_num(sig, nan=False))[0]:
                if pos < 60 or pos + 1 >= n or pos - last < hold:   # cooldown = 持有期
                    continue
                entry = o[pos + 1]
                if not (np.isfinite(entry) and entry > 0):
                    continue
                last = pos
                rows.append({
                    "lane": lane, "ticker": p.stem, "date": d8[pos], "year": d8[pos][:4],
                    "bull": _regime_at(twii_d8, twii_bull, d8[pos]),
                    "r10_ft": r_ft[pos], "r10_for": r_for[pos], "r10_all": r_all[pos],
                    "ma20d": ma20d[pos], "vratio": vratio[pos], "rsi": rsi[pos],
                    "f20": c[pos + 21] / entry - 1 if pos + 21 < n else np.nan,
                    "f60": c[pos + 61] / entry - 1 if pos + 61 < n else np.nan,
                })

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"逐筆 → {OUT}({len(df):,} 筆)\n")

    def stats(d: pd.DataFrame, col: str) -> str:
        x = d[col].dropna()
        if not len(x):
            return "n=0"
        return (f"n={len(x):5d} 平均{x.mean()*100:+6.2f}% 中位{x.median()*100:+6.2f}% "
                f"勝率{(x > 0).mean()*100:4.1f}% 左尾<-20%:{(x < -0.2).mean()*100:4.1f}%")

    print("基準(文件同窗口):20日 +1.58%/50.1% | 60日 +5.74%/53.2%")
    for lane, col in (("main", "f20"), ("rere", "f60")):
        d = df[df["lane"] == lane]
        print(f"\n===== {lane} lane(母體 {len(d):,} 筆,看 {col})=====")
        print("  全部          :", stats(d, col))
        for var, lbl in FLAG_VARIANTS:
            for thr, name in ((FLAG_SELL20, ">20%"), (FLAG_SELL30, ">30%")):
                print(f"  [{lbl} 賣壓{name}] 旗標組:", stats(d[d[var] <= thr], col))
                print(f"  [{lbl} 賣壓{name}] 其餘組:", stats(d[d[var] > thr], col))
        for b, nm in ((1.0, "多頭 TWII≥MA60"), (0.0, "空頭 TWII<MA60")):
            dd = d[d["bull"] == b]
            print(f"  {nm} | 卡片公式>30% 旗標組:", stats(dd[dd[PRIMARY] <= FLAG_SELL30], col))
            print(f"  {nm} | 其餘組             :", stats(dd[dd[PRIMARY] > FLAG_SELL30], col))
        print("  逐年(卡片公式>30%:旗標組 vs 其餘組 平均/勝率):")
        for y, g in d.groupby("year"):
            a = g[g[PRIMARY] <= FLAG_SELL30][col].dropna()
            b = g[g[PRIMARY] > FLAG_SELL30][col].dropna()
            if len(a) >= 10 and len(b) >= 10:
                print(f"    {y}: 旗標 n={len(a):4d} {a.mean()*100:+6.2f}%/{(a>0).mean()*100:4.1f}%"
                      f"  其餘 n={len(b):4d} {b.mean()*100:+6.2f}%/{(b>0).mean()*100:4.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
