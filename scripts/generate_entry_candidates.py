# -*- coding: utf-8 -*-
"""Canonical 進場候選產生器 —— Claude 與 Codex 共用的唯一來源。

把 stock-entry-decision-workflow 的順序寫成確定性程式,消除「兩邊各自即興、名單打架」:
  1. 型態硬 gate(先):貼支撐、不追高、不爆量、均線/MACD 沒破、RSI 不過熱。跌破/追高一律不進。
  2. 模型確認:recommendation 含「買進」且 pred_return_20d > 0(賣出/DOWN/pred<0 剔除)。
     dataA 來源沒有 recommendation 防呆層 → 主 lane 另補 CLAUDE.md 規則 11(本業虧損
     OM<0 → 剔除;2026-07-02 澤米 regression 修復,回測 fundamental_veto_backtest 支持;
     rere lane 不適用——轉機型常 OM<0,營收衰退組回測反而更強)。
  3. 籌碼K:只能否決或加分,**不能救型態**(型態 gate 在前,ChipK 之後才作用)。
     - risk_flags / distribution → veto;外資續賣(20日 < -FOREIGN_SELL_LOTS 張)→ 降級小倉。
     - 在 chipk diagnosis 且 confirmed → 正常;不在 diagnosis → 標記「需籌碼K確認」小倉。
  4. 處置/特殊股 gate 關 → 不進。
  5. 排序:型態乾淨度(貼MA20+量縮)為主 → 模型 pred → ChipK 強度(僅 tiebreak)。

輸出 JSON 相容 intraday_quote.py --plan 與 send_entry_list_email.py。

用法:
  python scripts/generate_entry_candidates.py --date 2026-07-02 -o logs/entry_list_20260702.json
  python scripts/generate_entry_candidates.py --dry   # 只印不寫檔
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(BASE_DIR))
from scripts.momentum_continuation_backtest import prepare_stock_frame, annotate_momentum_continuation  # noqa: E402
from scripts.disposition_contract import load_disposition_gate
from scripts.taiwan_trading_calendar import previous_taiwan_trading_day, next_taiwan_trading_day, is_taiwan_trading_day

DAILY_DIR = BASE_DIR / "日K資料"
MODEL_DIR = BASE_DIR / "ml" / "models"
REPORT_DIR = BASE_DIR / "ml" / "reports"
SECTOR_MAP = BASE_DIR / "ml" / "data" / "sector_mapping.csv"

# --- 驗證過的門檻(改前需回測,見 CLAUDE.md)---
MIN_LIQUIDITY_LOTS = 500          # 均量 >= 500 張
MA20_LOW = -0.02                  # 貼MA20下限(-2%,再低=跌破/接刀)
MA20_HIGH = 0.08                  # 貼MA20上限(+8%,再高=追高)
MAX_VOL_RATIO = 2.5              # 量比 >= 2.5 = 爆量,剔除
RSI_LOW, RSI_HIGH = 45, 80        # 過弱/過熱剔除
MACD_DELTA_FLOOR_REL = -0.010     # MACD 3日變化/收盤價 不可崩(價格比口徑)
# 2026-08-28 PM 核准價格比化(原 -1.0 絕對 MACD 單位隨股價縮放:低價股永不觸發、
# 高價股過度觸發)。回測 174,064 樣本依事前判準 PASS,主要收益=解除對 >200 元股的
# 過度封鎖(+2.55%/勝率51.1% vs 共同集+1.16%);切換日名單零翻轉。
# 證據: ml/reports/backtest_macd_price_relative_20260828.md
FOREIGN_SELL_LOTS = 3000          # 外資 20 日淨賣超過此(張)→ 降級


def _latest(pattern: str) -> str | None:
    fs = sorted(glob.glob(str(REPORT_DIR / pattern)) + glob.glob(str(MODEL_DIR / pattern)))
    return fs[-1] if fs else None


def _verified_snapshot() -> pd.DataFrame:
    from ml.snapshot_lineage import load_snapshot
    snapshot = load_snapshot(MODEL_DIR / "snapshot_cache.pkl")
    if snapshot is None:
        raise ValueError("Canonical snapshot lineage rejected or absent")
    return snapshot


def _snapshot_asof_date() -> date | None:
    """Return the certified raw-feature snapshot's actual market date."""
    frame = _verified_snapshot()
    values = pd.to_datetime(frame.get("Date"), errors="coerce")
    if values.notna().any():
        return values.max().date()
    raise ValueError("Canonical snapshot has no valid source date")


def _dated_prediction_paths(prefix: str) -> dict[date, str]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})\.csv$")
    result: dict[date, str] = {}
    for path in MODEL_DIR.glob(f"{prefix}*.csv"):
        match = pattern.fullmatch(path.name)
        if not match:
            continue
        try:
            result[datetime.strptime(match.group(1), "%Y-%m-%d").date()] = str(path)
        except ValueError:
            continue
    return result


def _latest_predictions_path(as_of: date | None = None) -> str | None:
    """模型確認來源。PM 2026-07-02 裁示:日常名單改用新乾淨模型(dataA)+rere lane,
    取代舊 champion 作為主 lane 的模型確認;dataA 檔缺席時 fallback 舊 champion。
    (champion 自動帳本與 pin 不受影響;新舊模型對決帳本照常累積。)"""
    snapshot_date = as_of or _snapshot_asof_date()
    data_a = _dated_prediction_paths("dataA_predictions_")
    champion = _dated_prediction_paths("predictions_")

    if snapshot_date is not None:
        if snapshot_date in data_a:
            return data_a[snapshot_date]
        if snapshot_date in champion:
            return champion[snapshot_date]
        return None

    return None  # 沒有快照日期不能把最新檔誤當同日


def _load_predictions(as_of: date | None = None) -> pd.DataFrame:
    p = _latest_predictions_path(as_of)
    if not p:
        raise ValueError("No prediction artifact matches the required source date")
    from ml.prediction_provenance import load_prediction_csv
    slot = "dataA" if Path(p).name.startswith("dataA_predictions_") else "production"
    loaded = load_prediction_csv(p, expected_model_slot=slot,
                                 read_csv_kwargs={"dtype": {"ticker": str}})
    if loaded is None:
        raise ValueError(f"{slot} prediction lineage rejected or absent: {Path(p).name}")
    d, _ = loaded
    d.columns = [c.strip().lstrip("﻿") for c in d.columns]
    d["ticker"] = d["ticker"].astype(str).str.zfill(4)
    if "recommendation" not in d.columns:
        # dataA 新模型只有迴歸分數:pred>0 視為模型不反對(買進確認)
        d["recommendation"] = np.where(
            pd.to_numeric(d["pred_return_20d"], errors="coerce") > 0, "建議買進(dataA)", "觀望")
    if "source_date" not in d:
        # 當前 cache 不能替舊分數檔補推論當時的證據。逐股 Date 存在才可驗。
        date_column = "Date" if "Date" in d else ("date" if "date" in d else None)
        d["source_date"] = (pd.to_datetime(d[date_column], errors="coerce").dt.strftime("%Y-%m-%d")
                            if date_column else None)
    keep = ["ticker", "pred_return_20d", "recommendation", "source_date"]
    if "operating_margin_latest" in d:
        keep.append("operating_margin_latest")
    return d[keep].assign(src_pred=os.path.basename(p))


def _snapshot_fields(as_of: date | None) -> tuple[dict, dict]:
    """日期與營益率只能取同一原始推論快照；缺值保持 unknown。"""
    frame = _verified_snapshot()
    tickers = frame["ticker"].astype(str).str.zfill(4)
    dates = pd.to_datetime(frame["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    date_map = dict(zip(tickers, dates))
    om = pd.to_numeric(frame.get("operating_margin_latest", pd.Series(np.nan, index=frame.index)), errors="coerce")
    valid = om.notna() & (dates.eq(as_of.isoformat()) if as_of else dates.notna())
    return date_map, dict(zip(tickers[valid], om[valid]))


CHIPK_ARCHIVE_DIR = BASE_DIR / "ml" / "data" / "chipk" / "archive"
CHIPK_DESKTOP_ENABLED_ENV = "CHIPK_DESKTOP_ENABLED"
LEGACY_CHIPK_DESKTOP_ENABLED_ENV = "STOCK_ENABLE_CHIPK_DESKTOP"


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _chipk_desktop_enabled() -> bool:
    if CHIPK_DESKTOP_ENABLED_ENV in os.environ:
        return _env_flag(CHIPK_DESKTOP_ENABLED_ENV)
    return _env_flag(LEGACY_CHIPK_DESKTOP_ENABLED_ENV)


def _load_chipk(as_of: date | None = None) -> pd.DataFrame:
    """優先吃全市場 archive(~2380 檔,全覆蓋);退回 diagnosis(僅模型買進子集,會有
    時序落差:diagnosis 常是用前一天的 predictions 產的)。"""
    if not _chipk_desktop_enabled():
        return pd.DataFrame()
    fs = sorted(glob.glob(str(CHIPK_ARCHIVE_DIR / "chipk_main_force_*.csv")))
    p = fs[-1] if fs else _latest("chipk_model_diagnosis_*.csv")
    if not p:
        return pd.DataFrame()
    if as_of is not None:
        stamp = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", Path(p).name)
        if not stamp or "-".join(stamp.groups()) != as_of.isoformat():
            return pd.DataFrame()  # 過期桌面源不能參與否決或排序
    d = pd.read_csv(p)
    d.columns = [c.strip().lstrip("﻿") for c in d.columns]
    d["ticker"] = d["ticker"].astype(str).str.zfill(4)
    keep = ["ticker", "chipk_bucket", "chipk_main_force_pattern", "chipk_entry_alignment",
            "model_chipk_decision", "chipk_risk_flags", "chipk_trap_risk", "chipk_distribution_risk"]
    d["chipk_src"] = os.path.basename(p)
    return d[[c for c in keep if c in d.columns] + ["chipk_src"]]


def _load_special(target_date: date | None = None, as_of: date | None = None) -> set[str]:
    target_date = target_date or next_taiwan_trading_day(date.today())
    gate = load_disposition_gate(BASE_DIR, target_date=target_date,
                                 required_as_of=as_of or previous_taiwan_trading_day(target_date))
    if not gate["complete"]:
        raise ValueError("處置狀態未知: " + "; ".join(gate["reasons"]))
    return set(gate["blocked_tickers"])


def _sector_map() -> dict[str, str]:
    d = pd.read_csv(SECTOR_MAP)
    d.columns = [c.strip().lstrip("﻿") for c in d.columns]
    return dict(zip(d["Ticker"].astype(str).str.zfill(4), d["Sector"]))


def _name_map() -> dict[str, str]:
    d = pd.read_csv(SECTOR_MAP)
    d.columns = [c.strip().lstrip("﻿") for c in d.columns]
    return dict(zip(d["Ticker"].astype(str).str.zfill(4), d["Name"].astype(str)))


def _technical(ticker: str, as_of: date | None = None) -> dict[str, Any] | None:
    p = DAILY_DIR / f"{ticker}.csv"
    if not p.exists():
        return None
    try:
        raw = prepare_stock_frame(p)
        if as_of is not None and not raw.empty:
            raw = raw[raw["Date"] <= pd.Timestamp(as_of)].copy()
        f = annotate_momentum_continuation(raw)
    except Exception:
        return None
    if f.empty or len(f) < 60:
        return None
    r = f.iloc[-1]
    vol = pd.to_numeric(f.get("Volume"), errors="coerce")
    if vol.tail(20).isna().any() or vol.tail(20).mean() < MIN_LIQUIDITY_LOTS * 1000:
        return None
    close = float(r["Close"])
    v20 = r.get("price_vs_ma20"); v60 = r.get("price_vs_ma60")
    if pd.isna(v20) or pd.isna(v60):
        return None
    ma20 = close / (1 + v20)
    fb_raw = pd.to_numeric(f.get("Foreign_BuySell"), errors="coerce")
    # 2026-09-23 審查修正:法人缺值(NaN)≠真實零買賣超。近6日(今日+前5日=外資轉買
    # 判定視窗)有缺值時記 gap 天數,rere lane 據此標「待確認」——不改門檻,只補判別。
    inst_gap_days = int(fb_raw.tail(6).isna().sum()) if fb_raw is not None else 6
    fb = fb_raw.fillna(0) / 1000
    tb = pd.to_numeric(f.get("Trust_BuySell"), errors="coerce").fillna(0) / 1000
    cl = pd.to_numeric(f["Close"], errors="coerce")
    # 10 日高要在公司行動連續基準上取:面額變更/分割/減資前的原始收盤若不換算,會算出假洗盤
    # (6949 於 2026-09-07 面額變更 1490→74.5,原始口徑 wash=2639%)。現金除息的 price_factor=1,不受影響。
    hi10 = float(_action_adjusted_close(f["Date"], cl, ticker).rolling(10).max().iloc[-1])
    vol_ma20 = vol.rolling(20).mean()
    vr_today = float(vol.iloc[-1] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] else np.nan
    prev_close = float(cl.iloc[-2]) if len(cl) >= 2 else close
    prev_v20 = f.get("price_vs_ma20")
    prev_below_ma20 = bool(pd.notna(prev_v20.iloc[-2]) and float(prev_v20.iloc[-2]) < 0) if hasattr(prev_v20, "iloc") and len(prev_v20) >= 2 else False
    return {
        "signal_date": pd.Timestamp(r["Date"]).date().isoformat(),
        "inst_gap_days": inst_gap_days,
        "close": close, "v20": float(v20), "v60": float(v60),
        "rsi": float(r.get("RSI_14")) if pd.notna(r.get("RSI_14")) else np.nan,
        "vol_ratio": float(r.get("volume_ratio_20d")) if pd.notna(r.get("volume_ratio_20d")) else np.nan,
        "macd_delta_rel": (float(r.get("macd_hist_delta_3d")) / close
                           if (pd.notna(r.get("macd_hist_delta_3d")) and close > 0) else np.nan),
        "ma20": ma20,
        "elow": float(r.get("momentum_entry_low")) if pd.notna(r.get("momentum_entry_low")) else np.nan,
        "ehigh": float(r.get("momentum_entry_high")) if pd.notna(r.get("momentum_entry_high")) else np.nan,
        "foreign20": float(fb.tail(20).sum()),
        "vol_today_lots": float(vol.iloc[-1] / 1000),      # 當日量(張),規則7雙量檢查用
        # rere lane 專用(基準 2026-07-15 重定:60日 +4.58%/勝率35.8%(2026-07-15 重定:停損+除息還原,backtest_rere_lane_baseline.py;舊+9.40%/50.6%為無停損legacy))
        "wash_from_hi10": float(hi10 / close - 1.0),      # 近10日高點殺下來的幅度
        "vr_today": vr_today,                              # 今日量/20日均量
        "foreign_prev5": float(fb.iloc[-6:-1].sum()),      # 前5日外資
        "foreign_today": float(fb.iloc[-1]),               # 今日外資
        # 發動型專用(BT-ignition-day:發動日+投信同買 60日+8.79%/勝率50%,電子科技類53-57%)
        "trust_today": float(tb.iloc[-1]),                 # 今日投信
        "day_ret": float(close / prev_close - 1.0) if prev_close else 0.0,
        "prev_below_ma20": prev_below_ma20,
    }


_PRICE_CALENDAR: pd.DataFrame | None = None


def _action_adjusted_close(dates: pd.Series, close: pd.Series, ticker: str,
                           calendar: pd.DataFrame | None = None) -> pd.Series:
    """原始收盤換到最後一列的價格基準(只處理 price_factor≠1 的公司行動);失敗時回原始值。"""
    global _PRICE_CALENDAR
    try:
        from ml.corporate_actions import backward_adjustment_multiplier, load_price_continuity_calendar
        if calendar is None:
            if _PRICE_CALENDAR is None:
                _PRICE_CALENDAR = load_price_continuity_calendar()
            calendar = _PRICE_CALENDAR
        return close * backward_adjustment_multiplier(dates, str(ticker), calendar)
    except Exception:
        return close


def _pattern_ok(t: dict[str, Any]) -> bool:
    """型態硬 gate:貼支撐、不追高、不爆量、RSI 正常、MACD 沒崩、趨勢在 MA60 上。"""
    return (
        (MA20_LOW <= t["v20"] <= MA20_HIGH)
        and (t["v60"] > 0)  # 收在 MA60 上(長期趨勢向上)
        and (np.isnan(t["vol_ratio"]) or t["vol_ratio"] <= MAX_VOL_RATIO)
        and (np.isnan(t["rsi"]) or (RSI_LOW <= t["rsi"] <= RSI_HIGH))
        and (t["macd_delta_rel"] >= MACD_DELTA_FLOOR_REL)
    )


# --- rere lane 門檻(基準 2026-07-15 重定:60日 +4.58%/勝率35.8%(2026-07-15 重定:停損+除息還原,backtest_rere_lane_baseline.py;舊+9.40%/50.6%為無停損legacy);改前需重測)---
RERE_WASH_MIN = 0.10        # 近10日高點殺下來 >= 10%(深洗盤)
RERE_WASH_SHALLOW = 0.08    # 淺洗盤型下限(2026-09-24 PM 核准,BT-rere-shallow-wash 背書)
RERE_MA20_LOW, RERE_MA20_HIGH = -0.05, 0.05   # 貼MA20 ±5%
RERE_VOL_MAX = 1.2          # 蹲點型:今日量/20日均量 < 1.2(量縮)
RERE_IGNITE_VOL = 1.5       # 發動型:今日量/20日均量 >= 1.5(放量,BT-ignition-day)
RERE_MAX = 6                # 現行三型呈現上限(2026-07-06 起)
RERE_V2_MAX = 6             # v2 兩型另計上限(2026-10-07 起,並行驗證);兩型平分,見 _pick_rere_v2
MAIN_MAX = 12               # 主 lane 呈現上限
RERE_PTYPE_LABEL = {"ignition": "發動型", "shallow": "淺洗盤型", "shakeout": "蹲點型",
                    "shakeout_v2": "蹲點型v2", "shallow_v2": "淺洗盤型v2"}


def _twii_above_ma60(as_of: date | None = None) -> bool | None:
    """regime 閘:加權指數是否站上 60 日線(2022 空頭年 rere 勝率掉到 31%)。"""
    try:
        frame = pd.read_csv(BASE_DIR / "大盤指數" / "index_TWII.csv", usecols=["Date", "Close"])
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        if as_of:
            frame = frame[frame["Date"] <= pd.Timestamp(as_of)]
            if frame.empty or frame["Date"].max().date() != as_of:
                return None
        c = frame.sort_values("Date")["Close"].astype(float)
        if len(c) < 60 or c.tail(60).isna().any():
            return None
        return bool(c.iloc[-1] > c.tail(60).mean())
    except Exception:
        return None  # 未知不偽裝多頭


def _rere_shakeout_ok(t: dict[str, Any]) -> bool:
    """rere 蹲點型(原型):深洗盤後貼支撐 + 量縮 + 外資由賣轉買 + 趨勢仍在。
    基準 2026-07-15 重定:60日 +4.58%/勝率35.8%(停損+除息還原)。模型不得 pred 否決。"""
    return (
        (t["wash_from_hi10"] >= RERE_WASH_MIN)
        and (RERE_MA20_LOW <= t["v20"] <= RERE_MA20_HIGH)
        and (not np.isnan(t["vr_today"]) and t["vr_today"] < RERE_VOL_MAX)
        and (t["v60"] > 0)
        and (t["foreign_prev5"] < 0 and t["foreign_today"] > 0)
    )


def _rere_ignition_ok(t: dict[str, Any]) -> bool:
    """rere 發動型(2026-07-06 南茂教訓,BT-ignition-day 背書):
    洗盤後「放量站回MA20」發動第一天 + 外資轉買 + 投信同日買。
    回測:發動日單獨勝率44%(靠停損),+投信同買=50.0%/60日+8.79%/中位轉正。
    她 7/2 買南茂即此型(系統原 lane 只認量縮蹲點故漏掉)。停損不可省。"""
    return (
        (t["wash_from_hi10"] >= RERE_WASH_MIN)
        and (t["v60"] > 0)
        and (not np.isnan(t["vr_today"]) and t["vr_today"] >= RERE_IGNITE_VOL)  # 放量(與蹲點相反)
        and (t["close"] > t["ma20"] and (t["prev_below_ma20"] or t["day_ret"] > 0.02))  # 站回MA20
        and (t["foreign_prev5"] < 0 and t["foreign_today"] > 0)   # 外資轉買
        and (t["trust_today"] > 0)                                # 投信同日買(edge 關鍵味)
    )


def _rere_shallow_ok(t: dict[str, Any]) -> bool:
    """rere 淺洗盤型(2026-09-24 PM 核准,BT-rere-shallow-wash 背書):
    8%<=洗盤<10%,其餘條件與蹲點型完全一致。動機:台表科 6278 差 0.25-2pp
    邊界 miss + 鈦昇 8027 原型戰役(2020 四次進場 8.7-9.8% 全在此帶,+19~40%)。
    全歷史 11,035 筆:BAND +2.82%/勝率34.6%/左尾5.6%,不遜主體(+2.88%/33.1%/
    左尾10.2%)且逐年無崩塌。不掛投信條件——鈦昇 16 次訊號投信全 0,
    「買在投信前」是她的核心 edge,+投信會系統性漏掉原型。"""
    return (
        (RERE_WASH_SHALLOW <= t["wash_from_hi10"] < RERE_WASH_MIN)
        and (RERE_MA20_LOW <= t["v20"] <= RERE_MA20_HIGH)
        and (not np.isnan(t["vr_today"]) and t["vr_today"] < RERE_VOL_MAX)
        and (t["v60"] > 0)
        and (t["foreign_prev5"] < 0 and t["foreign_today"] > 0)
    )


def _rere_shakeout_v2_ok(t: dict[str, Any]) -> bool:
    """蹲點型 v2(2026-10-07 PM 核可,BT-rere-no-foreign-turn 12/12 PASS):
    與蹲點型完全相同,但**不要求**「外資前5日賣→當日買」。該條件經 ablation(#8)證明邊際貢獻 0,
    來源釐清確認是 6/23 我方翻譯時加的、不是她的規則;台表科 9/16-17 正是只卡此條件而漏掉。
    僅在現行蹲點型不成立時才回此標籤(帳本 subtype 分開累積,現行 forward cohort 零變動)。"""
    return (
        (t["wash_from_hi10"] >= RERE_WASH_MIN)
        and (RERE_MA20_LOW <= t["v20"] <= RERE_MA20_HIGH)
        and (not np.isnan(t["vr_today"]) and t["vr_today"] < RERE_VOL_MAX)
        and (t["v60"] > 0)
    )


def _rere_shallow_v2_ok(t: dict[str, Any]) -> bool:
    """淺洗盤型 v2:8%<=洗盤<10%,其餘同蹲點型 v2(不要求外資拐點)。同票 PASS 6/6。"""
    return (
        (RERE_WASH_SHALLOW <= t["wash_from_hi10"] < RERE_WASH_MIN)
        and (RERE_MA20_LOW <= t["v20"] <= RERE_MA20_HIGH)
        and (not np.isnan(t["vr_today"]) and t["vr_today"] < RERE_VOL_MAX)
        and (t["v60"] > 0)
    )


RERE_V2_PTYPES = ("shakeout_v2", "shallow_v2")


def _rere_lane_ok(t: dict[str, Any]) -> str | None:
    """回進場型態標籤:'shakeout'/'ignition'/'shallow'/'shakeout_v2'/'shallow_v2'/None。
    先判現行三型(標籤與既有 forward cohort 一致),都不成立才判 v2(= 只差外資拐點的那批)。"""
    if _rere_shakeout_ok(t):
        return "shakeout"
    if _rere_ignition_ok(t):
        return "ignition"
    if _rere_shallow_ok(t):
        return "shallow"
    if _rere_shakeout_v2_ok(t):
        return "shakeout_v2"
    if _rere_shallow_v2_ok(t):
        return "shallow_v2"
    return None


def _night_session_gap(timeout: float = 8.0) -> dict[str, Any] | None:
    """開盤 gap 情境提示(非交易訊號),失敗回 None 不影響名單。

    重要教訓(2026-07-02):TAIFEX OpenAPI 的「盤後」列歸屬**當日交易日**,即
    Date=D 的盤後 = D-1 15:00 → D 05:00 那節;昨晚(給明天用)的夜盤要等
    Date=明天 的資料,盤前拿不到 → 該 API 不能當「最新夜盤」用,曾因此給出
    錯誤的「溫和」提示而實際大跳空 -2.4%。

    改法:盤中(TWII 有即時價)直接用加權指數 vs 昨收的真實 gap;盤前拿不到
    新鮮資料就誠實回報「無最新夜盤資料」,絕不用舊夜盤冒充。"""
    if not is_taiwan_trading_day(date.today()):
        return None
    try:
        import requests
        # 即時加權指數(MIS):z=現值, y=昨收 → 真實開盤 gap,永遠新鮮
        r = requests.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": "tse_t00.tw", "json": "1"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"},
            timeout=timeout,
        )
        m = (r.json().get("msgArray") or [{}])[0]
        z = m.get("z"); y = m.get("y")
        cur = float(z) if z not in (None, "-", "") else None
        prev = float(y) if y not in (None, "-", "") else None
        if cur and prev:
            gap = cur / prev - 1.0
            if gap >= 0.01:
                hint = f"大盤 {cur:.0f} vs 昨收 {prev:.0f}({gap*100:+.1f}%)→ 跳空開高日,嚴守不追價上緣"
            elif gap <= -0.01:
                hint = f"大盤 {cur:.0f} vs 昨收 {prev:.0f}({gap*100:+.1f}%)→ 跳空下殺日,防破支撐,先觀望15-30分"
            else:
                hint = f"大盤 {cur:.0f} vs 昨收 {prev:.0f}({gap*100:+.1f}%)→ 溫和,照計畫執行"
            return {"source": "twii_realtime", "cur": cur, "prev_close": prev,
                    "gap_pct": round(gap * 100, 2), "hint": hint, "time": m.get("t")}
        return {"source": "none", "hint": "無最新夜盤/即時大盤資料(盤前),開盤先看 gap 再執行", "gap_pct": None}
    except Exception:
        return None


WATCHLIST_STALE_TOL = 0.12  # 現價偏離觀察區間 >12% 視為過期(2026-08 PM 核定)
WATCHLIST_STOP_BREACH_PREFIX = "🔴已破停損"  # 破線失效訊息前綴(prune 判定共用,勿改字面)
WATCHLIST_MAX_AGE_DAYS = 30  # 手寫觀察條目超過 N 天未重估即剔除歸檔(2026-09 PM 裁示:30 天直接剔除)
WATCHLIST_AGE_EXPIRED_PREFIX = "🔴已逾期未重估"  # 時間過期訊息前綴(prune 判定共用,勿改字面)


def _watchlist_age_msg(reviewed_at: str | None, today: "date | None" = None) -> str:
    """手動 watchlist 條目「時間過期」判定(純函式,與 _watchlist_stale_msg 同設計)。

    價格判定抓不到「現價剛好還在區間內、但筆記已兩個月」的條目(原相 2026-09 事故:
    區間 188-193 仍有效,筆記卻停在 08-27,資訊早已不足判斷)。reviewed_at 為最後重估日
    (YYYY-MM-DD);超過 WATCHLIST_MAX_AGE_DAYS 回傳剔除訊息,否則回空。缺日期或格式錯
    回空——由呼叫端補戳當日,不在此判定。
    """
    try:
        seen = datetime.strptime(str(reviewed_at), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ""
    age = ((today or datetime.now().date()) - seen).days
    if age > WATCHLIST_MAX_AGE_DAYS:
        return (f"{WATCHLIST_AGE_EXPIRED_PREFIX}:最後重估{seen:%Y-%m-%d},已{age}天"
                f"(>{WATCHLIST_MAX_AGE_DAYS}天),資訊不足判斷;重估後更新 reviewed_at 再上卡")
    return ""


def _watchlist_stale_msg(close: float, zlo: float, zhi: float, stop: float = 0.0) -> str:
    """手動 watchlist 觀察區間過期/失效判定(純函式,便於測試與調閾值)。

    只累加不失效的手動 watchlist 會停在數週前的判斷(原相事故:現價 198 卻仍標
    「等回檔 228-232」,方向矛盾)。現價偏離區間 >WATCHLIST_STALE_TOL 即回傳過期訊息;
    在容忍區內回空字串。close 為 NaN 或 zlo<=0 時不判定(回空)。

    2026-08-27 稽核補強:破停損必須優先於容忍帶判定——界霖收 84 已破自訂 stop 88,
    但 84 vs 區間下緣 92 僅 -8.7% 未達 12% 容忍帶,舊版仍顯示「區間仍有效」。
    價格低於失效線 = setup 已失敗(skill:跌破支撐不是更便宜),不論容忍帶。
    """
    if not (close == close and zlo > 0):  # NaN-safe
        return ""
    if stop and stop > 0 and close < stop:
        return (f"{WATCHLIST_STOP_BREACH_PREFIX}:現價{close:.0f}低於失效線{stop:.0f}"
                f"({(close / stop - 1) * 100:.0f}%),setup已失敗;站回{stop:.0f}前不得視為有效觀察")
    if close < zlo * (1 - WATCHLIST_STALE_TOL):
        return (f"⚠️已過期:現價{close:.0f}低於區間下緣{zlo:.0f} "
                f"{(close / zlo - 1) * 100:.0f}%(需大漲才回檔,方向矛盾),請重新評估")
    if close > zhi * (1 + WATCHLIST_STALE_TOL):
        return (f"⚠️已過期:現價{close:.0f}高於區間上緣{zhi:.0f} "
                f"+{(close / zhi - 1) * 100:.0f}%(setup 早該追),請重新評估")
    return ""


def generate(as_of: str | None = None, *, trade_date: str | None = None,
             persist_watchlist: bool = False, include_live: bool = True) -> dict[str, Any]:
    """只用指定收盤日；研究/預覽預設不寫 watchlist。歷史回測另用獨立回測器。"""
    target = pd.Timestamp(trade_date).date() if trade_date else next_taiwan_trading_day(date.today())
    required = pd.Timestamp(as_of).date() if as_of else previous_taiwan_trading_day(target)
    if not is_taiwan_trading_day(target) or required != previous_taiwan_trading_day(target):
        raise ValueError("交易日與來源收盤日不一致")
    pred = _load_predictions(required)
    chipk = _load_chipk(required)
    special_gate = load_disposition_gate(BASE_DIR, target_date=target, required_as_of=required)
    special = set(special_gate["blocked_tickers"])
    sect = _sector_map()
    ck_by = {r["ticker"]: r.to_dict() for _, r in chipk.iterrows()} if not chipk.empty else {}

    # 主 lane 模型層(2026-10-07 PM 核可,BT-main-lane-ranking 4/4 PASS):模型**不再過濾、不再排序**,
    # pred20 只作顯示欄位。回測:同一型態池上,模型過濾+分數挑出的是池內最差一群(5/18~9/2:LIVE −1.34%/32%
    # vs 不用模型 +0.72%/41.7% vs 池等權 −0.13%)。舊規則(模型不反對 → clean+pred20 排序)改為影子名單
    # `shadow_legacy_main`,由 entry_filter_tracker 記入獨立帳本並行觀察 ≥60 日。
    buy = pred.copy()

    snapshot_dates, om_map = _snapshot_fields(required)
    if "operating_margin_latest" in pred:
        valid = pred["source_date"].eq(required.isoformat()) & pred["operating_margin_latest"].notna()
        om_map = dict(zip(pred.loc[valid, "ticker"], pred.loc[valid, "operating_margin_latest"]))
    # 財報由同日原始推論快照取值，避免 DB 最新季与旧模型 frame 混用。
    rows = []
    for _, pr in buy.iterrows():
        tk = pr["ticker"]
        if tk in special:
            continue
        om = om_map.get(str(tk).zfill(4))
        if om is not None and om < 0:
            continue  # OM<0 → 觀望(恢復 recommendation 防呆層;dataA 檔沒帶此層)
        t = _technical(tk, required)
        if t is None:
            continue
        if not _pattern_ok(t):
            continue  # 型態不過 → 直接不進(ChipK 救不了)
        ck = ck_by.get(tk, {})
        risk = str(ck.get("chipk_risk_flags") or "").strip()
        decision = str(ck.get("model_chipk_decision") or "")
        bucket = str(ck.get("chipk_bucket") or "")
        # ChipK veto
        if risk and risk.lower() not in ("nan", "none", ""):
            continue  # 有 risk flag → veto
        trap = str(ck.get("chipk_trap_risk") or "").strip().lower()
        dist = str(ck.get("chipk_distribution_risk") or "").strip().lower()
        if trap in ("1", "true", "yes", "high") or dist in ("1", "true", "yes", "high"):
            continue  # 隔日沖陷阱 / 出貨壓力 → veto
        if "reject" in decision.lower() or "avoid" in decision.lower():
            continue
        # 分類(籌碼K已退役:無新鮮資料時不得以缺籌碼降級——skill 規範
        # stale 檔不得 rescue/rank/block/degrade;啟用且新鮮時才參與分級)
        pred20 = float(pd.to_numeric(pr["pred_return_20d"], errors="coerce"))
        model_buy = ("買進" in str(pr.get("recommendation", ""))) and np.isfinite(pred20) and pred20 > 0
        chipk_active = bool(ck_by)
        if t["foreign20"] < -FOREIGN_SELL_LOTS:
            kind, status = "small", "小倉·盯外資(外資續賣)"
        elif not chipk_active:
            kind, status = "go", "可小倉觸發"
        elif not ck:
            kind, status = "small", "小倉·需籌碼確認"
        elif "confirmed" in decision.lower() or bucket in ("strong", "supportive"):
            kind, status = "go", "可小倉觸發"
        else:
            kind, status = "small", "小倉·籌碼中性"
        # 規則7雙量檢查(2026-07-03 回測:當日量<500張組 60d mean 2.61% vs 3.66%,勝率-1pp;
        # 量縮本是主 lane setup,故降級標注而非硬刪——執行風險靠盤中觸發+小倉控管)
        low_dayvol = t.get("vol_today_lots", np.nan) < MIN_LIQUIDITY_LOTS
        if low_dayvol and kind == "go":
            kind = "small"
            status = f"小倉·當日量{t['vol_today_lots']:.0f}張<500(留意出場流動性)"
        elif low_dayvol:
            status += f"·當日量{t['vol_today_lots']:.0f}張<500"
        elow = t["elow"] if not np.isnan(t["elow"]) else round(t["close"] * 0.985, 2)
        ehigh = t["ehigh"] if not np.isnan(t["ehigh"]) else round(t["close"] * 1.015, 2)
        if elow > ehigh:
            elow, ehigh = ehigh, elow  # 防護:momentum entry 欄位偶發反轉(4721 案例)
        stop = round(t["ma20"] * 0.96, 2)
        # 型態乾淨度分:貼MA20 甜蜜點(~+4%)近者佳、量縮者佳、ChipK strong 加分;pred20 不入分(見上)
        clean = -abs(t["v20"] - 0.04) - 0.1 * (t["vol_ratio"] if not np.isnan(t["vol_ratio"]) else 1.5)
        chip_bonus = {"strong": 0.03, "supportive": 0.015}.get(bucket, 0.0)
        score = clean + chip_bonus
        rows.append({
            "ticker": tk, "sector": sect.get(tk, ""), "close": t["close"], "v20": t["v20"],
            "source_date": t["signal_date"], "model_source_date": pr.get("source_date"),
            "data_warnings": (["本業營益率缺資料，待確認"] if om is None else [])
                + (["技術指標缺資料，待確認"] if not all(np.isfinite(t[k]) for k in ("vol_ratio", "rsi", "macd_delta_rel")) else []),
            "vol_ratio": t["vol_ratio"], "rsi": t["rsi"], "foreign20": t["foreign20"],
            "pred20": pred20, "model_buy": model_buy, "bucket": bucket, "decision": decision,
            "kind": kind, "status": status, "zone_low": round(elow, 2), "zone_high": round(ehigh, 2),
            "stop": stop, "score": score, "clean": clean, "chip_bonus": chip_bonus,
        })

    for row in rows:
        _apply_data_quality(row, required, special_gate, None)
    shadow_legacy_main = _legacy_main_rank(rows)  # 舊規則影子名單(2026-10-07 前的正式規則)
    rows.sort(key=lambda x: (x["kind"] == "watch", -x["score"]))
    rows = rows[:MAIN_MAX]  # 主 lane 呈現上限(純呈現,非訊號變更)

    # --- rere lane(第二獵場):掃全 universe,模型不得用 pred 否決 ---
    # 基準 2026-07-15 重定:60日 +4.58%/勝率35.8%(停損+除息還原;舊9.4%為無停損legacy)。
    # 案例:合晶 6182 於 78 元符合此 lane,模型全程判強力賣出,其後 +73%。
    # 動態產業強度(取代寫死產業;產業會輪動,系統自動跟)+ regime 閘
    try:
        sec_strength = json.loads((BASE_DIR / "ml" / "data" / "sector_strength.json").read_text(encoding="utf-8"))
    except Exception:
        sec_strength = {}
    twii_bull = _twii_above_ma60(required)  # 未知不可冒充多頭；不改 rere predicate

    seen = {r["ticker"] for r in rows if r["kind"] in ("go", "small")}
    rere_rows: list[dict[str, Any]] = []
    universe = pred["ticker"].dropna().unique().tolist() if not pred.empty else []
    for tk in universe:
        tk = str(tk).zfill(4)
        if tk in seen or tk in special:
            continue
        t = _technical(tk, required)
        if t is None:
            continue
        ptype = _rere_lane_ok(t)
        if ptype is None:
            continue
        ck = ck_by.get(tk, {})
        risk = str(ck.get("chipk_risk_flags") or "").strip()
        if risk and risk.lower() not in ("nan", "none", ""):
            continue  # 籌碼硬風險仍可 veto
        trap = str(ck.get("chipk_trap_risk") or "").strip().lower()
        dist = str(ck.get("chipk_distribution_risk") or "").strip().lower()
        if trap in ("1", "true", "yes", "high") or dist in ("1", "true", "yes", "high"):
            continue
        bucket = str(ck.get("chipk_bucket") or "")
        pr = pred[pred["ticker"] == tk]
        pred20 = float(pr["pred_return_20d"].iloc[0]) if len(pr) else np.nan
        ma60 = t["close"] / (1 + t["v60"])
        sec_name = sect.get(tk, "")
        tier = (sec_strength.get(sec_name) or {}).get("tier", "neutral")
        if ptype == "ignition":
            # 發動型:進場區間貼發動當日(她 7/2 買南茂發動日,不等回檔)
            elow = round(t["ma20"] * 0.99, 2)
            ehigh = round(t["close"] * 1.005, 2)
            ptxt = "發動型"
        else:
            # 蹲點型/淺洗盤型(含 v2)共用支撐區間(差別只在洗盤深度帶與是否要求外資拐點)
            elow = round(t["ma20"] * 0.98, 2)
            ehigh = round(t["close"] * 1.015, 2)
            ptxt = RERE_PTYPE_LABEL.get(ptype, "蹲點型")
        if elow > ehigh:
            elow, ehigh = ehigh, elow
        tier_txt = {"strong": "·族群強勢", "weak": "·族群弱勢(降級)", "neutral": ""}[tier]
        regime_txt = "·大盤資料待確認" if twii_bull is None else ("" if twii_bull else "·大盤弱(減規模)")
        rere_rows.append({
            "ticker": tk, "sector": sec_name, "close": t["close"], "v20": t["v20"],
            "source_date": t["signal_date"],
            "model_source_date": snapshot_dates.get(tk),
            "vol_ratio": t["vr_today"], "rsi": t["rsi"], "foreign20": t["foreign20"],
            "pred20": pred20, "bucket": bucket, "decision": "rere_lane",
            "kind": "small", "status": f"rere·{ptxt}·小倉(60日,配停損){tier_txt}{regime_txt}",
            "zone_low": elow, "zone_high": ehigh,
            "stop": round(ma60 * 0.97, 2),
            # 排序:族群強勢優先(輪動當紅)→ 洗盤深度;弱勢族群自動沉底但不封殺
            "score": (2 if tier == "strong" else (0 if tier == "weak" else 1)) + t["wash_from_hi10"],
            "lane": "rere", "ptype": ptype, "sec_tier": tier,
            "wash": t["wash_from_hi10"],
            # 法人缺值≠零(2026-09-23 審查):轉買判定視窗有缺日 → data_warnings
            # 走既有認證流程降為「資料待確認」,門檻本身不動。v2 不用外資條件,不掛此警示。
            "data_warnings": ([f"法人買賣超近6日缺{t['inst_gap_days']}日,外資轉買判定待確認"]
                              if (t.get("inst_gap_days") and ptype not in RERE_V2_PTYPES) else []),
        })
    rere_rows.sort(key=lambda x: x["score"], reverse=True)
    # rere lane 卡片排主 lane 前面(2026-08-31 用戶指示:南亞 1303 案例 priority 16
    # 沉在名單後段,實戰有效的 lane 不該被淹沒)。純呈現順序,非訊號變更。
    # v2 子型態另計上限,不佔現行三型的 6 個名額(現行 forward cohort 零變動)。
    chosen_rere = ([r for r in rere_rows if r["ptype"] not in RERE_V2_PTYPES][:RERE_MAX]
                   + _pick_rere_v2(rere_rows))
    rere_tickers = {r["ticker"] for r in chosen_rere}
    rows = chosen_rere + [r for r in rows if r["ticker"] not in rere_tickers]

    # --- MOPS 官方訊號 overlay(2026-07-02):警示=veto、法說=標註 ---
    # 警示類(董監轉讓/私募/增減資)=官方事實+警訊不對稱 → 自動剔除免回測
    # (與 chipk risk flag 同級);法說=行事曆標註不動排名;新聞「熱度」未回測不進 gate。
    try:
        mops_files = sorted((BASE_DIR / "新聞資料").glob("announcements_*.csv"))[-2:]
        mops = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in mops_files], ignore_index=True)
        mops["Ticker"] = mops["Ticker"].str.zfill(4)
        cutoff = (pd.Timestamp(required) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        mops = mops[(mops["Date"].astype(str) >= cutoff) & (mops["Date"].astype(str).str[:10] <= required.isoformat())]
        kw_alert = ["持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資"]  # 收窄:裸「董事」會誤殺例行董事會公告
        kw_cal = ["法人說明會", "法說"]
        for r in rows:
            mine = mops[mops["Ticker"] == r["ticker"]]
            if not len(mine):
                continue
            titles = " ".join(mine["Title"].astype(str))
            alert_titles = [x for x in mine["Title"].astype(str)
                            if any(k in x for k in kw_alert) and "庫藏股" not in x]
            if alert_titles:
                r["kind"] = "veto"
                r["status"] = "⚠️官方警示·剔除(3日內董監/私募/增資公告)"
                r["score"] = -9999
            elif any(k in titles for k in kw_cal):
                r["status"] = str(r["status"]) + "·📅法說在即"
        # 排序:veto 卡全域沉底 → rere lane 置頂(用戶 2026-08-31 指示)→ 主 lane,組內按分數
        rows.sort(key=lambda x: (
            1 if x.get("kind") == "veto" else 0,
            0 if x.get("lane") == "rere" else 1,
            -x["score"],
        ))
    except Exception:
        pass  # MOPS 檔缺失不影響名單

    # (watchlist stale 判定抽成模組層純函式 _watchlist_stale_msg,見檔案上方)

    # --- watchlist carryover(2026-07-02 南茂教訓):---
    # 「等回檔」股用 EOD 資料會被隔日名單丟掉,盤中回踩到區間就沒人盯
    # (南茂 07-01 標「等回103-105」,07-02 盤中真回踩,rere 買在 102.5-105 當日 +6.8%)。
    # 手動維護 logs/entry_watchlist.json:[{ticker, zone_low, zone_high, stop, note}]
    # 每日名單自動帶上(kind=watch),intraday --plan 就會盯到回踩觸發。
    wl_path = BASE_DIR / "logs" / "entry_watchlist.json"
    if wl_path.exists():
        try:
            wl_entries = json.loads(wl_path.read_text(encoding="utf-8"))
            survivors: list[dict[str, Any]] = []
            pruned: list[dict[str, Any]] = []
            wl_dirty = False  # 補戳 reviewed_at 後需回寫 entry_watchlist.json
            for w in wl_entries:
                tk = str(w.get("ticker", "")).zfill(4)
                # 補戳最後重估日:手寫條目沒填 reviewed_at 時以首次見到日為準並回寫,
                # 之後才有時間過期依據(2026-09:原相筆記兩個月仍上卡,價格判定抓不到)。
                if not w.get("reviewed_at"):
                    w["reviewed_at"] = datetime.now().strftime("%Y-%m-%d")
                    wl_dirty = True
                if not tk.isdigit() or tk in {r["ticker"] for r in rows}:
                    survivors.append(w)
                    continue
                # 時間過期優先於價格判定:逾 WATCHLIST_MAX_AGE_DAYS 未重估 → 直接歸檔
                # (PM 2026-09 裁示 30 天剔除;現價仍在區間也不例外——筆記已不足判斷)。
                age_msg = _watchlist_age_msg(w.get("reviewed_at"))
                if age_msg:
                    pruned.append({**w, "pruned_at": datetime.now().strftime("%Y-%m-%d"),
                                   "prune_reason": age_msg})
                    continue
                t = _technical(tk, required)
                close = (t or {}).get("close", np.nan)
                zlo, zhi = float(w["zone_low"]), float(w["zone_high"])
                # stale/失效檢查(2026-08 原相事故 + 界霖破線事故):
                # 破停損 = setup 明確失敗 → 直接移除卡片並歸檔(PM 2026-08-28 裁示,
                # 失效卡留在版面只是噪音;痕跡進 entry_watchlist_archive.json 與 git)。
                # 區間漂移 >12% = 需人工重估 → 保留但標紅(原相/南茂即由此觸發重算)。
                stale_msg = _watchlist_stale_msg(close, zlo, zhi, float(w.get("stop") or 0))
                if stale_msg.startswith(WATCHLIST_STOP_BREACH_PREFIX):
                    pruned.append({**w, "pruned_at": datetime.now().strftime("%Y-%m-%d"),
                                   "prune_reason": stale_msg})
                    continue
                survivors.append(w)
                # 手寫 note 不再塞進 status(2026-08 版面清理):note 留在 entry_watchlist.json
                # 供人工參考;卡片只顯示結構化的區間/失效 + 一句話狀態。先前整段過期敘述
                # 被塞進 status,而 reason 又包一次 status,導致 email/dashboard 各印一次洗版。
                base_status = "觀察·等回檔" if not stale_msg else "🔴觀察區間已過期"
                rows.append({
                    "ticker": tk, "sector": sect.get(tk, ""),
                    "close": close,
                    "source_date": (t or {}).get("signal_date"),
                    "v20": (t or {}).get("v20", np.nan), "vol_ratio": (t or {}).get("vol_ratio", np.nan),
                    "rsi": (t or {}).get("rsi", np.nan), "foreign20": (t or {}).get("foreign20", np.nan),
                    "pred20": np.nan, "bucket": "", "decision": "watchlist",
                    "kind": "watch", "status": base_status, "stale": stale_msg,
                    "_lane_override": ("rere_watch" if "rere" in str(w.get("note", "")).lower() else "watch"),
                    "zone_low": zlo, "zone_high": zhi,
                    "stop": float(w.get("stop") or 0), "score": -999,
                    "lane": ("rere_watch" if "rere" in str(w.get("note", "")).lower() else "watch"),
                    "wash": np.nan,
                })
            if pruned and persist_watchlist:
                arch_path = BASE_DIR / "logs" / "entry_watchlist_archive.json"
                try:
                    arch = json.loads(arch_path.read_text(encoding="utf-8")) if arch_path.exists() else []
                except Exception:
                    arch = []
                arch.extend(pruned)
                arch_path.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
                for pw in pruned:
                    print(f"[watchlist] 已移除 {pw.get('ticker')} -> archive({pw.get('prune_reason', '')[:40]})")
            if (pruned or wl_dirty) and persist_watchlist:
                # 剔除或補戳日期任一發生都回寫,讓 reviewed_at 持久化(否則每天重戳=永不過期)
                wl_path.write_text(json.dumps(survivors, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # --- 出關雷達標籤(BT-disposition-release 2026-07-08 判決落地;2026-08-29 接線):---
    # 雷達≠扳機:純觀察標註,不改 kind/score/排序,由裁量層自行判斷。
    # radar json 由每晚排程 disposition_release_radar.py 產出;>4 天視為過期不標。
    try:
        radar = json.loads((BASE_DIR / "ml" / "data" / "disposition_radar.json").read_text(encoding="utf-8"))
        radar_age = (datetime.now() - datetime.strptime(radar.get("date", "1970-01-01"), "%Y-%m-%d")).days
        if radar_age <= 4:
            radar_hits = {str(h.get("ticker", "")).zfill(4): h for h in radar.get("hits", [])}
            for r in rows:
                h = radar_hits.get(r["ticker"])
                if not h:
                    continue
                yoy = h.get("known_yoy")
                yoy_txt = f",YoY{yoy:+.0f}%" if yoy is not None else ""
                fuse = "🔥雙引信" if h.get("dual_fuse") else ""
                r["status"] = f"{r['status']}·🚪出關雷達({h.get('days_since', '?')}天前出關{yoy_txt}){fuse}"
    except Exception:
        pass  # 雷達檔缺失不影響名單

    for row in rows:
        _apply_data_quality(row, required, special_gate, twii_bull)

    return {
        "as_of_close": _latest_predictions_path(required),
        "as_of_date": required.isoformat(), "trade_date": target.isoformat(),
        "data_status": "OK" if special_gate["complete"] else "UNKNOWN",
        "data_warnings": special_gate["reasons"], "special_status": special_gate,
        "chipk_src": chipk["chipk_src"].iloc[0] if not chipk.empty else None,
        "chipk_desktop_enabled": _chipk_desktop_enabled(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "shadow_legacy_main": shadow_legacy_main,
        "night_gap": _night_session_gap() if include_live else None,
    }


def _pick_rere_v2(rere_rows: list[dict[str, Any]], cap: int | None = None) -> list[dict[str, Any]]:
    """v2 名額在兩個子型態間平分(各 cap//2),有空位才由另一型補。
    排序分數含洗盤深度,若共用名額,淺洗盤 v2(8-10%)永遠排在蹲點 v2(≥10%)之後而上不了名單、帳本也累積不到
    (2026-09-15~17 回放:v2 候選 22-43 檔,前 6 全是蹲點 v2)。回測兩型報酬相當(+4.13% / +4.02%)且淺洗盤左尾較低,
    沒有「洗越深越好」的證據支持讓一型獨佔。輸入須已按 score 由高到低排序。"""
    cap = RERE_V2_MAX if cap is None else cap
    v2 = [r for r in rere_rows if r.get("ptype") in RERE_V2_PTYPES]
    half = cap // 2
    picked = ([r for r in v2 if r["ptype"] == "shakeout_v2"][:half]
              + [r for r in v2 if r["ptype"] == "shallow_v2"][:half])
    chosen = {id(r) for r in picked}
    picked += [r for r in v2 if id(r) not in chosen][:cap - len(picked)]
    return sorted(picked, key=lambda x: -x["score"])


def _legacy_main_rank(candidates: list[dict[str, Any]], top_n: int | None = None) -> list[dict[str, Any]]:
    """2026-10-07 前的主 lane 正式規則(影子名單,只供帳本並行比較,不上名單):
    模型不反對(推薦含「買進」且 pred>0)→ score = clean + pred20 + chip_bonus → 取前 N。
    輸入為已過型態 gate / ChipK veto / 資料品質的候選列;回傳獨立副本(後續 MOPS overlay 不影響)。"""
    top_n = MAIN_MAX if top_n is None else top_n
    picked = [dict(r) for r in candidates if r.get("model_buy") and r.get("kind") != "watch"]
    for r in picked:
        r["score"] = float(r.get("clean", 0.0)) + float(r.get("pred20", 0.0)) + float(r.get("chip_bonus", 0.0))
        r["lane"] = "main_legacy"
    picked.sort(key=lambda x: -x["score"])
    return picked[:top_n]


def generate_certified(as_of: str | None = None, *, trade_date: str | None = None,
                       persist_watchlist: bool = False, include_live: bool = True) -> dict[str, Any]:
    """One actual generation with immutable input and authorized-write evidence."""
    from scripts.entry_artifact_lineage import capture_generation, observe_watchlist_writes, bind_generation
    run = capture_generation(as_of, trade_date, persist_watchlist=persist_watchlist, include_live=include_live)
    with observe_watchlist_writes(run):
        result = generate(run["as_of_date"], trade_date=run["trade_date"],
                          persist_watchlist=persist_watchlist, include_live=include_live)
    return bind_generation(run, result)


def _apply_data_quality(row: dict, required: date, gate: dict, regime: bool | None) -> None:
    """執行資料資格與策略訊號分離；未知只能觀察，絕不提高 rere 倉位。"""
    warnings = list(row.get("data_warnings", []))
    if row.get("source_date") != required.isoformat():
        warnings.append(f"日K不是所需收盤日 {required}")
    if row.get("lane", "main") == "main" and row.get("model_source_date") != required.isoformat():
        warnings.append(f"模型個股資料不是所需收盤日 {required}")
    if not gate.get("complete"):
        warnings.append("處置名單完整性待確認")
    if row.get("lane") == "rere" and regime is None:
        warnings.append("大盤資料待確認")
    warnings = list(dict.fromkeys(warnings))
    row["data_warnings"] = warnings
    row["data_status"] = "UNKNOWN" if warnings else "OK"
    row["intraday_verified"] = False
    row["signal_kind"] = row["kind"]
    if row["ticker"] in gate.get("blocked_tickers", []):
        row["kind"], row["status"] = "veto", "暫緩：交易日處置中"
    elif warnings and row["kind"] != "veto":
        row["kind"], row["status"] = "watch", "資料待確認：僅供觀察"


def _chipk_source_label(result: dict[str, Any]) -> str:
    if result.get("chipk_src"):
        return str(result["chipk_src"])
    if result.get("chipk_desktop_enabled"):
        return "ChipK desktop enabled but no fresh source"
    return "ChipK desktop retired; mobile screenshots are manual veto only"


def _format_entry_row(i: int, r: dict[str, Any], nm: dict[str, str]) -> dict[str, Any]:
    """單列輸出格式(rows 與 shadow_legacy_main 共用,確保帳本追蹤器讀到同樣欄位)。"""
    if r.get("lane") == "watch":
        reason = (f"【watchlist·已過期】{r.get('stale')}" if r.get("stale")
                  else "【watchlist】回踩進區間才觸發,照 5 步紀律執行")
    elif r.get("lane") == "rere":
        ptype = r.get("ptype")
        reason = (f"【rere lane·{RERE_PTYPE_LABEL.get(ptype, '蹲點型')}】洗盤{r['wash']*100:.0f}%後貼MA20{r['v20']*100:+.0f}% "
                  f"{'放量' if ptype == 'ignition' else '量縮'}{r['vol_ratio']:.1f}x "
                  f"{'不要求外資拐點(v2 並行驗證)' if ptype in RERE_V2_PTYPES else '外資轉買'};60日波段,模型pred不採計,務必小倉")
    else:
        reason = (f"貼MA20{r['v20']*100:+.0f}% 量{r['vol_ratio']:.1f}x RSI{r['rsi']:.0f} "
                  f"外資20d{r['foreign20']:+.0f}張" + (f" 籌碼{r['bucket']}" if r.get("bucket") else ""))
    return {
        "priority": str(i),
        "stock": f"{r['ticker']} {nm.get(r['ticker'], r['sector'][:4])}",
        "sector": r.get("sector", ""),
        "lane": r.get("lane", "main"),
        "ptype": r.get("ptype"),
        "status": r["status"],
        "data_status": r.get("data_status", "UNKNOWN"),
        "data_warnings": r.get("data_warnings", []),
        "model_source_date": r.get("model_source_date"),
        "intraday_verified": False,
        "kind": r["kind"], "zone": f"{r['zone_low']}-{r['zone_high']}", "stop": str(r["stop"]),
        "no_chase": f">{r['zone_high']}", "ret20d": f"{r['pred20']*100:+.2f}%" if r["pred20"] == r["pred20"] else "n/a",
        "stale": r.get("stale", ""),
        "reason": reason,
        "source_date": r.get("source_date"),
    }


def to_entry_json(result: dict[str, Any], trade_date: str) -> dict[str, Any]:
    model_path = Path(result["as_of_close"]) if result.get("as_of_close") else None
    model_source = model_path.name if model_path else None
    model_match = re.search(r"(\d{4}-\d{2}-\d{2})", model_source or "")
    nm = _name_map()
    chipk_source = _chipk_source_label(result)
    out_rows = [_format_entry_row(i, r, nm) for i, r in enumerate(result["rows"], 1)]
    shadow_rows = [_format_entry_row(i, r, nm) for i, r in enumerate(result.get("shadow_legacy_main") or [], 1)]
    payload = {
        "trade_date": trade_date,
        "as_of_date": result.get("as_of_date"),
        "generated_at": result.get("generated_at"),
        "data_status": result.get("data_status", "UNKNOWN"),
        "data_warnings": result.get("data_warnings", []),
        "special_status": result.get("special_status", {}),
        "model_source": model_source,
        "model_source_date": model_match.group(1) if model_match else None,
        "subtitle": "canonical 產生器(型態 gate → 模型確認,Claude/Codex 共用)",
        "intro": (
            f"由 scripts/generate_entry_candidates.py 產生;來源 {os.path.basename(result['as_of_close'] or '')}。"
            f"型態硬 gate 先、依型態乾淨度排序(模型分數只顯示,2026-10-07 起不過濾不排序);籌碼否決僅接受人工截圖(桌面自動源已退役)。"
            f"本日 rere lane 新訊號:{sum(1 for r in result['rows'] if r.get('lane')=='rere')} 檔"
            f"(含 v2 {sum(1 for r in result['rows'] if r.get('ptype') in RERE_V2_PTYPES)} 檔;"
            f"rere 為日條件,無訊號日為 0 屬正常;v2=不要求外資拐點,並行驗證中)。"
        ),
        "rows": out_rows,
        # 舊主 lane 規則的影子名單(不上儀表板名單、不進 rows 計數;entry_filter_tracker --legacy 記入獨立帳本)
        "shadow_legacy_main": shadow_rows,
        "discipline": (
            ([f"【夜盤】{result['night_gap']['hint']}"] if result.get("night_gap") else [])
            + [
                "盤中進區間守15-30分才算;跌破停損=型態失敗就砍,別凹。",
                "出場:賣壓力減碼、砍型態失敗(非當日紅)、龍頭留基本倉、波段來回。",
                "rere 系統掃描為方法代理；主力成本與盤中守穩仍需人工確認，不能視為 KOL 本人薦股。",
            ]
        ),
    }
    from scripts.entry_artifact_lineage import bind_payload
    return bind_payload(result, payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="交易日標籤(如 2026-07-02),預設今天+1 天概念由使用者給")
    ap.add_argument("-o", "--output", default=None, help="輸出 JSON 路徑")
    ap.add_argument("--dry", action="store_true", help="只印摘要不寫檔")
    ap.add_argument("--as-of", default=None, help="來源收盤日；必須是交易日前一交易日")
    args = ap.parse_args()

    # 最新 sector/MOPS 並非歷史 PIT archive；--as-of 不能冒充任意歷史回測。
    now = datetime.now()
    latest_close = (now.date() if is_taiwan_trading_day(now) and now.hour >= 18
                    else previous_taiwan_trading_day(now))
    target = pd.Timestamp(args.date).date() if args.date else next_taiwan_trading_day(latest_close)
    if previous_taiwan_trading_day(target) != latest_close:
        ap.error("歷史/未來名單請用隔離回測器；正式候選僅允許最新已收盤日")

    result = generate_certified(args.as_of, trade_date=target.isoformat(), persist_watchlist=not args.dry)
    if not result.get("as_of_close"):
        print("[entry-candidates] no prediction file matches snapshot date; refusing stale plan")
        return 2
    trade_date = result["trade_date"]
    chipk_source = _chipk_source_label(result)
    print(f"候選(型態→模型→籌碼K):{len(result['rows'])} 檔  來源 {os.path.basename(result['as_of_close'] or '')} / {chipk_source}")
    if result.get("night_gap"):
        print(f"夜盤:{result['night_gap']['hint']}")
    for r in result["rows"][:15]:
        lane = "rere " if r.get("lane") == "rere" else r["kind"]
        p20 = f"{r['pred20']*100:+.1f}%" if r["pred20"] == r["pred20"] else "n/a"
        print(f"  [{lane:5}] {r['ticker']} {r['sector'][:4]:5} 收{r['close']:.1f} 乖離MA20{r['v20']*100:+.0f}% 量{r['vol_ratio']:.1f}x 外資20d{r['foreign20']:+.0f} pred{p20} → {r['status']}")

    if not args.dry and args.output:
        payload = to_entry_json(result, trade_date)
        _atomic_json(Path(args.output), payload)
        # 同步 static 副本:web 進場過濾器分頁的免重啟資料源(/static 每請求讀磁碟)
        static_copy = BASE_DIR / "frontend" / "static" / "entry_canonical.json"
        _atomic_json(static_copy, payload)
        print(f"已寫 {args.output}({len(payload['rows'])} 檔)+ static 副本")
    return 0


def _atomic_json(path: Path, payload: dict) -> None:
    from scripts.entry_artifact_lineage import publish_entry_artifact
    publish_entry_artifact(path, payload)


if __name__ == "__main__":
    raise SystemExit(main())
