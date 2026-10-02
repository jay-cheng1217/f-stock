"""A 組:D2 收盤進場,D3~D7 跌破 D1 漲停價即出(跳空則開盤價出),否則 D7 收盤出。含 0.4% 來回成本。"""
import glob, os, re
import numpy as np, pandas as pd
BASE = r"F:\stock\日K資料"; FR = 0.004
res = []
for p in glob.glob(os.path.join(BASE, "*.csv")):
    t = os.path.basename(p)[:-4]
    if not re.fullmatch(r"[1-9]\d{3}", t): continue
    try: d = pd.read_csv(p, usecols=["Date","Open","High","Low","Close","Volume"], encoding="utf-8-sig")
    except Exception: continue
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce"); d = d.dropna().sort_values("Date")
    d = d[(d["Date"] >= "2020-01-01") & (d["Date"] <= "2026-09-22")].reset_index(drop=True)
    if len(d) < 80: continue
    c,h,l,o,v = (d[k].to_numpy(float) for k in ("Close","High","Low","Open","Volume"))
    pc = np.roll(c,1); pc[0]=np.nan; ret = c/pc-1
    vol20 = pd.Series(v).rolling(20).mean().shift(1).to_numpy()
    lu = (ret>=0.094)&(ret<=0.101)&(c>=h*0.9999)
    for i in np.where(lu)[0]:
        if i<21 or i+7>=len(d) or not (vol20[i]>=500_000) or lu[i-1]: continue
        j=i+1
        if lu[j] or not (l[j]<c[i] and c[j]>=c[i]): continue
        if abs(o[j+1]/c[j]-1)>0.11: continue
        ex=None; stopped=False
        for k in range(j+1, j+6):
            if l[k] < c[i]:
                ex = min(o[k], c[i]); stopped=True; break
        if ex is None: ex = c[j+5]
        res.append((d["Date"].iloc[i].year, ex/c[j]-1-FR, c[j+5]/c[j]-1-FR, stopped, c[j]/c[i]-1))
r = pd.DataFrame(res, columns=["y","with_stop","no_stop","stopped","entry_above_key"])
f = lambda s: f"均 {s.mean()*100:+.2f}% | 中位 {s.median()*100:+.2f}% | 勝率 {(s>0).mean()*100:.1f}% | 最差5% {s.quantile(.05)*100:+.2f}%"
print("n =", len(r), "| 5日內被停損比例:", f"{r.stopped.mean()*100:.1f}%", "| 進場價高於關鍵價 中位:", f"{r.entry_above_key.median()*100:.2f}%")
print("有停損(破D1漲停價出):", f(r.with_stop)); print("不停損(抱5日)        :", f(r.no_stop))
print("停損單平均損失:", f"{r[r.stopped].with_stop.mean()*100:+.2f}%", "| 未停損單平均:", f"{r[~r.stopped].with_stop.mean()*100:+.2f}%")
print(r.groupby("y").agg(n=("with_stop","size"), 有停損均=("with_stop","mean"), 不停損均=("no_stop","mean")).mul([1,100,100]).round(2).to_string())
