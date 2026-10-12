"""預測結果的「資料日」(as-of)單一來源。

2026-10-10/12 事故:多處程式用排序後的第一列 `date` 代表整批預測的日期。
6173 最後一根K棒停在 10/06,又被排在第一列,於是 10/08 資料的預測被存成
`predictions_2026-10-06.csv`(蓋掉歷史檔)、舊版紙上組合鎖成 2026-10-06 並在 10/07 進場(前視錯誤)、
儀表板找不到有效的 10/08 預測憑證而失敗。

規則:資料日 = 多數列共有的日期;同票數時取較晚者。不用最大日期——單一筆異常的未來日期列
(例如漏網的非交易日假棒)不應該決定整批的日期;不用第一列——排序與停牌股會讓它失真。
"""
from __future__ import annotations

import pandas as pd


def canonical_prediction_date(frame: pd.DataFrame | None, column: str = "date") -> str | None:
    """回傳 YYYY-MM-DD;沒有任何可解析日期時回傳 None(由呼叫端決定要報錯或退回其他來源)。"""
    if frame is None or len(frame) == 0 or column not in frame.columns:
        return None
    dates = pd.to_datetime(frame[column], errors="coerce").dropna()
    if dates.empty:
        return None
    counts = dates.dt.strftime("%Y-%m-%d").value_counts()
    top = counts.max()
    return max(day for day, n in counts.items() if n == top)


def stale_prediction_rows(frame: pd.DataFrame | None, as_of: str | None, column: str = "date",
                          ticker_column: str = "ticker") -> list[str]:
    """資料日與整批資料日不同的個股(多半是停牌或缺資料),供告警列出。"""
    if frame is None or len(frame) == 0 or not as_of or column not in frame.columns:
        return []
    days = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    mask = days.ne(as_of)
    if ticker_column in frame.columns:
        return [f"{t}@{d}" for t, d in zip(frame.loc[mask, ticker_column].astype(str), days[mask].fillna("NaT"))]
    return [str(d) for d in days[mask].fillna("NaT")]
