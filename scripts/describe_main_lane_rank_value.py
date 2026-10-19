# -*- coding: utf-8 -*-
"""主 lane 乾淨度排序的逐日描述(#43 事後描述,非事前登記的判準,不可直接據此改排序)。

#43 的 C 段用逐列平均:候選多的日子權重較大,池平均因此不能直接和前 12 名比。這裡沿用 #43 的重放
(scripts/backtest_main_veto_backfill.build_pool:型態池、20 根口徑、股東報酬還原、減同期流動母體)
先算每日平均再跨日平均,並列乾淨度十分位(逐日分位,0=最乾淨)。只用型態池排序,不含營益率防呆與資料待確認排序。

用法:python -X utf8 scripts/describe_main_lane_rank_value.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import backtest_main_veto_backfill as bt  # noqa: E402  (import 時已把 stdout 包成 UTF-8)


def main() -> None:
    D = bt.build_pool().sort_values(["as_of", "clean"], ascending=[True, False])
    D["rank"] = D.groupby("as_of").cumcount() + 1
    g = D.groupby("as_of")
    daily = pd.DataFrame({"pool": g["ex"].mean(), "top12": D[D["rank"] <= 12].groupby("as_of")["ex"].mean(),
                          "r13_15": D[(D["rank"] > 12) & (D["rank"] <= 15)].groupby("as_of")["ex"].mean(),
                          "bottom12": g.apply(lambda x: x.tail(12)["ex"].mean())})
    daily["year"] = daily.index.str[:4]
    cols = ["pool", "top12", "r13_15", "bottom12"]
    print("逐日平均後再平均(每日等權,20 根超額 pp):")
    print(daily.groupby("year")[cols].mean().round(2).to_string())
    print("全期:", " ".join(f"{k} {v:+.2f}" for k, v in daily[cols].mean().items()), f"| 日數 {len(daily)}")
    diff = daily["top12"] - daily["pool"]
    print(f"前12 − 池(逐日):平均 {diff.mean():+.2f}pp,前12 勝過池的日子 {(diff > 0).mean() * 100:.0f}%")
    D["decile"] = g["clean"].rank(pct=True, ascending=False).mul(10).clip(upper=9.999).astype(int)
    dec = D.groupby(["as_of", "decile"])["ex"].mean().unstack().mean()
    print("乾淨度十分位(0=最乾淨)逐日平均超額:", " ".join(f"{int(k)}:{v:+.2f}" for k, v in dec.items()))


if __name__ == "__main__":
    main()
