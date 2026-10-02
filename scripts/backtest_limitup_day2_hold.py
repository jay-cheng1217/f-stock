"""漲停發動(D1)→隔日(D2)型態→D3/之後表現。read-only,只讀日K CSV。
進場=D2 收盤;停損不設;價格未還原(短窗,另剔除疑似除權息跳空)。"""
import glob, os, re, sys
import numpy as np, pandas as pd

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "日K資料")
rows = []
for p in glob.glob(os.path.join(BASE, "*.csv")):
    t = os.path.basename(p)[:-4]
    if not re.fullmatch(r"[1-9]\d{3}", t):
        continue
    try:
        d = pd.read_csv(p, usecols=["Date", "Open", "High", "Low", "Close", "Volume"], encoding="utf-8-sig")
    except Exception:
        continue
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d = d.dropna().sort_values("Date")
    d = d[(d["Date"] >= "2020-01-01") & (d["Date"] <= "2026-09-22")].reset_index(drop=True)
    if len(d) < 80:
        continue
    c, h, l, o, v = (d[k].to_numpy(float) for k in ("Close", "High", "Low", "Open", "Volume"))
    pc = np.roll(c, 1); pc[0] = np.nan
    ret = c / pc - 1
    vol20 = pd.Series(v).rolling(20).mean().shift(1).to_numpy()
    limit_up = (ret >= 0.094) & (ret <= 0.101) & (c >= h * 0.9999)
    n = len(d)
    for i in np.where(limit_up)[0]:
        if i < 21 or i + 7 >= n or not (vol20[i] >= 500_000):
            continue
        if limit_up[i - 1]:          # 只取連續漲停的第一根當 D1
            continue
        j = i + 1                    # D2
        if limit_up[j]:
            grp = "D2續鎖漲停"
        elif l[j] < c[i] and c[j] >= c[i]:
            grp = "A 洗到漲停價下方後收回(她的型態)"
        elif l[j] >= c[i]:
            grp = "C 全日守在漲停價上"
        else:
            grp = "B 收在漲停價下方"
        k = j + 1                    # D3
        r3 = c[k] / c[j] - 1
        if abs(o[k] / c[j] - 1) > 0.11:   # 疑似除權息/資料異常
            continue
        fwd = c[j + 5] / c[j] - 1
        mdd = l[j + 1:j + 6].min() / c[j] - 1
        rows.append((t, d["Date"].iloc[i], grp, r3, bool(limit_up[k]), h[k] / c[j] - 1, fwd, mdd,
                     l[k] >= c[i]))
df = pd.DataFrame(rows, columns=["t", "d1", "grp", "r3", "d3_limit", "d3_high", "r5", "mdd5", "d3_hold"])
print("D1 樣本數:", len(df), "| 期間:", df.d1.min().date(), "~", df.d1.max().date())
out = df.groupby("grp").agg(
    n=("r3", "size"), d3漲停率=("d3_limit", "mean"), d3收盤均=("r3", "mean"), d3收盤中位=("r3", "median"),
    d3上漲率=("r3", lambda s: (s > 0).mean()), d5報酬均=("r5", "mean"), d5中位=("r5", "median"),
    d5勝率=("r5", lambda s: (s > 0).mean()), d5最大回落中位=("mdd5", "median"),
    d5跌逾7pct=("mdd5", lambda s: (s <= -0.07).mean()))
pd.set_option("display.width", 250); pd.set_option("display.unicode.east_asian_width", True)
print(out.assign(**{c: (out[c] * 100).round(2) for c in out.columns if c != "n"}).to_string())
a = df[df.grp.str.startswith("A")]
print("\nA 組再細分:D3 低點是否守住 D1 漲停價")
print(a.groupby("d3_hold").agg(n=("r3", "size"), d3漲停率=("d3_limit", "mean"), d3收盤均=("r3", "mean"),
      d5報酬均=("r5", "mean"), d5勝率=("r5", lambda s: (s > 0).mean())).mul([1, 100, 100, 100, 100]).round(2).to_string())
print("\nA 組逐年 d5:"); print(a.assign(y=a.d1.dt.year).groupby("y").agg(n=("r5", "size"), d5均=("r5", "mean"),
      d5勝率=("r5", lambda s: (s > 0).mean()), d3漲停率=("d3_limit", "mean")).mul([1, 100, 100, 100]).round(2).to_string())
