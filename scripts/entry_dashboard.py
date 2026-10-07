# -*- coding: utf-8 -*-
"""台股進場名單 dashboard v2(深色系)——純呈現層,不動任何訊號邏輯。

- 兩策略明確分區:主策略(dataA 溫和動能,20日)與 rere lane(暴力轉機,60日),各帶自己的 watch。
- 大盤資訊:TWII 收盤/漲跌/vs MA20/60 + 120日走勢。
- 個股詳情(點卡片或搜尋任意個股):日K(名單股)或收盤線(全 universe)+ MACD +
  四大面向數據(技術/籌碼/基本/評價)。K線與 MACD 柱採台股慣例「紅漲綠跌」。
- 查詢 input:全 universe(snapshot ~1209 檔)代號/名稱搜尋。

資料源:logs/entry_list_<date>.json + ml/models/snapshot_cache.pkl +
dataA predictions + stock.duckdb indices(TWII)+ 日K資料/(名單股 OHLCV)。
深色色板經 dataviz validator 驗證;所有狀態圖示+文字雙編碼。

用法:
  python scripts/entry_dashboard.py                  # 最新 entry_list
  python scripts/entry_dashboard.py --date 20260907   # 仍須目前有效的 certified plan

本工具只呈現當前已驗證來源。歷史日期需要獨立 frozen 研究 bundle,
不可用目前的日K/快照假裝歷史 as-of。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
OUT = BASE_DIR / "ml" / "reports" / "entry_dashboard_latest.html"
DAILY_DIR = BASE_DIR / "日K資料"

ASPECT_FIELDS = [
    "RSI_14", "price_vs_ma20", "price_vs_ma60", "MACDh_12_26_9",
    "Volume", "VOL_MA_20",
    "revenue_yoy_latest", "revenue_yoy_3m_avg", "eps_ttm", "eps_yoy",
    "gross_margin_latest", "operating_margin_latest",
    "pe_ratio", "pb_ratio", "atr_pct", "volatility_20d",
]


class DashboardLineageError(ValueError):
    """Refuse mixed or uncertified inputs without overwriting the last HTML."""


class _RenderReadSet:
    """Read-only, build-scoped receipts; never issue source certificates."""

    def __init__(self):
        from ml.snapshot_lineage import source_state
        self.sources = source_state()
        # Supplemental readers are not all canonical ML inputs. Include missing
        # paths/globs so a file arriving halfway through rendering also aborts.
        self.dependencies = source_state(patterns=[str(BASE_DIR / path) for path in (
            "scripts/entry_dashboard.py", "scripts/entry_artifact_lineage.py",
            "scripts/daytrade_prep_screener.py", "scripts/tdcc_whale_radar.py",
            "scripts/taiwan_trading_calendar.py", "config/adhoc_market_closures.json",
            "ml/prediction_provenance.py", "disposition_active.csv",
            "大盤指數/index_*.csv", "日K資料/*.csv", "集保分散/tdcc_summary.csv",
            "ml/data/sector_mapping.csv", "ml/data/tdcc_whale_*.csv",
            "ml/data/tdcc_whale_radar_status.json", "ml/models/snapshot_cache.pkl*",
            "ml/models/dataA_predictions_*.csv*", "ml/models/predictions_*.csv*",
            "ml/reports/entry_filter_ledger.csv", "ml/reports/industry_news_latest.md",
            "ml/reports/agent_arena_latest.json", "my_holdings.db", "my_holdings.db-wal",
        )])
        self.artifacts: dict[Path, str] = {}
        self.predictions: list[dict] = []

    def track(self, path: Path) -> None:
        path = Path(path)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise DashboardLineageError(f"Required certified dashboard input missing: {path}") from exc
        if path in self.artifacts and self.artifacts[path] != digest:
            raise DashboardLineageError(f"Dashboard input changed during rendering: {path}")
        self.artifacts[path] = digest

    def assert_unchanged(self) -> None:
        from ml.snapshot_lineage import assert_sources_unchanged
        from ml.prediction_provenance import assert_run_unchanged
        assert_sources_unchanged(self.sources)
        assert_sources_unchanged(self.dependencies)
        for certificate in self.predictions:
            assert_run_unchanged(certificate)
        for path in self.artifacts:
            self.track(path)


def _certified_snapshot(*, inputs: _RenderReadSet | None = None) -> pd.DataFrame:
    from ml.snapshot_lineage import load_snapshot
    path = BASE_DIR / "ml" / "models" / "snapshot_cache.pkl"
    if inputs is not None:
        inputs.track(path)
        inputs.track(Path(str(path) + ".manifest.json"))
    frame = load_snapshot(path)
    if frame is None:
        raise DashboardLineageError("Canonical snapshot certificate missing, invalid, or stale; rebuild inputs before rendering")
    return frame


def _certified_predictions(path: Path, slot: str, *, inputs: _RenderReadSet | None = None,
                           **read_csv_kwargs) -> pd.DataFrame:
    from ml.prediction_provenance import load_prediction_csv
    if inputs is not None:
        inputs.track(path)
        inputs.track(Path(str(path) + ".manifest.json"))
    loaded = load_prediction_csv(path, expected_model_slot=slot,
                                 read_csv_kwargs={"dtype": None, **read_csv_kwargs})
    if loaded is None:
        raise DashboardLineageError(f"{slot} prediction certificate missing, invalid, or stale: {path}")
    frame, certificate = loaded
    if inputs is not None:
        inputs.predictions.append(certificate)
    return frame


def _coerce_date(value: object) -> date | None:
    raw = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _latest_dated_file(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
    date_format: str,
    not_after: date | None = None,
) -> tuple[Path, date] | None:
    """Select only files whose entire variable part is a valid calendar date."""
    candidates: list[tuple[date, Path]] = []
    pattern = re.compile(rf"^{re.escape(prefix)}(.+){re.escape(suffix)}$")
    if not directory.exists():
        return None
    for path in directory.iterdir():
        match = pattern.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            source_date = datetime.strptime(match.group(1), date_format).date()
        except ValueError:
            continue
        if not_after is not None and source_date > not_after:
            continue
        candidates.append((source_date, path))
    if not candidates:
        return None
    source_date, path = max(candidates, key=lambda item: item[0])
    return path, source_date


def _entry_model_metadata(payload: dict) -> tuple[str | None, date | None]:
    source = str(payload.get("model_source") or "").strip() or None
    source_date = _coerce_date(payload.get("model_source_date"))
    if source is None:
        match = re.search(
            r"(?:dataA_)?predictions_(\d{4}-\d{2}-\d{2})\.csv",
            str(payload.get("intro") or ""),
        )
        if match:
            source_date = _coerce_date(match.group(1))
            source = match.group(0)
    return source, source_date


def _source_freshness(
    trade_date: object,
    sources: list[tuple[str, str, object, str]],
) -> list[dict]:
    """Describe daily/weekly source freshness relative to the entry trade date."""
    target = _coerce_date(trade_date)
    if target is None:
        return [
            {"key": key, "label": label, "asof": str(actual or ""),
             "expected": None, "status": "unknown"}
            for key, label, actual, _cadence in sources
        ]

    try:
        from scripts.taiwan_trading_calendar import previous_taiwan_trading_day
    except ModuleNotFoundError:  # Direct execution: python scripts/entry_dashboard.py
        from taiwan_trading_calendar import previous_taiwan_trading_day

    expected_daily = previous_taiwan_trading_day(target)
    result = []
    for key, label, actual_raw, cadence in sources:
        actual = _coerce_date(actual_raw)
        threshold = expected_daily - timedelta(days=7) if cadence == "weekly" else expected_daily
        status = ("missing" if actual is None else "future" if actual > expected_daily
                  else "fresh" if actual >= threshold else "stale")
        result.append({
            "key": key,
            "label": label,
            "asof": actual.isoformat() if actual else None,
            "expected": threshold.isoformat(),
            "status": status,
        })
    return result


def load_legacy_ledger_summary(main_ledger_path) -> dict | None:
    """舊主 lane 規則影子帳本(2026-10-07 起)與同期新排序主 lane 的已平倉摘要;缺檔回 None。"""
    legacy_path = BASE_DIR / "ml" / "reports" / "entry_filter_ledger_legacy_main.csv"
    if not legacy_path.exists():
        return None
    try:
        lg = pd.read_csv(legacy_path, dtype={"ticker": str, "trade_date": str})
        if lg.empty:
            return None
        since = str(lg["trade_date"].min())

        def _closed_stat(frame):
            closed = frame[frame["status"].isin(["stopped", "expired"])]
            ret = pd.to_numeric(closed.get("ret_pct"), errors="coerce").dropna()
            return (int(len(closed)), (round(float(ret.mean()), 2) if len(ret) else None),
                    (int(round(float((ret > 0).mean() * 100))) if len(ret) else None))

        closed_n, mean, win = _closed_stat(lg)
        new_closed, new_mean, new_win = 0, None, None
        if main_ledger_path.exists():
            mf = pd.read_csv(main_ledger_path, dtype={"ticker": str, "trade_date": str})
            mf = mf[(mf["lane"] == "main") & (mf["trade_date"] >= since)]
            new_closed, new_mean, new_win = _closed_stat(mf)
        return {"since": since, "closed": closed_n, "mean": mean, "win": win,
                "holding": int((lg["status"] == "holding").sum()),
                "new_closed": new_closed, "new_mean": new_mean, "new_win": new_win}
    except Exception:  # noqa: BLE001 - 摘要缺失不影響儀表板
        return None


def strategy_of(row: dict) -> str:
    lane = str(row.get("lane") or "")
    return "rere" if lane.startswith("rere") else "main"


def state_of(row: dict) -> str:
    if str(row.get("kind")) == "veto":
        return "veto"
    if str(row.get("kind")) == "watch":
        return "watch"
    return "go" if str(row.get("kind")) == "go" else "small"


def guard_context(raw: object) -> dict:
    """Translate observed automatic-layer diagnostics without inventing a cause."""
    detail = str(raw or "").strip()
    if not detail:
        return {}
    reasons = []
    if "攔截率" in detail:
        reasons.append("名單風險攔截比例偏高")
    if "雙軌分歧" in detail:
        reasons.append("模型判斷分歧偏高")
    if any(word in detail.lower() for word in ("churn", "turnover", "換手率", "名單變動")):
        reasons.append("近期名單變動過大")
    return {"headline": "模型風險檢查警示：" + "、".join(reasons or ["部分風險檢查未通過"]),
            "scope": "這是模型預測的風險診斷；Champion 是否正式放行，以當日正式訊號名單（unified）為準。rere 觀察仍依自身型態、失效價與人工確認判斷。",
            "detail": detail}


def close_context(price: object, price_date: object, zone: object, as_of: object) -> dict:
    """Describe a dated historical close relative to the zone, never a live trigger."""
    unknown = {"price": None, "date": None, "position": "unknown", "message": "盤後價格日期未確認，暫不比較區間。"}
    observed, cutoff = _coerce_date(price_date), _coerce_date(as_of)
    try:
        value = float(price)
    except (TypeError, ValueError):
        return unknown
    if not np.isfinite(value) or value <= 0 or observed is None or cutoff is None or observed > cutoff:
        return unknown
    result = {"price": round(value, 2), "date": observed.isoformat(), "position": "unknown",
              "message": "觀察區間待確認，暫不比較位置。"}
    match = re.fullmatch(r"\s*([\d.]+)\s*[-–]\s*([\d.]+)\s*", str(zone or ""))
    if not match:
        return result
    low, high = float(match[1]), float(match[2])
    if not 0 < low <= high:
        return result
    if value < low:
        result.update(position="below", message="上次收盤低於區間；等站回下緣後再確認守穩。")
    elif value > high:
        result.update(position="above", message="上次收盤高於區間；等回檔，不追價。")
    else:
        result.update(position="inside", message="僅上次收盤在區間；盤中是否守穩仍待確認。")
    return result


def card_context(row: dict, plan: dict) -> dict:
    """Presentation contract: a daily screen never proves an intraday entry.

    Preserve the generator's lane/kind/rank and display-only chip verdict. Missing
    legacy metadata is explicitly unknown; it must not acquire a green badge.
    """
    lane = strategy_of(row)
    quality = row.get("data_quality") or plan.get("data_quality") or {}
    quality = quality if isinstance(quality, dict) else {}
    status = str(row.get("data_status") or plan.get("data_status") or quality.get("status") or "UNKNOWN").upper()
    source = _coerce_date(row.get("source_date"))
    model = _coerce_date(row.get("model_source_date") or plan.get("model_source_date"))
    warnings = []
    for values in (plan.get("data_warnings"), row.get("data_warnings"), quality.get("warnings")):
        if isinstance(values, list):
            warnings.extend(str(v) for v in values if v)
    if status != "OK":
        warnings.append("資料完整性尚未通過確認")
    if source is None:
        warnings.append("型態資料日期未提供")
    trade = _coerce_date(plan.get("trade_date"))
    if trade and source and source >= trade:
        warnings.append("型態日期晚於盤前應使用的資料")
    if lane == "main" and model is None:
        warnings.append("模型資料日期未提供")
    if lane == "main" and source and model and source != model:
        warnings.append("型態與模型資料日期不同")
    zone = re.fullmatch(r"\s*([\d.]+)\s*[-–]\s*([\d.]+)\s*", str(row.get("zone") or ""))
    try:
        valid_zone = bool(zone and 0 < float(zone[1]) <= float(zone[2]) and float(row.get("stop")) > 0)
    except (ValueError, TypeError):
        valid_zone = False
    if not valid_zone:
        warnings.append("觀察區間或失效價待重估")
    warnings = list(dict.fromkeys(warnings))
    if row.get("kind") == "veto" or row.get("vt") == "skip" or row.get("stale"):
        label, tone = "暫緩 · 有否決條件", "skip"
        action = "先處理卡上的否決或過期條件，再重新確認型態。"
    elif warnings:
        label, tone = "暫緩 · 待資料確認", "warn"
        action = "資料補齊並重跑檢查前，保留觀察。"
    elif row.get("kind") == "watch":
        label, tone = "觀察 · 等回檔", "watch"
        action = "等價格回到觀察區間；跌破失效價就重新評估。"
    else:
        label, tone = "觀察 · 待盤中確認", "check"
        action = "進入區間後，仍須確認支撐守穩 15–30 分鐘；超過上緣不追。"
    ptype = str(row.get("ptype") or row.get("pattern_type") or "")
    if lane == "rere":
        pattern = "放量點火" if ptype == "ignition" or "點火" in str(row.get("status")) else "洗盤轉強"
        basis = [f"{pattern}型態入列，仍需盤中守住支撐", "模型分數不否決 rere；進場前人工查分點與主力成本"]
    elif str(row.get("lane")) in ("watch", "rere_watch"):
        basis = ["人工保留的回檔觀察", "原觀察區間需要持續確認有效"]
    elif row.get("kind") == "watch":
        basis = ["型態候選暫列觀察", "尚有資料或確認條件未滿足，先完成卡上檢查"]
    else:
        basis = ["價格接近月線，型態與量能通過篩選", "dataA 模型提供方向參考；不是確定報酬"]
    return {"label": label, "tone": tone, "action": action, "basis": basis,
            "last_close": close_context(row.get("last_close"), row.get("last_close_date"), row.get("zone"),
                                         row.get("source_date") or plan.get("as_of_date")),
            "warnings": warnings, "source_date": source.isoformat() if source else None,
            "model_source_date": model.isoformat() if model else None,
            "horizon": 60 if lane == "rere" else 20,
            "size": "只考慮最小試單" if lane == "rere" or row.get("kind") == "small" else "確認條件後再決定部位"}


IDX_META = [("TWII", "加權指數"), ("GSPC", "S&P 500"), ("SOX", "費城半導體"),
            ("VIX", "VIX 恐慌"), ("USDTWDX", "美元/台幣")]


def load_indices() -> list:
    """大盤指數/*.csv → 多指數卡(收盤/漲跌/90日走勢/vs均線)。"""
    out = []
    for key, lab in IDX_META:
        p = BASE_DIR / "大盤指數" / f"index_{key}.csv"
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, usecols=["Date", "Close"]).dropna().tail(130)
        except Exception:
            continue
        c = df["Close"].astype(float)
        if not len(c):
            continue
        ma20 = c.rolling(20).mean().iloc[-1]
        ma60 = c.rolling(60).mean().iloc[-1]
        nd = 1 if key in ("TWII", "GSPC", "SOX") else 3
        out.append({"k": key, "nm": lab,
                    "c": [round(float(x), nd) for x in c.tail(90)],
                    "chg": round(c.iloc[-1] / c.iloc[-2] - 1, 4) if len(c) > 1 else 0,
                    "vs20": round(c.iloc[-1] / ma20 - 1, 4) if ma20 == ma20 else None,
                    "vs60": round(c.iloc[-1] / ma60 - 1, 4) if ma60 == ma60 else None,
                    "d": str(df["Date"].iloc[-1])[:10]})
    return out


def load_universe(
    list_tickers: set[str],
    *,
    model_source: str | None = None,
    model_source_date: date | None = None,
    _inputs: _RenderReadSet | None = None,
) -> tuple[dict, dict]:
    """全 universe:名稱/產業/四大面向/90日收盤;名單股另附 120日 OHLCV。"""
    df = _certified_snapshot(inputs=_inputs)
    snapshot_dates = pd.to_datetime(df.get("Date"), errors="coerce")
    snapshot_asof = snapshot_dates.max().date().isoformat() if snapshot_dates.notna().any() else None
    if model_source_date is not None and _coerce_date(snapshot_asof) != model_source_date:
        raise DashboardLineageError("Canonical snapshot date differs from the certified entry plan")
    df["ticker"] = df["ticker"].astype(str).str.zfill(4)

    sec = pd.read_csv(BASE_DIR / "ml" / "data" / "sector_mapping.csv", dtype=str)
    sec["Ticker"] = sec["Ticker"].str.zfill(4)
    names = dict(zip(sec["Ticker"], zip(sec["Name"], sec["Sector"])))

    pred = {}
    pred_pick = None
    pred_slot = "dataA"
    if model_source and Path(model_source).name == model_source:
        candidate = BASE_DIR / "ml" / "models" / model_source
        match = re.fullmatch(r"(dataA_)?predictions_(\d{4}-\d{2}-\d{2})\.csv", model_source)
        if candidate.is_file() and match:
            candidate_date = _coerce_date(match.group(2))
            if candidate_date is not None:
                pred_pick = (candidate, candidate_date)
                pred_slot = "dataA" if match.group(1) else "production"
    if model_source and pred_pick is None:
        raise DashboardLineageError(f"The plan's exact prediction input is unavailable: {model_source}")
    if pred_pick is None:
        pred_pick = _latest_dated_file(
            BASE_DIR / "ml" / "models",
            prefix="dataA_predictions_",
            suffix=".csv",
            date_format="%Y-%m-%d",
            not_after=model_source_date,
        )
    data_a_asof = None
    if pred_pick:
        pred_path, pred_date = pred_pick
        if model_source_date is not None and pred_date != model_source_date:
            raise DashboardLineageError("Selected prediction date differs from the certified entry plan")
        data_a_asof = pred_date.isoformat()
        pf = _certified_predictions(pred_path, pred_slot, inputs=_inputs)
        pred = {str(t).split(".")[0].zfill(4): float(p)
                for t, p in zip(pf["ticker"], pf["pred_return_20d"])}
    else:
        raise DashboardLineageError("No certified DataA prediction input selected for this entry plan")

    stocks = {}
    for _, r in df.iterrows():
        tk = r["ticker"]
        a = {}
        for f in ASPECT_FIELDS:
            v = r.get(f)
            if v is not None and v == v and np.isfinite(float(v) if isinstance(v, (int, float, np.floating)) else np.nan):
                a[f] = round(float(v), 4)
        nm, sc = names.get(tk, ("", ""))
        # 2026-09-23 審查 P1-2:snapshot 每列自帶 Date(停牌/斷更股會停在舊日,
        # 如 5371 止於 08-21),逐檔保留讓前端能標示過期,不被全頁 asof 掩蓋
        row_dt = pd.to_datetime(r.get("Date"), errors="coerce")
        stocks[tk] = {"n": nm, "s": sc, "a": a,
                      "a_date": row_dt.date().isoformat() if row_dt == row_dt else None,
                      "p": round(pred.get(tk, float("nan")), 4) if tk in pred else None}

    # 收盤序列(90日)全 universe;名單股另附 OHLCV 120日
    for tk in list(stocks.keys()):
        p = DAILY_DIR / f"{tk}.csv"
        if not p.exists():
            continue
        try:
            k = pd.read_csv(p, usecols=lambda c: c in (
                "Date", "Open", "High", "Low", "Close", "Volume",
                "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell",
                "Margin_Balance", "Short_Balance"))
        except Exception:
            continue
        # 籌碼實數(張):snapshot 的 cumsum 欄是百分位,不能當張數顯示
        a = stocks[tk]["a"]
        for col, k5, k20 in (("Foreign_BuySell", "f5", "f20"), ("Trust_BuySell", "t5", "t20"),
                             ("Dealer_BuySell", "d5", "d20")):
            if col in k.columns:
                v = pd.to_numeric(k[col], errors="coerce")
                if v.notna().any():
                    a[k5] = int(v.tail(5).sum() / 1000)
                    a[k20] = int(v.tail(20).sum() / 1000)
        if "f20" in a:
            a["i20"] = a.get("f20", 0) + a.get("t20", 0) + a.get("d20", 0)
        # 逐日法人買賣超(近20日,張)——rere「當日轉買」判斷需要日粒度
        for col, key in (("Foreign_BuySell", "ff"), ("Trust_BuySell", "tt"),
                         ("Dealer_BuySell", "dd")):
            if col in k.columns:
                v = pd.to_numeric(k[col], errors="coerce").fillna(0).tail(20)
                a[key] = [int(x_ / 1000) for x_ in v]
        for col, key in (("Margin_Balance", "mg5"), ("Short_Balance", "sb5")):
            if col in k.columns:
                mb = pd.to_numeric(k[col], errors="coerce").dropna()
                if len(mb) >= 6:
                    a[key] = int(mb.iloc[-1] - mb.iloc[-6])
        if not ({"Open", "High", "Low", "Close"} <= set(k.columns)):
            continue
        k = k.dropna(subset=["Close"]).copy()
        k["Date"] = k["Date"].astype(str).str[:10]
        dated = k.assign(_date=pd.to_datetime(k["Date"], errors="coerce")).dropna(subset=["_date"]).sort_values("_date")
        if not dated.empty:
            stocks[tk]["last_close"] = round(float(dated["Close"].iloc[-1]), 2)
            stocks[tk]["last_close_date"] = dated["_date"].iloc[-1].date().isoformat()

        def bars(df, lab, vdiv):
            return [[d, round(float(o), 2), round(float(h), 2), round(float(l), 2),
                     round(float(c), 2), int(v // vdiv) if v == v else 0]
                    for d, o, h, l, c, v in zip(df[lab], df["Open"], df["High"],
                                                df["Low"], df["Close"], df["Volume"])]

        d90 = k.tail(90).copy()
        d90["lab"] = d90["Date"].str[5:10]
        stocks[tk]["o"] = bars(d90, "lab", 1000)          # 量:張
        # 月K(近5年)/年K(全歷史):先聚合再輸出;量:千張
        for key, cut, n_keep in (("om", 7, 60), ("oy", 4, 40)):
            grp = k.copy()
            grp["lab"] = grp["Date"].str[:cut]
            agg = grp.groupby("lab", sort=True).agg(
                Open=("Open", "first"), High=("High", "max"),
                Low=("Low", "min"), Close=("Close", "last"),
                Volume=("Volume", "sum")).reset_index().tail(n_keep)
            if cut == 7:
                agg["lab"] = agg["lab"].str[2:]
            stocks[tk][key] = bars(agg, "lab", 1_000_000)
    return stocks, {"snapshot": snapshot_asof, "data_a": data_a_asof}


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_news_html() -> str:
    """industry_news_latest.md → 摺疊式 HTML(白名單新聞日報,靜態嵌入)。"""
    p = BASE_DIR / "ml" / "reports" / "industry_news_latest.md"
    if not p.exists():
        return ""
    out, li, sec_open, first = [], False, False, True

    def close_li():
        nonlocal li
        if li:
            out.append("</ul>")
            li = False

    for raw in p.read_text(encoding="utf-8").splitlines():
        ln = raw.rstrip()
        t = _esc(ln)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        if ln.startswith("# "):
            out.append(f'<div class="meta">{t[2:]}</div>')
        elif ln.startswith("## "):
            close_li()
            if sec_open:
                out.append("</div></details>")
            out.append(f'<details class="dled"{" open" if first else ""}><summary><b>{t[3:]}</b></summary><div class="newsbody">')
            sec_open, first = True, False
        elif ln.startswith("### "):
            close_li()
            out.append(f"<h4>{t[4:]}</h4>")
        elif ln.startswith("- "):
            if not li:
                out.append("<ul>")
                li = True
            out.append(f"<li>{t[2:]}</li>")
        elif ln.strip():
            close_li()
            out.append(f"<p>{t}</p>")
    close_li()
    if sec_open:
        out.append("</div></details>")
    return "\n".join(out)


def load_arena() -> dict:
    """agent_arena_latest.json → 精簡投資賽 payload(排行/持倉/掛單/近況)。"""
    p = BASE_DIR / "ml" / "reports" / "agent_arena_latest.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))

    def rnd(v, n=2):
        try:
            return round(float(v), n)
        except Exception:
            return None

    st = [{"rank": s.get("rank"), "id": s.get("agent_id"), "nm": s.get("name"),
           "sty": s.get("style"), "eq": rnd(s.get("equity"), 0),
           "tr": rnd(s.get("total_return_pct")), "dr": rnd(s.get("daily_return_pct")),
           "pos": s.get("open_positions"), "prog": rnd(s.get("progress_to_target_pct"), 2),
           "btr": rnd(s.get("backtest_return_pct"), 1), "bmdd": rnd(s.get("backtest_max_drawdown_pct"), 1)}
          for s in d.get("standings") or []]
    hold = [{"a": h.get("agent_id"), "tk": str(h.get("ticker")), "nm": h.get("stock_name"),
             "ed": h.get("entry_date"), "ep": rnd(h.get("entry_price")),
             "hw": rnd(h.get("high_water_price")), "sh": rnd(h.get("shares"), 0),
             "no": rnd(h.get("notional"), 0), "xp": h.get("exit_policy")}
            for h in d.get("holdings") or []]
    pend = [{"a": o.get("agent_id"), "side": o.get("side"), "tk": str(o.get("ticker")),
             "sd": o.get("signal_date"), "no": rnd(o.get("target_notional"), 0),
             "sc": rnd(o.get("signal_close")), "rsn": o.get("signal_reason")}
            for o in d.get("pending_orders") or [] if o.get("status") == "PENDING"]
    ev = sorted(d.get("order_events") or [], key=lambda x: x.get("id") or 0, reverse=True)[:40]
    ev = [{"a": e.get("agent_id"), "side": e.get("side"), "tk": str(e.get("ticker")),
           "st": e.get("status"), "sd": e.get("signal_date"), "fd": e.get("filled_date"),
           "fp": rnd(e.get("fill_price")), "rsn": e.get("signal_reason"),
           "ret": rnd(e.get("intraday_return_pct"))}
          for e in ev]
    return {"date": d.get("latest_date"), "cap0": d.get("starting_capital"),
            "capT": d.get("target_capital"), "n": d.get("agent_count"),
            "adm": d.get("admitted_count"), "st": st, "hold": hold, "pend": pend, "ev": ev}


def load_tdcc() -> tuple[dict, str | None]:
    """集保分散/tdcc_summary.csv(週頻)→ 每檔近8週 [MMDD, 大戶%, 散戶%, 股東人數]。"""
    p = BASE_DIR / "集保分散" / "tdcc_summary.csv"
    if not p.exists():
        return {}, None
    df = pd.read_csv(p, dtype={"Ticker": str, "Date": str})
    df = df[df["Ticker"].str.len() == 4]
    parsed_dates = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce")
    tdcc_asof = parsed_dates.max().date().isoformat() if parsed_dates.notna().any() else None
    out = {}
    for tk, g in df.groupby("Ticker"):
        g = g.sort_values("Date").tail(8)
        try:
            out[tk] = [[str(d)[4:8], round(float(w), 2), round(float(r), 2), int(h)]
                       for d, w, r, h in zip(g["Date"], g["Whale_Pct"],
                                             g["Retail_Pct"], g["Total_Holders"])]
        except Exception:
            continue
    return out, tdcc_asof


def load_whale_deltas(not_after: str | None = None) -> tuple[dict, list, str | None]:
    """Read the shared raw-verified weekly derivative; never recompute from old snapshots."""
    from scripts.tdcc_whale_radar import load_radar_artifact
    try:
        m, report = load_radar_artifact(BASE_DIR, not_after=not_after)
    except (ValueError, OSError, KeyError) as exc:
        # Optional display evidence remains UNKNOWN; errors must be diagnosable.
        print(f"[WARN] TDCC whale radar unavailable: {exc}")
        return {}, [], None
    m = m.rename(columns={"whale_delta": "wd", "retail_delta": "rd"})
    per = {r["ticker"]: (round(r["whale1000"], 2), r["wd"], round(r["retail100"], 2), r["rd"])
           for _, r in m.iterrows() if r["wd"] == r["wd"]}
    radar = m[(m["wd"] >= 3.0) & (m["rd"] < 0)].sort_values("wd", ascending=False)
    radar_rows = [{"tk": r["ticker"], "w": round(r["whale1000"], 2), "wd": r["wd"],
                   "rt": round(r["retail100"], 2), "rd": r["rd"]}
                  for _, r in radar.iterrows()]
    asof = _coerce_date(report["as_of_date"])
    return per, radar_rows, asof.isoformat() if asof else None


def _conviction(row: dict, a: dict, m: dict, wd: float | None, rd: float | None) -> tuple[float, str]:
    """rere 重倉四味評分(0-4):籌碼多重確認/貼主力成本/品質下檔/催化劑近。
    2026-07-06 南茂教訓後編碼:四味>=3.5 時軟否決只能降級不能歸零。"""
    from datetime import date
    s1 = 0.0  # 籌碼多重確認
    hits = 0
    if wd is not None and wd >= 2.5:
        hits += 1
    if (m.get("inet") or 0) >= 2000:
        hits += 1
    if (a.get("t5") or 0) > 0 and (a.get("t20") or 0) > 0:
        hits += 1
    if rd is not None and rd < 0:
        hits += 1
    s1 = 1.0 if hits >= 2 else (0.5 if hits == 1 else 0.0)
    s2 = 0.0  # 貼主力成本
    ic, close = m.get("ic"), row.get("closev")
    if ic and close:
        prem = close / ic - 1
        s2 = 1.0 if -0.03 <= prem <= 0.05 else (0.5 if prem <= 0.10 else 0.0)
    s3 = 0.0  # 品質下檔
    om = a.get("operating_margin_latest")
    wr = m.get("wr")
    if (om is not None and om >= 0.10) or (wr is not None and wr >= 58):
        s3 = 1.0
    elif (om is not None and om > 0) or (wr is not None and wr >= 55):
        s3 = 0.5
    # 催化劑:月營收公布窗(每月10日前7天內)視為近催化劑
    day = date.today().day
    s4 = 1.0 if (3 <= day <= 10) else 0.5  # 粗略:財報/營收季常態台股月中前;人工可覆寫
    total = s1 + s2 + s3 + s4
    return total, f"{total:.1f}/4味"


def _position_tier(conv: float, bull: bool) -> str:
    """規劃B: conviction 4味 → 建議倉位分級(rere 試單vs重倉紀律)。純標籤,不自動下單。
       空頭regime一律降一級(右尾型須保守)。"""
    tiers = [(4, "標準倉"), (3, "7成倉"), (2, "試單(半倉)"), (0, "觀望")]
    idx = next(i for i, (th, _) in enumerate(tiers) if conv >= th)
    if not bull and idx < len(tiers) - 1:
        idx += 1  # 空頭降一級
    label = tiers[idx][1]
    return f" · 建議{label}({int(conv)}/4味{'·空頭降級' if not bull else ''})"


def _verdict(row: dict, a: dict, wd_map: dict) -> tuple[str, str]:
    """第一層籌碼判定(與人工規則一致):回 (等級, 說明)。"""
    tk = row["tk"]
    if row.get("state") == "watch":
        return "", ""
    f5, t5 = a.get("f5"), a.get("t5")
    w = wd_map.get(tk)
    wd = w[1] if w else None
    rd = w[3] if w else None
    m = row.get("_m") or {}
    conv, _ = _conviction(row, a, m, wd, rd)
    # Keep the existing evidence classifications. A presentation score must not
    # override the generator's minimum-size rere lane or imply a live trigger.
    if row.get("strategy") == "rere":
        if (wd is not None and wd <= -1) or (rd is not None and rd >= 1):
            return "skip", f"🔴 跳過:籌碼發散(大戶{wd:+.1f}pp/散戶{rd:+.1f}pp)"
        return "check", "需人工確認分點、主力成本與盤中支撐；僅考慮最小試單"
    if f5 is not None and t5 is not None and t5 >= 2000 and f5 <= -2000:
        return "check", f"投信({t5:+,})與外資({f5:+,})方向不同，分點待人工確認"
    if wd is not None and wd >= 3:
        return "check", f"大戶週增{wd:+.1f}個百分點，持股變化仍需搭配分點確認"
    if f5 is not None and f5 >= 1500:
        return "check", f"外資5日{f5:+,}張，仍待分點與盤中支撐確認"
    if f5 is not None and f5 <= -2000:
        # 回測:大賣組 20日 +0.92%/39.4% 顯著弱 → 主lane(20日)維持警告;60日結構倉另議
        return "warn", f"外資5日{f5:+,}張，賣壓需先釐清"
    if f5 is not None and f5 <= -500:
        # 回測:溫和賣組 60日 +7.73%/48.1% ≈ 對照組 → 結構強勢時降級不歸零
        if conv >= 3.5:
            return "check", f"外資5日{f5:+,}張，其他結構訊號較強；仍待人工確認"
        return "warn", f"外資5日{f5:+,}張，賣壓需先釐清"
    return "ok", "已讀取的籌碼欄位未見上述警訊；不代表分點或盤中已確認"


def evidence_verdict(row: dict, a: dict, wd_map: dict, sources: list[dict]) -> tuple[str, str]:
    """Only contemporaneous supplemental evidence can affect the card verdict.

    Stale weekly deltas cannot veto today's rere setup. Missing evidence also
    cannot become an affirmative 'no risk' statement. The generator is untouched.
    """
    by_key = {s["key"]: s for s in sources}
    fresh = lambda key: by_key.get(key, {}).get("status") == "fresh"
    current_row = dict(row)
    if not fresh("production"):
        current_row["_m"] = {}
    verdict, message = _verdict(current_row, a if fresh("snapshot") else {},
                                wd_map if fresh("whale") else {})
    missing = [by_key.get(key, {}).get("label", key) for key in ("snapshot", "production", "whale") if not fresh(key)]
    if missing and row.get("state") != "watch":
        suffix = "；" + "、".join(missing) + "非當期或未確認，需人工補充"
        return (verdict if verdict in ("skip", "warn") else "check", message + suffix)
    return verdict, message


def load_daytrade() -> dict:
    """盤前當沖研究觀察池(scripts/daytrade_prep_screener.py 共用 screen())。
    研究用途,非買賣訊號;失敗回空不影響主頁。"""
    try:
        import sys as _sys
        if str(BASE_DIR) not in _sys.path:
            _sys.path.insert(0, str(BASE_DIR))
        from scripts.daytrade_prep_screener import screen
        df = screen()
        if df is None or df.empty:
            return {"rows": [], "asof": None}
        asof = pd.read_csv(BASE_DIR / "日K資料" / "2330.csv", usecols=["Date"]).iloc[-1, 0][:10]
        return {"rows": df.head(30).to_dict("records"), "asof": asof}
    except Exception as exc:
        print(f"[daytrade] load failed: {exc}")
        return {"rows": [], "asof": None}


def load_holdings() -> list:
    """my_holdings.db(本地 SQLite)→ 持股+最新收盤。"""
    import sqlite3
    p = BASE_DIR / "my_holdings.db"
    if not p.exists():
        return []
    con = sqlite3.connect(str(p))
    rows = con.execute(
        "select ticker, shares, avg_cost, breakeven_price, entry_date, note from holdings").fetchall()
    con.close()
    out = []
    for tk, sh, ac, be, ed, note in rows:
        tk = str(tk).zfill(4)
        last = hwm = trail = hard = None
        f = DAILY_DIR / f"{tk}.csv"
        if f.exists():
            try:
                k = pd.read_csv(f, usecols=["Date", "Close"])
                k["Date"] = k["Date"].astype(str).str[:10]
                last = round(float(k["Close"].iloc[-1]), 2)
                # 規劃A: HWM 移動停利鏡像(收盤HWM自進場日起算,-8%;與 exit_policies
                # trailing_stop_hwm_8pct 同口徑)。硬停損=兩平-10%(規則14)。純顯示,不自動下單。
                since = k[k["Date"] >= str(ed)[:10]] if ed else k.tail(60)
                if len(since):
                    hwm = round(float(since["Close"].max()), 2)
                    trail = round(hwm * 0.92, 2)
                base = float(be or ac or 0)
                if base > 0:
                    hard = round(base * 0.90, 2)
            except Exception:
                pass
        out.append({"tk": tk, "sh": sh, "ac": ac, "be": be, "ed": ed,
                    "note": note or "", "last": last,
                    "hwm": hwm, "trail": trail, "hard": hard})
    return out


def load_model_tags(*, not_after: date | None = None,
                    _inputs: _RenderReadSet | None = None) -> tuple[dict, dict]:
    """production predictions 最新檔 → 每檔模型/主力/ChipK/短波 tag + 市場層資訊。"""
    pick = _latest_dated_file(
        BASE_DIR / "ml" / "models",
        prefix="predictions_",
        suffix=".csv",
        date_format="%Y-%m-%d",
        not_after=not_after,
    )
    if not pick:
        raise DashboardLineageError("No certified production prediction input selected for this entry plan")
    source_path, source_date = pick
    if not_after is not None and source_date != not_after:
        raise DashboardLineageError("Production prediction date differs from the certified entry plan")
    df = _certified_predictions(source_path, "production", inputs=_inputs, low_memory=False)
    df["ticker"] = df["ticker"].astype(str).str.split(".").str[0].str.zfill(4)

    def g(r, k, nd=2, scale=1.0):
        try:
            v = float(r.get(k)) * scale
            return round(v, nd) if np.isfinite(v) else None
        except Exception:
            return None

    def s(r, k):
        v = r.get(k)
        if v is None or (isinstance(v, float) and v != v):
            return None
        return str(v)

    tags = {}
    for _, r in df.iterrows():
        tags[r["ticker"]] = {
            "sig": s(r, "signal"), "pu": g(r, "up_prob", 3), "pd": g(r, "down_prob", 3),
            "pr": g(r, "pred_return_20d", 4), "rec": s(r, "recommendation"),
            "rt": s(r, "risk_tags") or "", "wr": g(r, "historical_win_rate", 1),
            "ar": g(r, "historical_avg_return", 2),
            "ic": g(r, "inst_cost_10d"), "inet": g(r, "inst_net_10d", 0, 1e-3),
            "iv": g(r, "inst_vol_pct_10d", 1),
            "poc20": g(r, "poc_20d"), "poc60": g(r, "poc_60d"),
            "va": [g(r, "va_low_20d"), g(r, "va_high_20d")],
            "sw": g(r, "shortwave_score", 2), "swa": s(r, "shortwave_action"),
            "swr": s(r, "shortwave_reason") or "", "swf": s(r, "shortwave_risk_flags") or "",
            "swz": [g(r, "shortwave_entry_zone_low"), g(r, "shortwave_entry_zone_high"),
                    s(r, "shortwave_entry_zone_status")],
            "mo": s(r, "momentum_action"), "mos": g(r, "momentum_score", 2),
        }
    r0 = df.iloc[0]
    mkt = {"senti": s(r0, "market_sentiment"), "med": g(r0, "market_return_median", 4),
           "guard": s(r0, "guardrail_trigger_reason"),
           "macro": s(r0, "macro_event_next_title"), "mdays": g(r0, "macro_event_nearest_days", 0),
           "msenti": s(r0, "macro_market_sentiment_label"),
           "asof": s(r0, "date") or source_date.isoformat(), "file": source_path.name}
    return tags, mkt


def build(path: Path) -> str:
    from scripts.entry_artifact_lineage import load_entry_artifact
    inputs = _RenderReadSet()
    inputs.track(path)
    inputs.track(Path(str(path) + ".manifest.json"))
    d = load_entry_artifact(path)
    rows = d.get("rows") or []
    for r in rows:
        r["tk"] = str(r.get("stock", "")).split()[0]
        r["strategy"] = strategy_of(r)
        r["state"] = state_of(r)
        # 2026-09-23 審查:EOD 名單一律未經盤中確認(intraday_verified=false),
        # 可執行卡(go/small)明確標注,確認流程回寫 true 後才視為可觸發
        if r.get("intraday_verified") is not True and r["state"] in ("go", "small"):
            r["status"] = f"{r.get('status', '')}·⏳待盤中確認"
        m = re.match(r"([\d.]+)\s*-\s*([\d.]+)", str(r.get("zone") or ""))
        r["zlo"], r["zhi"] = (float(m.group(1)), float(m.group(2))) if m else (None, None)
        try:
            r["stopv"] = float(r.get("stop"))
        except Exception:
            r["stopv"] = None

    model_source, model_source_date = _entry_model_metadata(d)
    list_tickers = {r["tk"] for r in rows}
    idx = load_indices()
    stocks, universe_sources = load_universe(
        list_tickers,
        model_source=model_source,
        model_source_date=model_source_date,
        _inputs=inputs,
    )
    tags, mkt = load_model_tags(not_after=model_source_date, _inputs=inputs)
    for tk, m in tags.items():
        if tk in stocks:
            stocks[tk]["m"] = m
    tdcc, tdcc_asof = load_tdcc()
    for tk, t8 in tdcc.items():
        if tk in stocks:
            stocks[tk]["t8"] = t8
    wd_map, radar, whale_asof = load_whale_deltas(not_after=model_source_date)
    source_status = _source_freshness(d.get("trade_date"), [
        ("snapshot", "四面向", universe_sources.get("snapshot"), "daily"),
        ("data_a", "20D模型", universe_sources.get("data_a"), "daily"),
        ("production", "正式標籤", mkt.get("asof"), "daily"),
        ("tdcc", "集保週報", tdcc_asof, "weekly"),
        ("whale", "大戶雷達", whale_asof, "weekly"),
    ])
    if next(s for s in source_status if s["key"] == "whale")["status"] != "fresh":
        radar = []
    twii_row = next((i for i in idx if i.get("k") == "TWII"), None)
    bull = bool(twii_row and (twii_row.get("vs60") or 0) > 0)
    for r in rows:
        st_ = stocks.get(r["tk"], {})
        a = st_.get("a", {})
        r["_m"] = st_.get("m") or {}
        r["_bull"] = bull
        bars_ = st_.get("o") or []
        r["closev"] = bars_[-1][4] if bars_ else None
        r["last_close"], r["last_close_date"] = st_.get("last_close"), st_.get("last_close_date")
        r["vt"], r["vtx"] = evidence_verdict(r, a, wd_map, source_status)
        r["card"] = card_context(r, d)
        r.pop("_m", None)
        r.pop("_bull", None)
    list_tks = {r["tk"] for r in rows}
    radar = [x for x in radar if x["tk"] in stocks][:12]
    for x in radar:
        x["n"] = stocks[x["tk"]].get("n", "")
        x["s"] = stocks[x["tk"]].get("s", "")
        x["il"] = 1 if x["tk"] in list_tks else 0

    ledger = []
    lp = BASE_DIR / "ml" / "reports" / "entry_filter_ledger.csv"
    if lp.exists():
        lf = pd.read_csv(lp, dtype={"ticker": str, "trade_date": str}).fillna("")
        lf["ticker"] = lf["ticker"].str.zfill(4)
        ledger = lf.to_dict("records")
    ledger_legacy = load_legacy_ledger_summary(lp)

    payload = {"meta": {"trade_date": d.get("trade_date"), "subtitle": d.get("subtitle"),
                        "intro": d.get("intro"),
                        "data_status": d.get("data_status", "UNKNOWN"),
                        "data_warnings": d.get("data_warnings") or [],
                        "discipline": d.get("discipline") or [],
                        "sources": source_status,
                        "generated": datetime.now().strftime("%Y-%m-%d %H:%M")},
               "rows": [{k: r.get(k) for k in
                         ("tk", "stock", "sector", "strategy", "state", "status", "kind", "ptype",
                          "priority", "zone", "stop", "no_chase", "ret20d", "reason",
                          "zlo", "zhi", "stopv", "vt", "vtx", "stale", "card",
                          "source_date", "model_source_date", "data_status", "data_warnings")}
                        for r in rows],
               "radar": radar,
               "idx": idx, "mkt": {**mkt, "guard_context": guard_context(mkt.get("guard"))}, "stocks": stocks, "ledger": ledger,
               "ledger_legacy": ledger_legacy,
               "news_html": load_news_html(), "arena": load_arena(),
               "holdings": load_holdings(),
               "daytrade": load_daytrade()}
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False)
                        .replace("</", "<\\/"))
    # Revalidate the producer-bound plan, not the renderer-mutated row objects.
    # main evaluates build fully before writing OUT, so any failure keeps it.
    load_entry_artifact(path)
    inputs.assert_unchanged()
    return html


HTML = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股選股觀察</title>
<link rel="icon" href="data:,">
<style>
:root{
  --page:#161511; --card:#211f18; --line:#363328;
  --ink:#ede9da; --ink2:#b8b4a4; --muted:#8a8778;
  --pill:#2e5d45; --pill-ink:#ede9da;
  --go:#42a06b; --go-wash:#233127; --dn:#e05e49; --dn-wash:#38251f;
  --wt:#b3841f; --wt-wash:#332b18; --sm:#8a8778; --sm-wash:#2a2820;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:16px/1.55 "Segoe UI",system-ui,sans-serif;padding:24px 3.5vw 60px}
h1{font-family:Georgia,"Times New Roman",serif;font-weight:800;font-size:36px;
  display:inline-block;margin:0 10px 0 0}
.pill{display:inline-block;background:var(--pill);color:var(--pill-ink);
  border-radius:6px;padding:4px 14px;font-family:Consolas,monospace;font-size:16px;
  font-weight:700;vertical-align:5px}
.meta{color:var(--ink2);font-family:Consolas,monospace;font-size:13.5px;margin:8px 0 4px}
.meta .tag{background:var(--sm-wash);border-radius:4px;padding:2px 8px;margin-right:6px}
.freshline{font-family:Consolas,monospace;font-size:12.5px;margin:8px 0;padding:7px 10px;
  border:1px solid var(--line);border-radius:6px;color:var(--ink2)}
.freshline.has-stale{border-color:var(--wt);background:var(--wt-wash);color:var(--wt)}
.fresh-ok{color:var(--go)} .fresh-bad{color:var(--wt);font-weight:700}
.topbar{display:flex;flex-wrap:wrap;gap:14px;align-items:stretch;margin:16px 0}
.mktcard{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px 18px;flex:1 1 420px;display:flex;gap:18px;align-items:center}
.mktcard .nm{font-family:Georgia,serif;font-size:20px;font-weight:800}
.mktcard .cl{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums}
.mktcard .st{font-family:Consolas,monospace;font-size:13px;color:var(--ink2);line-height:1.7}
.up{color:var(--dn)} .dwn{color:var(--go)} /* 台股慣例:紅漲綠跌 */
.srch{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px 18px;flex:1 1 300px}
.srch label{font-family:Consolas,monospace;font-size:13px;color:var(--ink2);display:block;margin-bottom:8px}
.srch input{width:100%;background:var(--page);border:1px solid var(--line);color:var(--ink);
  border-radius:6px;padding:9px 12px;font-size:16px;font-family:Consolas,monospace}
.srch input:focus{outline:2px solid var(--pill)}
.stratbox{border:1px solid var(--line);border-radius:12px;padding:4px 18px 18px;margin:22px 0}
.stratbox.s-main{border-top:4px solid var(--go)}
.stratbox.s-rere{border-top:4px solid var(--dn)}
.strath{font-family:Georgia,serif;font-size:24px;font-weight:800;margin:14px 0 2px}
.strath .sub{font-family:Consolas,monospace;font-size:13px;color:var(--muted);font-weight:400;margin-left:10px}
.sec{color:var(--ink2);font-family:Consolas,monospace;font-size:14px;
  margin:20px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;border-left:3px solid var(--line);min-width:0}
.c-go{border-left-color:var(--go)} .c-small{border-left-color:var(--sm)}
.c-watch{border-left-color:var(--wt)}
.tk{font-size:24px;font-weight:800}
.mkt{font-size:12px;background:var(--sm-wash);border:1px solid var(--line);
  border-radius:4px;padding:1px 6px;vertical-align:3px;margin-left:6px;color:var(--ink2)}
.badge{float:right;font-size:13px;font-weight:700;border-radius:6px;padding:4px 10px;font-family:Consolas,monospace}
.b-go{color:var(--go);background:var(--go-wash)}
.b-sm{color:var(--ink2);background:var(--sm-wash)}
.b-wt{color:var(--wt);background:var(--wt-wash)}
.px{font-size:12.5px;font-family:Consolas,monospace;border-radius:4px;padding:1px 7px;margin-left:8px;
  color:var(--go);background:var(--go-wash)}
.zone{margin:12px 0 6px}
.zlab{color:var(--muted);font-family:Consolas,monospace;font-size:12.5px;margin-right:10px}
.zval{font-family:Consolas,monospace;font-size:23px;font-weight:800;font-variant-numeric:tabular-nums}
.lv{display:flex;gap:18px;color:var(--ink2);font-size:14px;margin:4px 0 8px;font-variant-numeric:tabular-nums}
.lv b{color:var(--ink)} .lv .sv{color:var(--dn)}
.rsn{color:var(--ink2);font-size:13.5px;border-top:1px dashed var(--line);padding-top:8px}
.stat{color:var(--muted);font-family:Consolas,monospace;font-size:12.5px;margin-top:6px}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px;color:var(--muted);font-size:14px}
.disc{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px 18px 14px 34px;margin-top:8px;color:var(--ink2);font-size:14.5px}
.disc li{margin:6px 0}
#overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:9}
#panel{position:fixed;top:2vh;left:50%;transform:translateX(-50%);width:min(1100px,96vw);
  max-height:95vh;overflow-y:auto;background:var(--page);border:1px solid var(--line);
  border-radius:12px;padding:22px 28px;display:none;z-index:10}
.back{background:var(--ink);color:var(--page);border:none;border-radius:6px;
  padding:6px 14px;font-family:Consolas,monospace;font-size:13px;cursor:pointer;font-weight:700}
.crumb{color:var(--muted);font-size:13px;margin-left:10px}
.dhead{display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;
  margin:14px 0 6px;border-bottom:2px solid var(--ink);padding-bottom:12px}
.dstats{text-align:right;font-family:Consolas,monospace;font-size:13.5px;color:var(--ink2);line-height:1.9}
.dstats b{color:var(--ink)}
.company{color:var(--ink2);font-size:15px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:14px 0}
.leg{color:var(--muted);font-size:12.5px;font-family:Consolas,monospace;margin-top:4px}
.acols{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.abox{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.abox h3{margin:0 0 8px;font-family:Georgia,serif;font-size:17px;border-bottom:1px solid var(--line);padding-bottom:6px}
.arow{display:flex;justify-content:space-between;padding:4px 0;font-size:14px}
.arow .k{color:var(--ink2)} .arow .v{font-family:Consolas,monospace;font-weight:700;font-variant-numeric:tabular-nums}
.v.pos{color:var(--dn)} .v.neg{color:var(--go)} /* 紅正綠負,台股慣例 */
.v.warn{color:var(--wt)}
details.dled{background:var(--card);border:1px solid var(--line);border-radius:10px;
  margin:10px 0;overflow:hidden}
details.dled summary{cursor:pointer;padding:12px 16px;font-family:Consolas,monospace;
  font-size:14.5px;color:var(--ink);list-style:none;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
details.dled summary::before{content:"▸";color:var(--muted);transition:.15s}
details.dled[open] summary::before{transform:rotate(90deg)}
details.dled summary:hover{background:var(--sm-wash)}
.lsum{font-family:Consolas,monospace;font-size:13px;border-radius:4px;padding:2px 8px}
.lsum.pos{color:var(--dn);background:var(--dn-wash)} .lsum.neg{color:var(--go);background:var(--go-wash)}
.lsum.mut{color:var(--muted);background:var(--sm-wash)}
.ltbl{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.ltbl th{font-family:Consolas,monospace;font-size:12px;color:var(--muted);text-align:left;
  padding:7px 14px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.ltbl td{padding:7px 14px;border-bottom:1px solid var(--line);font-size:14px}
.ltbl tr:last-child td{border-bottom:none}
.ltbl .st-h{color:var(--wt)} .ltbl .st-s{color:var(--dn)} .ltbl .st-e{color:var(--ink2)}
.ltbl .st-u{color:var(--muted)} .ltbl .st-p{color:var(--muted)}
.rp{font-weight:700} .rp.pos{color:var(--dn)} .rp.neg{color:var(--go)}
.tabs{display:flex;gap:8px;margin:4px 0 14px;flex-wrap:wrap}
.tabbtn{background:var(--card);border:1px solid var(--line);color:var(--ink2);border-radius:8px;
  padding:8px 18px;font-family:Consolas,monospace;font-size:15px;font-weight:700;cursor:pointer}
.tabbtn.active{background:var(--pill);color:var(--pill-ink);border-color:var(--pill)}
.newsbody{padding:4px 18px 14px}
.newsbody h4{font-family:Georgia,serif;font-size:16px;margin:14px 0 4px;color:var(--ink)}
.newsbody ul{margin:4px 0;padding-left:22px}
.newsbody li{margin:4px 0;font-size:14px;color:var(--ink2)}
.newsbody li b{color:var(--ink)}
.newsbody code{background:var(--sm-wash);border-radius:3px;padding:0 5px;font-size:12.5px;color:var(--wt)}
.newsbody p{font-size:13.5px;color:var(--ink2)}
.tblwrap{overflow-x:auto}
.hint{color:var(--muted);font-weight:400;margin-left:10px}
.idxgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px;width:100%}
.idxtile{background:var(--page);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.idxtile .inm{font-family:Consolas,monospace;font-size:12.5px;color:var(--ink2)}
.idxtile .icl{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
.idxtile .ich{font-family:Consolas,monospace;font-size:12px;font-weight:700}
.idxtile .idt{font-family:Consolas,monospace;font-size:10.5px;color:var(--muted)}
.tfbar{display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.tfbtn{background:var(--page);border:1px solid var(--line);color:var(--ink2);border-radius:6px;
  padding:4px 14px;font-family:Consolas,monospace;font-size:13px;font-weight:700;cursor:pointer}
.tfbtn.active{background:var(--pill);color:var(--pill-ink);border-color:var(--pill)}
.maline{margin-left:auto;font-family:Consolas,monospace;font-size:13px;color:var(--ink2)}
.kwrap{position:relative}
.kx{position:absolute;top:0;bottom:0;width:0;border-left:1px dashed var(--muted);
  display:none;pointer-events:none}
.ktip{position:absolute;top:8px;display:none;background:var(--card);border:1px solid var(--line);
  border-radius:6px;padding:6px 10px;font-family:Consolas,monospace;font-size:12.5px;line-height:1.7;
  pointer-events:none;z-index:5;white-space:nowrap;color:var(--ink2)}
.ktip b{color:var(--ink)}
.chip{display:inline-block;font-family:Consolas,monospace;font-size:11.5px;border-radius:4px;
  padding:1px 7px;margin:2px 6px 0 0;background:var(--sm-wash);color:var(--ink2);border:1px solid var(--line)}
.ch-go{color:var(--go)} .ch-dn{color:var(--dn)} .ch-wt{color:var(--wt)}
.tags{margin-top:8px;line-height:1.9}
.verdict{margin:8px 0 2px;font-size:13px;font-weight:700;border-radius:6px;padding:5px 10px;
  font-family:Consolas,monospace}
.v-ok{color:var(--go);background:var(--go-wash)}
.v-warn{color:var(--wt);background:var(--wt-wash)}
.v-check{color:#8fc1ff;background:#1e2733}
.v-skip{color:var(--dn);background:var(--dn-wash)}
.card-state{display:inline-block;font-size:13px;font-weight:700;padding:4px 9px;border-radius:5px;margin-bottom:10px}
.card-title{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.stock-link{font:inherit;color:var(--ink);background:none;border:0;padding:0;text-align:left;cursor:pointer}
.stock-link.tk{font-size:24px;font-weight:800}
.stock-link:hover{text-decoration:underline}.stock-link:focus-visible{outline:2px solid #8fc1ff;outline-offset:4px}
.card-horizon{font-size:12px;color:var(--ink2);margin:5px 0 10px}
.card-action{font-size:14px;color:var(--ink);line-height:1.65;margin-bottom:8px}
.card-close{font-size:13px;color:var(--ink2);margin:8px 0 10px;line-height:1.65}
.card-close b{color:var(--ink);font-size:17px;font-variant-numeric:tabular-nums}
.card-evidence{padding-left:18px;margin:9px 0;color:var(--ink2);font-size:13px}
.card-evidence li{margin:4px 0}
.card-price-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}
.card-price-grid>div{background:var(--page);padding:8px 10px;border-radius:5px;min-width:0}
.card-price-grid .observe{grid-column:1/-1}
.card-price-grid .zlab{display:block;margin:0 0 3px}.card-price-grid b{overflow-wrap:anywhere}
.card-unknown{font-size:12.5px;color:var(--wt);border-left:2px solid var(--wt);padding-left:9px;margin:10px 0}
.card-source{font-size:11.5px;color:var(--ink2);margin:8px 0;overflow-wrap:anywhere}
.card-details{border-top:1px solid var(--line);margin-top:12px;padding-top:8px;font-size:12.5px}
.card-details summary{cursor:pointer;color:var(--ink2)}.card-details .rsn{border:0;overflow-wrap:anywhere}
.card-top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
.card-top .card-state{margin-bottom:0}
.card-tag{font-size:11.5px;color:var(--ink2);background:var(--sm-wash);border-radius:4px;padding:2px 8px;white-space:nowrap}
.card-tag.is-rere{color:var(--go);background:var(--go-wash)}
.card-prices{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:6px;margin:8px 0}
.card-prices>div{background:var(--page);padding:6px 8px;border-radius:5px;min-width:0}
.card-prices .zlab{display:block;font-size:11px;color:var(--muted);margin:0 0 2px}
.card-prices b{overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.card-why{font-size:13px;color:var(--ink2);line-height:1.5;margin:6px 0 2px}
.guard{background:var(--wt-wash);border:1px solid var(--wt);border-radius:8px;color:var(--wt);
  font-family:Consolas,monospace;font-size:13px;padding:8px 14px;margin:2px 0 6px}
footer{color:var(--muted);font-size:12.5px;margin-top:36px;font-family:Consolas,monospace}
@media (max-width:720px){.zval{font-size:20px}.mktcard{flex-wrap:wrap}body{padding:16px 12px 40px}
 .stratbox{padding:4px 10px 12px}.strath{font-size:22px}.strath .sub{display:block;margin:6px 0;line-height:1.65}
 .card{padding:14px}.lv{flex-wrap:wrap}#panel{padding:16px 14px}.idxgrid{grid-template-columns:repeat(auto-fit,minmax(128px,1fr))}}
</style></head><body>
<div id="hdr"></div>
<div class="topbar" id="topbar"></div>
<div class="tabs" id="tabs">
  <button class="tabbtn active" data-tab="list">📋 選股觀察</button>
  <button class="tabbtn" data-tab="mine">💼 我的持股</button>
  <button class="tabbtn" data-tab="news">📰 新聞</button>
  <button class="tabbtn" data-tab="arena">🏆 投資賽</button>
  <button class="tabbtn" data-tab="daytrade">⚡ 當沖研究</button>
</div>
<div id="tab-list">
  <div id="view"></div>
  <div id="ledgerSec"></div>
  <div class="sec">▼ 紀律(canonical 產生器隨單附帶)</div>
  <ul class="disc" id="disc"></ul>
</div>
<div id="tab-mine" hidden></div>
<div id="tab-news" hidden></div>
<div id="tab-arena" hidden></div>
<div id="tab-daytrade" hidden></div>
<div id="overlay" data-close="1"></div>
<div id="panel"></div>
<footer>純呈現層(訊號以 generate_entry_candidates.py 為準)· K線/MACD 紅漲綠跌(台股慣例)·
四大面向數據來自每日 snapshot · 非投資建議 · generated <span id="gen"></span></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const ROWS = D.rows, ST = D.stocks, IX = D.idx||[], MK = D.mkt||{};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.getElementById('gen').textContent = D.meta.generated;
const nGo = ROWS.filter(r=>r.state==='go').length, nSm = ROWS.filter(r=>r.state==='small').length,
      nWt = ROWS.filter(r=>r.state==='watch').length;
const SOURCES = D.meta.sources||[], staleSources = SOURCES.filter(s=>s.status!=='fresh');
const sourceHtml = SOURCES.map(s=>{
  const ok=s.status==='fresh', value=s.asof||'缺資料';
  const note=s.status==='future'?'（日期超出名單基準）':ok?'':'（需 ≥ '+(s.expected||'待確認')+'）';
  return `<span class="${ok?'fresh-ok':'fresh-bad'}">${esc(s.label)} ${esc(value)}${esc(note)}</span>`;
}).join(' · ');
document.getElementById('hdr').innerHTML =
 `<h1>台股選股觀察</h1><span class="pill">${D.meta.trade_date}</span>
  <div class="meta"><span class="tag">${ROWS.length} 檔觀察 · 尚未確認盤中買點</span>
  <span class="tag">${D.meta.subtitle||''}</span>
  ${MK.asof?`<span class="tag">model tags asof ${MK.asof}</span>`:''}</div>
  ${sourceHtml?`<div class="freshline ${staleSources.length?'has-stale':''}">資料基準 · ${sourceHtml}</div>`:''}
  ${(D.meta.data_warnings||[]).length?`<div class="guard">資料待確認 · ${D.meta.data_warnings.map(esc).join('；')}</div>`:''}
  <div class="meta">先看狀態，再看區間與失效價。名單根據盤後資料產生；盤中價格、分點與主力成本仍需確認。</div>
  ${D.meta.intro?`<details class="meta"><summary>名單來源與篩選說明</summary>${esc(D.meta.intro)}</details>`:''}
  ${MK.guard?`<div class="guard"><b>⚠️ ${esc((MK.guard_context||{}).headline||'模型風險檢查警示')}</b><br>
    ${esc((MK.guard_context||{}).scope||'模型診斷不代表正式訊號全部關閉；Champion 以當日 unified 名單為準，rere 仍依自身條件判斷。')}
    <details><summary>查看原始診斷</summary>${esc(MK.guard)}</details></div>`:''}`;
document.getElementById('disc').innerHTML = D.meta.discipline.map(x=>`<li>${x}</li>`).join('');
// ---- 大盤多指數卡 + 搜尋 ----
function spark(cs, w, h, lab){
  const mn = Math.min(...cs), mx = Math.max(...cs), rg = (mx-mn)||1;
  const pts = cs.map((v,i)=>`${(i*(w-4)/(cs.length-1)+2).toFixed(1)},${(h-4-(v-mn)/rg*(h-8)).toFixed(1)}`).join(' ');
  return `<svg width="${w}" height="${h}" role="img" aria-label="${lab||''} 90日走勢">
    <polyline points="${pts}" fill="none" stroke="var(--pill-ink)" stroke-width="1.4" opacity=".8"/></svg>`;
}
const twx = IX.find(i=>i.k==='TWII');
document.getElementById('topbar').innerHTML = `
 <div class="mktcard" style="flex:2 1 560px;flex-wrap:wrap">
   <div class="idxgrid">${IX.map(ix=>{
     const ch = ix.chg*100, cls = ch>=0?'up':'dwn';
     return `<div class="idxtile"><div class="inm">${ix.nm}</div>
       <div class="icl ${cls}">${ix.c[ix.c.length-1].toLocaleString()}</div>
       <div class="ich ${cls}">${ch>=0?'▲':'▼'}${Math.abs(ch).toFixed(2)}%</div>
       ${spark(ix.c, 128, 30, ix.nm)}<div class="idt">${ix.d}</div></div>`;}).join('')}</div>
   <div class="st" style="width:100%;margin-top:4px">
     ${twx?`加權 vs MA20 <b class="${twx.vs20>=0?'up':'dwn'}">${(twx.vs20*100).toFixed(1)}%</b>
      · vs MA60 <b class="${twx.vs60>=0?'up':'dwn'}">${(twx.vs60*100).toFixed(1)}%</b>`:''}
     ${MK.senti?` · 模型市場情緒 <b class="${MK.senti.includes('空')?'dwn':'up'}">${MK.senti}</b>
      · 全市場20D預測中位 <b class="${MK.med>=0?'up':'dwn'}">${(MK.med*100).toFixed(1)}%</b>`:''}
     ${MK.macro?`<br>宏觀:${MK.macro}(${MK.mdays!==null?MK.mdays+'天後':''}) · 情緒 ${MK.msenti||''}`:''}</div>
 </div>
 <div class="srch"><label>🔎 查詢個股(全 universe ${Object.keys(ST).length} 檔,四大面向+K線)</label>
   <input id="q" list="tkl" placeholder="輸入代號或名稱,如 2330 / 台積電" autocomplete="off">
   <datalist id="tkl">${Object.entries(ST).map(([k,v])=>`<option value="${k}">${v.n||''} ${v.s||''}</option>`).join('')}</datalist>
 </div>`;
document.getElementById('q').addEventListener('change', e=>{
  let v = e.target.value.trim();
  if(!(v in ST)){
    const hit = Object.entries(ST).find(([k,s])=>s.n && s.n===v) ||
                Object.entries(ST).find(([k,s])=>s.n && s.n.includes(v));
    v = hit ? hit[0] : v;
  }
  if(v in ST){ showD(v); e.target.value=''; }
});
// ---- 兩策略分區 ----
function recCls(rec){ return rec.includes('買')?'ch-go':(rec.includes('賣')?'ch-dn':'ch-wt'); }
function card(r){
  const info = r.card||{label:'暫緩 · 待資料確認',tone:'warn',action:'先補齊資料與盤中確認。',basis:[],warnings:['卡片資料尚未更新'],horizon:r.strategy==='rere'?60:20,size:'確認條件後再決定部位'};
  const m = (ST[r.tk]||{}).m || {};
  // 卡片只放「會改變決策的證據」(2026-09-04 BT-entry-overlay-flags 裁決,見 STRATEGY_overview 回測票5):
  // champion 模型的 v1 訊號 / recommendation / 主力成本 / 歷史勝率 對短打卡片無決策價值——
  // 歷史勝率是 recommendation 類別常數;recommendation 來自與產生器(dataA)不同的模型且 OOS 反向;
  // 分歧盛行率 50%;法人賣壓六年回測無穩定劣化(勝率反高、左尾近零)。一律不上卡。
  // risk_tags 採 deny-list:先剝掉 💡 提示尾綴,再只濾掉上述噪音片段;其餘(流動性不足/本業虧損/
  // 籌碼頂部背離/處置股/短線偏熱/預測值異常/乖離過大…)對應 CLAUDE.md 硬擋規則,保留。
  const NOISE = ['分歧','法人10日賣壓','強力買進門檻','Guardrail','降倉通過'];
  const hardRisk = (m.rt||'').split('⚠')
    .map(s=>s.replace(/^️/,'').replace(/\\s*💡.*$/,'').trim())
    .filter(s=>s && !NOISE.some(k=>s.includes(k)));
  const chips = [
    ...hardRisk.map(s=>`<span class="chip ch-wt">⚠ ${esc(s)}</span>`),
  ].filter(Boolean).join('');
  const VCLS = {skip:'v-skip', warn:'v-warn', check:'v-check', ok:'v-ok'};
  const verd = r.vtx ? `<div class="verdict ${VCLS[r.vt]||''}">籌碼提示 · ${esc(r.vtx)}</div>` : '';
  const isStale = r.state==='watch' && r.stale;
  const tone = info.tone==='skip'?'v-skip':info.tone==='check'?'v-check':'v-warn';
  // 收斂(2026-10-07 PM):卡面只放「這檔才有的資訊」——狀態、代號、收盤相對區間、三個價位、一行篩選理由、硬風險 chip。
  // 每張卡相同的制式句(動作提示/依據/尚缺/資料日期)移入展開區;共同紀律在頁首 #disc 講一次。
  const PT = {shakeout:'蹲點型', ignition:'發動型', shallow:'淺洗盤型', shakeout_v2:'蹲點型v2', shallow_v2:'淺洗盤型v2'};
  const laneTag = r.strategy==='rere' ? `rere · ${PT[r.ptype]||'蹲點型'} · ${info.horizon} 日`
                : (r.state==='watch' ? `觀察卡 · ${info.horizon} 日` : `主策略 · ${info.horizon} 日`);
  const lc = info.last_close||{};
  const POS = {inside:'在區間內', within:'在區間內', above:'高於區間', below:'低於區間'};
  const closeLine = (lc.price!==null && lc.price!==undefined)
    ? `收盤 <b>${esc(lc.price)}</b>（${esc(String(lc.date||'').slice(5))}）· ${esc(POS[lc.position]||lc.message||'')}`
    : esc(lc.message||'盤後價格日期未確認');
  const shortLabel = String(info.label||'').replace(/^(觀察|暫緩)\\s*·\\s*/,'');
  // 一行篩選理由:去掉【lane】前綴與「;60日波段…」制式尾句
  const why = String(r.reason||'').replace(/^【[^】]*】/,'').split(/[;；]/)[0].trim();
  return `<article class="card c-${r.state}${info.tone==='skip'?' c-skip':''}">
    <div class="card-top"><span class="card-state ${tone}">${esc(shortLabel||info.label)}</span><span class="card-tag${r.strategy==='rere'?' is-rere':''}">${esc(laneTag)}</span></div>
    <div class="card-title"><button type="button" class="stock-link tk" data-tk="${esc(r.tk)}" aria-label="查看 ${esc(r.stock)} 詳情">${esc(r.stock)}</button>${r.sector?`<span class="mkt">${esc(r.sector)}</span>`:''}</div>
    <div class="card-close">${closeLine}</div>
    <div class="card-prices"><div class="observe"><span class="zlab">區間</span><b class="zval">${esc(r.zone||'待重估')}</b></div>
      <div><span class="zlab">不追</span><b>${esc(r.no_chase||'待重估')}</b></div>
      <div><span class="zlab">失效</span><b class="up">${esc(r.stop||'待重估')}</b></div></div>
    ${why?`<div class="card-why">${esc(why)}</div>`:''}
    ${isStale?`<div class="verdict v-skip">${esc(r.stale)}</div>`:''}
    ${chips?`<div class="tags">${chips}</div>`:''}
    ${r.kind==='veto'?`<div class="verdict v-skip">否決原因 · ${esc(r.status||r.reason||'需確認原始否決條件')}</div>`:r.vt==='skip'?verd:''}
    ${info.warnings.length?`<div class="card-unknown">${info.warnings.map(esc).join('；')}</div>`:''}
    <details class="card-details"><summary>詳細 · 動作提示、資料日期、模型值、完整理由</summary>
      <div class="stat">${esc(info.action)} ${esc(info.size)}。</div>
      <ul class="card-evidence">${info.basis.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>
      <div class="stat">尚缺：盤中進區間守穩、人工籌碼確認。</div>
      <div class="stat">型態截至 ${esc(info.source_date||'未提供')} · 模型截至 ${esc(info.model_source_date||'未提供')}${r.strategy==='rere'?'（不作否決）':''}</div>
      <div class="rsn">${esc(r.reason||'未提供篩選理由')}</div>
      <div class="stat">原始狀態：${esc(r.status||'未提供')} · 名單順位 #${esc(r.priority||'—')}</div>
      <div class="stat">20 日模型值：${esc(r.ret20d||'未提供')}（模型參考值，不是報酬保證；2026-10-07 起不過濾、不排序${r.strategy==='rere'?'，不決定 rere 入列':''}）</div>
      ${r.kind==='veto'||r.vt==='skip'?'':verd}
      <button type="button" class="back" data-tk="${esc(r.tk)}">查看 K 線與四面向</button>
    </details>
  </article>`;
}
// 否決沉底(2026-09-05):產生器的順位(#priority)只反映型態/族群分數,籌碼否決是儀表板渲染時
// 才判的;不重排的話「跳過」的卡會壓在可執行的卡前面(景碩 #2 案例)。這裡做穩定分區:
// 未否決者保持原順序在前,vt==='skip' 或過期 watch 沉到區塊最後。#priority 保留以對回名單/信件。
function isSunk(r){ return r.kind==='veto' || r.vt==='skip' || (r.state==='watch' && !!r.stale); }
function sinkSkipped(arr){ return arr.filter(r=>!isSunk(r)).concat(arr.filter(isSunk)); }
function sunkNote(arr){ const n = arr.filter(isSunk).length; return n ? `,${n} 檔否決沉底` : ''; }
function strat(name, key, subtitle){
  const rs = ROWS.filter(r=>r.strategy===key);
  const go = sinkSkipped(rs.filter(r=>r.state==='go')), sm = sinkSkipped(rs.filter(r=>r.state==='small')),
        wt = sinkSkipped(rs.filter(r=>r.state==='watch')), blocked=rs.filter(r=>r.state==='veto');
  let h = `<div class="stratbox s-${key}"><div class="strath">${name}<span class="sub">${subtitle}</span></div>`;
  if(key==='rere'){
    /* rere lane 一律強制小倉,不分 GO/SMALL */
    const act = sinkSkipped(go.concat(sm));
    h += `<div class="sec">▼ 型態觀察 ${act.length} 檔（盤中與人工籌碼尚待確認${sunkNote(act)}）</div>`;
    h += act.length ? `<div class="grid">${act.map(card).join('')}</div>` : '<div class="empty">本日無訊號(rere 為日條件,0 屬正常)</div>';
  } else {
    h += `<div class="sec">▼ 條件篩選入列 · 待盤中確認（${go.length} 檔${sunkNote(go)}）</div>`;
    h += go.length ? `<div class="grid">${go.map(card).join('')}</div>` : '<div class="empty">本日無</div>';
    h += `<div class="sec">▼ 較保守觀察 · 確認後最多小倉（${sm.length} 檔${sunkNote(sm)}）</div>`;
    h += sm.length ? `<div class="grid">${sm.map(card).join('')}</div>` : '<div class="empty">本日無</div>';
  }
  if(wt.length){
    h += `<div class="sec" style="color:var(--wt)">▼ 觀察 · 等回檔或資料補齊（${wt.length} 檔${sunkNote(wt)}）</div><div class="grid">${wt.map(card).join('')}</div>`;
  }
  if(blocked.length) h += `<div class="sec">▼ 暫緩 · 有否決條件（${blocked.length} 檔）</div><div class="grid">${blocked.map(card).join('')}</div>`;
  return h + '</div>';
}
function radarSec(){
  const R = D.radar||[];
  if(!R.length) return '';
  return `<div class="stratbox" style="border-top:4px solid var(--wt)">
    <div class="strath">📡 大戶週增雷達<span class="sub">千張大戶週增≥3pp且散戶減(TDCC週頻) · 僅供人工判斷,非名單股部位比主lane再砍半</span></div>
    <div class="tblwrap"><table class="ltbl"><thead><tr>
      <th>股票</th><th>千張大戶%</th><th>週增</th><th>散戶%</th><th>週減</th></tr></thead><tbody>` +
    R.map(x=>`<tr><td><span data-tk="${x.tk}" style="cursor:pointer;font-weight:700">${x.tk} ${x.n||''}</span>${x.s?`<span class="mkt">${x.s}</span>`:''}${x.il?'<span class="chip ch-go">已在名單</span>':''}</td>
      <td>${x.w}%</td><td><span class="rp ${x.wd>=0?'pos':'neg'}">${x.wd>=0?'+':''}${x.wd}</span></td>
      <td>${x.rt}%</td><td><span class="rp ${x.rd>=0?'pos':'neg'}">${x.rd>=0?'+':''}${x.rd}</span></td></tr>`).join('') +
    '</tbody></table></div></div>';
}
/* rere 區置頂(用戶 2026-08-31 指示:南亞 1303 案例,實戰有效的 lane 不可沉底) */
document.getElementById('view').innerHTML =
  strat('rere 方法 · 洗盤轉強', 'rere', '系統依既有型態篩選，非 rere 本人推薦名單。模型不否決 · 最小試單 · 60 日觀察 · 失效參考為季線下方 3%') +
  strat('主策略 · 溫和動能', 'main', '型態先篩選，再由 dataA 模型確認方向 · 20 日觀察 · 依失效價管理') +
  radarSec();
// ---- 個股詳情 ----
function ema(arr, n){
  const k = 2/(n+1); let e = arr[0]; const out=[e];
  for(let i=1;i<arr.length;i++){ e = arr[i]*k + e*(1-k); out.push(e); }
  return out;
}
function fmt(v, pct, dec){
  if(v===undefined||v===null||!isFinite(v)) return '—';
  const d = dec!==undefined?dec:2;
  return pct ? (v*100).toFixed(1)+'%' : (+v).toFixed(d);
}
function cl(v, inv){ if(v===undefined||v===null||!isFinite(v)) return '';
  return (v>=0)!==!!inv ? 'pos' : 'neg'; }
const TF_LAB = {d:'日K', m:'月K', y:'年K'};
function sma(arr, n){
  return arr.map((_,i)=>{ const st = Math.max(0,i-n+1);
    const seg = arr.slice(st,i+1); return seg.reduce((a,b)=>a+b,0)/seg.length; });
}
function maText(tk, tf){
  tf = tf || 'd';
  const s = ST[tk] || {};
  const bars = (tf==='m' ? s.om : tf==='y' ? s.oy : s.o) || [];
  const cl = bars.map(b=>b[4]);
  const u = {d:'日', m:'月', y:'年'}[tf];
  const f = n => cl.length >= 1 ? sma(cl,n)[cl.length-1].toFixed(2) : '—';
  return `${u}MA5 <b style="color:#ffc94d">${f(5)}</b> ·
    ${u}MA10 <b style="color:#e39bf2">${f(10)}</b> ·
    ${u}MA20 <b style="color:#8fc1ff">${f(20)}</b>`;
}
function candle(tk, tf){
  tf = tf || 'd';
  const s = ST[tk], row = ROWS.find(r=>r.tk===tk);
  const bars = (tf==='m' ? s.om : tf==='y' ? s.oy : s.o) || [];
  if(!bars.length) return '<div class="empty">無K線資料</div>';
  const W=1000, H=300, P=30;
  const dates = bars.map(b=>b[0]), closes = bars.map(b=>b[4]);
  const ma5 = sma(closes,5), ma10 = sma(closes,10), ma20 = sma(closes,20);
  let iH=0, iL=0;
  bars.forEach((b,i)=>{ if(b[2]>bars[iH][2]) iH=i; if(b[3]<bars[iL][3]) iL=i; });
  let lo = bars[iL][3], hi = bars[iH][2];
  const inList = row && tf==='d';
  if(inList){ if(row.stopv) lo = Math.min(lo, row.stopv); if(row.zhi) hi = Math.max(hi, row.zhi); }
  /* 全 universe 支撐/壓力(20日價值區 VA + POC,production 模型附帶) */
  const m2 = (s.m)||{};
  const hasVA = tf==='d' && m2.va && m2.va[0]!==null && m2.va[1]!==null;
  if(hasVA){ lo = Math.min(lo, m2.va[0]); hi = Math.max(hi, m2.va[1]); }
  const rg = (hi-lo)||1;
  const PL = 46, PR = 78;   /* 左=價格刻度欄,右=訊號價位標籤欄(不蓋K棒) */
  const x = i => PL + i*(W-PL-PR)/Math.max(1, dates.length-1);
  const y = v => H-P - (v-lo)/rg*(H-2*P);
  let g = '';
  /* 淡格線 + 左側價格刻度 */
  for(const t of [0.25, 0.5, 0.75]){
    const pv = lo + rg*t, py = y(pv);
    g += `<line x1="${PL}" y1="${py}" x2="${W-PR}" y2="${py}" stroke="var(--line)" stroke-width="1" opacity=".55"/>
      <text x="${PL-5}" y="${py+3.5}" font-size="10" fill="var(--muted)" text-anchor="end">${pv.toFixed(rg<5?2:1)}</text>`;
  }
  const CUP = '#ff7a5c', CDN = '#3ed08e';   /* 提亮版:紅漲綠跌 */
  const bw = Math.max(1.5, (W-PL-PR)/bars.length*0.6);
  bars.forEach((b,i)=>{
    const [d,o,h2,l,c] = b;
    const col = c>=o ? CUP : CDN;
    g += `<line x1="${x(i)}" y1="${y(h2)}" x2="${x(i)}" y2="${y(l)}" stroke="${col}" stroke-width="1.4"/>`;
    g += `<rect x="${x(i)-bw/2}" y="${y(Math.max(o,c))}" width="${bw}" height="${Math.max(1.2,Math.abs(y(o)-y(c)))}" fill="${col}"/>`;
  });
  if(bars.length>=2){
    const mp = (arr,colr)=>`<path d="${arr.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join('')}"
      fill="none" stroke="${colr}" stroke-width="1.7"/>`;
    g += mp(ma5,'#ffc94d') + mp(ma10,'#e39bf2') + mp(ma20,'#8fc1ff');
  }
  /* 右側價位標籤(專業看盤式):收集→防重疊→繪製 */
  const tags = [];
  const addTag = (v, color, label) => { if(v!==null && v!==undefined && isFinite(v)) tags.push({v:+v, color, label}); };
  /* 圖內名稱小標(帶深色底,不與K棒打架) */
  const nameLab = (v, color, txt) => `
    <rect x="${PL+3}" y="${y(v)-14}" width="${txt.length*10.5+10}" height="14" rx="3" fill="var(--page)" opacity=".78"/>
    <text x="${PL+8}" y="${y(v)-3}" font-size="10.5" font-weight="700" fill="${color}">${txt}</text>`;
  if(hasVA){
    const bh = Math.max(3, (H-2*P)*0.018);
    g += `<rect x="${PL}" y="${y(m2.va[0])-bh/2}" width="${W-PL-PR}" height="${bh}" fill="#3ed08e" opacity=".25"/>` + nameLab(m2.va[0], '#3ed08e', '支撐');
    g += `<rect x="${PL}" y="${y(m2.va[1])-bh/2}" width="${W-PL-PR}" height="${bh}" fill="#ff7a5c" opacity=".25"/>` + nameLab(m2.va[1], '#ff7a5c', '壓力');
    addTag(m2.va[0], '#3ed08e', '支');
    addTag(m2.va[1], '#ff7a5c', '壓');
    if(m2.poc20!==null && m2.poc20!==undefined && m2.poc20>lo && m2.poc20<hi){
      g += `<line x1="${PL}" y1="${y(m2.poc20)}" x2="${W-PR}" y2="${y(m2.poc20)}"
        stroke="var(--muted)" stroke-width="1" stroke-dasharray="2 4"/>`;
      addTag(m2.poc20, '#8a8778', 'POC');
    }
  }
  if(inList && row.zlo && row.zhi){
    g += `<rect x="${PL}" y="${y(row.zhi)}" width="${W-PL-PR}" height="${Math.abs(y(row.zlo)-y(row.zhi))}"
          fill="#8fc1ff" opacity=".16"/>` + nameLab(row.zhi, '#8fc1ff', '進場區間');
    addTag(row.zhi, '#8fc1ff', '進上');
    addTag(row.zlo, '#8fc1ff', '進下');
  }
  if(inList && row.stopv){
    g += `<line x1="${PL}" y1="${y(row.stopv)}" x2="${W-PR}" y2="${y(row.stopv)}"
          stroke="${CUP}" stroke-width="1.2" stroke-dasharray="5 4"/>` + nameLab(row.stopv, CUP, '失效');
    addTag(row.stopv, CUP, '停');
  }
  /* 標籤防重疊:由上而下,間距至少17px */
  tags.sort((a,b)=>y(a.v)-y(b.v));
  let lastY = -99;
  for(const tg of tags){
    let ty = Math.max(2, Math.min(H-18, y(tg.v)-8));
    if(ty < lastY + 17) ty = lastY + 17;
    lastY = ty;
    g += `<line x1="${W-PR}" y1="${y(tg.v)}" x2="${W-PR+4}" y2="${ty+8}" stroke="${tg.color}" stroke-width="1"/>
      <rect x="${W-PR+4}" y="${ty}" width="${PR-8}" height="16" rx="3" fill="${tg.color}"/>
      <text x="${W-PR+4+(PR-8)/2}" y="${ty+12}" font-size="10.5" font-weight="800" fill="#1b1a14"
        text-anchor="middle">${tg.label} ${tg.v}</text>`;
  }
  /* 區間最高/最低點標記 */
  const hAnch = iH > bars.length*0.75 ? 'end' : iH < bars.length*0.25 ? 'start' : 'middle';
  const lAnch = iL > bars.length*0.75 ? 'end' : iL < bars.length*0.25 ? 'start' : 'middle';
  g += `<text x="${x(iH)}" y="${Math.max(12, y(bars[iH][2])-8)}" font-size="11.5" font-weight="700"
        fill="${CUP}" text-anchor="${hAnch}">▲高 ${bars[iH][2]} (${dates[iH]})</text>`;
  g += `<text x="${x(iL)}" y="${Math.min(H-4, y(bars[iL][3])+16)}" font-size="11.5" font-weight="700"
        fill="${CDN}" text-anchor="${lAnch}">▼低 ${bars[iL][3]} (${dates[iL]})</text>`;
  /* X 軸日期刻度 */
  const stp = Math.max(1, Math.ceil(dates.length/6));
  for(let i=0;i<dates.length;i+=stp){
    g += `<text x="${x(i)}" y="${H-6}" font-size="10.5" fill="var(--muted)"
          text-anchor="${i===0?'start':'middle'}">${dates[i]}</text>`;
  }
  g += `<text x="${W-PR}" y="16" font-size="12" fill="var(--muted)" text-anchor="end">${TF_LAB[tf]} ${dates.length} 根 · ${lo.toFixed(1)}–${hi.toFixed(1)}</text>`;
  CURK = {dates, ohlc: bars, closes, P: PL, PR, W, n: dates.length, tf};
  return `<div class="kwrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}</svg>
   <div class="kx"></div><div class="ktip"></div></div>
  <div class="leg">${TF_LAB[tf]}(紅漲綠跌,滑鼠移動看開高低收) · <span style="color:#ffc94d">— MA5</span>
   <span style="color:#e39bf2">— MA10</span> <span style="color:#8fc1ff">— MA20</span>
   · ▲▼=區間高低點${hasVA?' · <span style="color:#3ed08e">▬支撐</span>/<span style="color:#ff7a5c">▬壓力</span>=20日價值區VA · ┈POC=成交密集價':''}${inList?' · <span style="color:#8fc1ff">▬藍帶=進場區間</span> · 紅虛線=失效價':''}</div>`;
}
let CURK = null, CURTK = null;
function hookK(){
  const w = document.querySelector('#panel .kwrap'); if(!w || !CURK) return;
  const svg = w.querySelector('svg'), tip = w.querySelector('.ktip'), kx = w.querySelector('.kx');
  function mv(ev){
    const r = svg.getBoundingClientRect(); if(!r.width) return;
    const vx = (ev.clientX - r.left) / r.width * CURK.W;
    const step = (CURK.W - CURK.P - (CURK.PR||CURK.P)) / Math.max(1, CURK.n - 1);
    let i = Math.round((vx - CURK.P) / step);
    i = Math.max(0, Math.min(CURK.n - 1, i));
    const px = (CURK.P + i*step) / CURK.W * r.width;
    kx.style.left = px + 'px'; kx.style.display = 'block';
    let htm;
    if(CURK.ohlc){
      const [d,o,h2,l,c,v] = CURK.ohlc[i];
      const prev = i>0 ? CURK.ohlc[i-1][4] : o;
      const chg = prev ? (c/prev-1)*100 : 0;
      htm = `<b>${d}</b> <span class="${c>=prev?'up':'dwn'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span><br>
        開 <b>${o}</b> 高 <b>${h2}</b> 低 <b>${l}</b> 收 <b>${c}</b><br>量 ${(+v).toLocaleString()} ${CURK.tf==='d'?'張':'千張'}`;
    } else {
      const prev = i>0 ? CURK.closes[i-1] : CURK.closes[i];
      const chg = prev ? (CURK.closes[i]/prev-1)*100 : 0;
      htm = `<b>${CURK.dates[i]||('第'+(i+1)+'根')}</b> <span class="${chg>=0?'up':'dwn'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span><br>收 <b>${CURK.closes[i]}</b>`;
    }
    tip.innerHTML = htm; tip.style.display = 'block';
    const tw = tip.offsetWidth;
    tip.style.left = (px + 14 + tw > r.width ? Math.max(0, px - tw - 12) : px + 12) + 'px';
  }
  w.addEventListener('mousemove', mv);
  w.addEventListener('mouseleave', ()=>{ tip.style.display='none'; kx.style.display='none'; });
}
function macdChart(tk){
  const s = ST[tk];
  const closes = (s.o||[]).map(x=>x[4]);
  if(!closes || closes.length<30) return '';
  const dif = ema(closes,12).map((v,i)=>v-ema(closes,26)[i]);
  const dea = ema(dif,9);
  const hist = dif.map((v,i)=>v-dea[i]);
  const W=1000, H=140, P=30;
  const mx = Math.max(...hist.map(Math.abs), ...dif.map(Math.abs), ...dea.map(Math.abs))||1;
  const x = i => P + i*(W-2*P)/(closes.length-1);
  const y = v => H/2 - v/mx*(H/2-16);
  let g = `<line x1="${P}" y1="${H/2}" x2="${W-P}" y2="${H/2}" stroke="var(--line)"/>`;
  const bw = Math.max(1.5,(W-2*P)/closes.length*0.55);
  hist.forEach((v,i)=>{
    g += `<rect x="${x(i)-bw/2}" y="${Math.min(y(v),H/2)}" width="${bw}" height="${Math.abs(y(v)-H/2)||.5}"
          fill="${v>=0?'#ff7a5c':'#3ed08e'}" opacity=".9"/>`;   /* 紅正綠負 */
  });
  g += `<path d="${dif.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join('')}"
        fill="none" stroke="var(--ink)" stroke-width="1.6"/>`;
  g += `<path d="${dea.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join('')}"
        fill="none" stroke="#ffc94d" stroke-width="1.6"/>`;
  CURM = {dif, dea, hist, P, W, n: closes.length};
  return `<div class="kwrap mwrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}</svg>
   <div class="kx"></div><div class="ktip"></div></div>
   <div class="leg">MACD(12,26,9) · 柱=DIF−DEA(紅正綠負,滑鼠移動看數值) · <span style="color:var(--ink)">— DIF</span>
    <span style="color:#ffc94d">— DEA</span></div>`;
}
let CURM = null;
function hookM(){
  const w = document.querySelector('#panel .mwrap'); if(!w || !CURM) return;
  const svg = w.querySelector('svg'), tip = w.querySelector('.ktip'), kx = w.querySelector('.kx');
  function mv(ev){
    const r = svg.getBoundingClientRect(); if(!r.width) return;
    const vx = (ev.clientX - r.left) / r.width * CURM.W;
    const step = (CURM.W - 2*CURM.P) / Math.max(1, CURM.n - 1);
    let i = Math.round((vx - CURM.P) / step);
    i = Math.max(0, Math.min(CURM.n - 1, i));
    const px = (CURM.P + i*step) / CURM.W * r.width;
    kx.style.left = px + 'px'; kx.style.display = 'block';
    const d = (CURK && CURK.dates && CURK.dates[i]) ? CURK.dates[i] : ('第'+(i+1)+'根');
    const hv = CURM.hist[i];
    tip.innerHTML = `<b>${d}</b><br>DIF <b>${CURM.dif[i].toFixed(3)}</b> · DEA <b>${CURM.dea[i].toFixed(3)}</b><br>
      柱 <span class="${hv>=0?'up':'dwn'}"><b>${hv>=0?'+':''}${hv.toFixed(3)}</b></span>`;
    tip.style.display = 'block';
    const tw = tip.offsetWidth;
    tip.style.left = (px + 14 + tw > r.width ? Math.max(0, px - tw - 12) : px + 12) + 'px';
  }
  w.addEventListener('mousemove', mv);
  w.addEventListener('mouseleave', ()=>{ tip.style.display='none'; kx.style.display='none'; });
}
function flowChart(tk){
  const s = ST[tk], a = s.a||{};
  if(!a.ff || !a.ff.length) return '';
  const dates = (s.o||[]).slice(-a.ff.length).map(b=>b[0]);
  const closes = (s.o||[]).slice(-a.ff.length).map(b=>b[4]);
  const series = [['外資', a.ff, '#8fc1ff'], ['投信', a.tt||[], '#ffc94d'], ['自營', a.dd||[], '#e39bf2']];
  const W=1000, H=170, P=30;
  const mx = Math.max(1, ...series.flatMap(x=>x[1].map(Math.abs)));
  const n = a.ff.length, gw = (W-2*P)/n, bw = Math.max(2, gw/4.4);
  const y = v => H/2 - v/mx*(H/2-22);
  let g = `<line x1="${P}" y1="${H/2}" x2="${W-P}" y2="${H/2}" stroke="var(--line)"/>`;
  series.forEach(([nm,arr,col],si)=>{
    arr.forEach((v,i)=>{
      const bx = P + i*gw + gw/2 + (si-1)*bw - bw/2;
      g += `<rect x="${bx.toFixed(1)}" y="${Math.min(y(v),H/2).toFixed(1)}" width="${bw.toFixed(1)}"
        height="${(Math.abs(y(v)-H/2)||.5).toFixed(1)}" fill="${col}" opacity=".95"/>`;
    });
  });
  const stp = Math.max(1, Math.ceil(n/6));
  for(let i=0;i<n;i+=stp){
    g += `<text x="${(P+i*gw+gw/2).toFixed(1)}" y="${H-4}" font-size="10" fill="var(--muted)" text-anchor="middle">${dates[i]||''}</text>`;
  }
  g += `<text x="${W-P}" y="14" font-size="11" fill="var(--muted)" text-anchor="end">±${mx.toLocaleString()} 張</text>`;
  CURF = {dates, closes, ff: a.ff, tt: a.tt||[], dd: a.dd||[], P, W, n, gw};
  return `<div class="chart"><div class="kwrap fwrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}</svg>
    <div class="kx"></div><div class="ktip"></div></div>
    <div class="leg">法人逐日買賣超(近${n}日,張;滑鼠移動看逐日數值+收盤) ·
    <span style="color:#8fc1ff">■ 外資</span> <span style="color:#ffc94d">■ 投信</span>
    <span style="color:#e39bf2">■ 自營</span></div></div>`;
}
function chipTable(tk){
  const a = (ST[tk]||{}).a||{};
  if(a.f5===undefined && a.t5===undefined && a.d5===undefined) return '';
  const sumN = (arr,n) => arr&&arr.length ? arr.slice(-n).reduce((x,y)=>x+y,0) : null;
  const last = arr => arr&&arr.length ? arr[arr.length-1] : null;
  const cell = v => (v===undefined||v===null) ? '<td>—</td>'
    : `<td><span class="rp ${v>=0?'pos':'neg'}">${v>=0?'+':''}${(+v).toLocaleString()}</span></td>`;
  const streak = arr => {
    if(!arr || !arr.length) return '';
    const lastV = arr[arr.length-1];
    if(lastV===0) return '<span class="chip">今無</span>';
    const sgn = lastV>0 ? 1 : -1;
    let n = 0;
    for(let i=arr.length-1; i>=0; i--){
      if(arr[i]*sgn > 0) n++; else break;
    }
    const prev = arr.length-1-n >= 0 ? arr[arr.length-1-n] : 0;
    const buy = sgn>0;
    /* 紅買綠賣,與表內正負數同色系 */
    if(n===1 && prev*sgn < 0)
      return `<span class="chip ${buy?'ch-dn':'ch-go'}" style="font-weight:700">${buy?'賣轉買':'買轉賣'}</span>`;
    return `<span class="chip ${buy?'ch-dn':'ch-go'}">連${n}${buy?'買':'賣'}</span>`;
  };
  const rows = [
    ['最新日', last(a.ff), last(a.tt), last(a.dd)],
    ['5日',   a.f5, a.t5, a.d5],
    ['10日',  sumN(a.ff,10), sumN(a.tt,10), sumN(a.dd,10)],
    ['20日',  a.f20, a.t20, a.d20],
  ].map(([lab,f,t,d])=>{
    const tot = (f===null&&t===null&&d===null) ? null : (f||0)+(t||0)+(d||0);
    return `<tr><td style="color:var(--ink2)">${lab}</td>${cell(f)}${cell(t)}${cell(d)}${cell(tot)}</tr>`;
  }).join('');
  const nz2 = v => (v===undefined||v===null) ? '—' : ((v>=0?'+':'')+(+v).toLocaleString());
  return `<div class="chart"><div class="tblwrap"><table class="ltbl"><thead><tr>
    <th>籌碼(張)</th><th>外資 ${streak(a.ff)}</th><th>投信 ${streak(a.tt)}</th>
    <th>自營 ${streak(a.dd)}</th><th>三大合計</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="leg">融資5日增減 <b>${nz2(a.mg5)}</b> 張 · 融券5日增減 <b>${nz2(a.sb5)}</b> 張
    (10日=近20日資料內加總;主力成本/法人佔量見「主力(法人10日推估)」)</div></div>`;
}
function tdccTable(tk){
  const t8 = (ST[tk]||{}).t8||[];
  if(!t8.length) return '';
  const lt = t8[t8.length-1], p4 = t8[Math.max(0, t8.length-5)];
  const dW = lt[1]-p4[1], dH = lt[3]-p4[3];
  let wn=0, wdir=0;
  for(let i=t8.length-1;i>0;i--){
    const d2 = t8[i][1]-t8[i-1][1];
    if(d2===0) break;
    const sg = d2>0?1:-1;
    if(wdir===0) wdir=sg;
    if(sg===wdir) wn++; else break;
  }
  const tag = wn ? `<span class="chip ${wdir>0?'ch-dn':'ch-go'}" style="font-weight:700">大戶連${wn}週${wdir>0?'增':'減'}</span>` : '';
  const rows = t8.slice(-5).reverse().map((r,ri,arr2)=>{
    const prev = ri < arr2.length-1 ? arr2[ri+1] : null;
    const dw = prev ? r[1]-prev[1] : null;
    const dh = prev ? r[3]-prev[3] : null;
    return `<tr><td style="color:var(--ink2)">${r[0].slice(0,2)}/${r[0].slice(2)}</td>
      <td><b>${r[1]}%</b></td>
      <td>${dw===null?'—':`<span class="rp ${dw>=0?'pos':'neg'}">${dw>=0?'+':''}${dw.toFixed(2)}</span>`}</td>
      <td>${r[2]}%</td>
      <td>${r[3].toLocaleString()}</td>
      <td>${dh===null?'—':`<span class="rp ${dh>=0?'pos':'neg'}">${dh>=0?'+':''}${dh.toLocaleString()}</span>`}</td></tr>`;
  }).join('');
  return `<div class="chart"><div class="tblwrap"><table class="ltbl"><thead><tr>
    <th>大戶/散戶(TDCC週頻) ${tag}</th><th>大戶%</th><th>週變化(pp)</th><th>散戶%</th><th>股東人數</th><th>週變化</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="leg">4週:大戶 ${dW>=0?'+':''}${dW.toFixed(2)}pp · 股東 ${dH>=0?'+':''}${dH.toLocaleString()} 人
    · 大戶增+股東減=籌碼集中 · 集保每週公布,滯後數天</div></div>`;
}
let CURF = null;
function hookF(){
  const w = document.querySelector('#panel .fwrap'); if(!w || !CURF) return;
  const svg = w.querySelector('svg'), tip = w.querySelector('.ktip'), kx = w.querySelector('.kx');
  const fmtN = v => (v===undefined||v===null) ? '—' : (v>=0?'+':'')+(+v).toLocaleString();
  function mv(ev){
    const r = svg.getBoundingClientRect(); if(!r.width) return;
    const vx = (ev.clientX - r.left) / r.width * CURF.W;
    let i = Math.floor((vx - CURF.P) / CURF.gw);
    i = Math.max(0, Math.min(CURF.n - 1, i));
    const px = (CURF.P + i*CURF.gw + CURF.gw/2) / CURF.W * r.width;
    kx.style.left = px + 'px'; kx.style.display = 'block';
    tip.innerHTML = `<b>${CURF.dates[i]||''}</b>${CURF.closes[i]!==undefined?` · 收 <b>${CURF.closes[i]}</b>`:''}<br>
      <span style="color:#8fc1ff">外資 <b>${fmtN(CURF.ff[i])}</b></span> ·
      <span style="color:#ffc94d">投信 <b>${fmtN(CURF.tt[i])}</b></span> ·
      <span style="color:#e39bf2">自營 <b>${fmtN(CURF.dd[i])}</b></span> 張`;
    tip.style.display = 'block';
    const tw = tip.offsetWidth;
    tip.style.left = (px + 14 + tw > r.width ? Math.max(0, px - tw - 12) : px + 12) + 'px';
  }
  w.addEventListener('mousemove', mv);
  w.addEventListener('mouseleave', ()=>{ tip.style.display='none'; kx.style.display='none'; });
}
function abox(title, rows){
  return `<div class="abox"><h3>${title}</h3>` +
    rows.map(([k,v,c])=>`<div class="arow"><span class="k">${k}</span><span class="v ${c||''}">${v}</span></div>`).join('') +
    '</div>';
}
function showD(tk){
  const s = ST[tk]; if(!s) return;
  const a = s.a||{}, row = ROWS.find(r=>r.tk===tk);
  const volR = (a.Volume && a.VOL_MA_20) ? a.Volume/a.VOL_MA_20 : null;
  const tech = abox('📈 技術面', [
    ['RSI(14)', fmt(a.RSI_14,false,1), a.RSI_14>75?'warn':''],
    ['vs MA20', fmt(a.price_vs_ma20,true), cl(a.price_vs_ma20)],
    ['vs MA60', fmt(a.price_vs_ma60,true), cl(a.price_vs_ma60)],
    ['MACD柱', fmt(a.MACDh_12_26_9,false,3), cl(a.MACDh_12_26_9)],
    ['量比(當日/20日)', volR?volR.toFixed(2)+'x':'—', volR>2.5?'warn':''],
  ]);
  const nz = v => (v===undefined||v===null) ? '—' : (+v).toLocaleString();
  const fund = abox('🏭 基本面', [
    ['營收年增率(最新月)', fmt(a.revenue_yoy_latest,true), cl(a.revenue_yoy_latest)],
    ['營收年增率(3月均)', fmt(a.revenue_yoy_3m_avg,true), cl(a.revenue_yoy_3m_avg)],
    ['EPS(TTM)', fmt(a.eps_ttm), cl(a.eps_ttm)],
    ['毛利率', fmt(a.gross_margin_latest,true), ''],
    ['營益率', fmt(a.operating_margin_latest,true), cl(a.operating_margin_latest)],
  ]);
  const val = abox('⚖️ 評價/風險', [
    ['PE', fmt(a.pe_ratio,false,1), a.pe_ratio>50?'warn':''],
    ['PB', fmt(a.pb_ratio,false,2), ''],
    ['ATR%', fmt(a.atr_pct,true), ''],
    ['波動率20日', fmt(a.volatility_20d,true), ''],
    ['dataA 20D超額預測', s.p!==null&&s.p!==undefined?fmt(s.p,true):'—', cl(s.p)],
  ]);
  const m = s.m || {};
  const model = abox('🤖 模型面(Champion production v1/v2,與 dataA 為不同模型)', [
    ['v1 訊號', m.sig||'—', m.sig==='UP'?'pos':m.sig==='DOWN'?'neg':''],
    ['↑機率 / ↓機率', (m.pu!==null&&m.pu!==undefined)?`${(m.pu*100).toFixed(0)}% / ${(m.pd*100).toFixed(0)}%`:'—',
      (m.pu!==null&&m.pu!==undefined&&m.pu>m.pd)?'pos':'neg'],
    ['v2 20D超額預測', (m.pr!==null&&m.pr!==undefined)?fmt(m.pr,true):'—', cl(m.pr)],
    ['推薦', m.rec||'—', m.rec?(m.rec.includes('買')?'pos':m.rec.includes('賣')?'neg':'warn'):''],
    ['推薦類別歷史統計', (m.wr!==null&&m.wr!==undefined)?`${m.wr}% / ${m.ar}%（非個股勝率）`:'—', ''],
  ]);
  const chipk = abox('🧷 法人推估（非分點主力成本）', [
    ['法人10日成本推估', (m.ic!==null&&m.ic!==undefined)?m.ic:'—', ''],
    ['法人10日淨買(張)', (m.inet!==null&&m.inet!==undefined)?m.inet.toLocaleString():'—', cl(m.inet)],
    ['法人佔量(10日)', (m.iv!==null&&m.iv!==undefined)?m.iv+'%':'—', ''],
    ['POC 20日/60日', (m.poc20!==null&&m.poc20!==undefined)?`${m.poc20} / ${m.poc60}`:'—', ''],
    ['價值區VA(20日)', (m.va&&m.va[0]!==null)?`${m.va[0]}–${m.va[1]}`:'—', ''],
  ]);
  const swRows = [];
  if(m.sw!==null&&m.sw!==undefined){
    swRows.push(['短波評分', m.sw, m.sw>=0.75?'pos':'']);
    swRows.push(['短波動作', m.swa||'—', (m.swa||'').includes('EXIT')?'neg':'']);
    if(m.swz&&m.swz[0]!==null) swRows.push(['短波區間', `${m.swz[0]}–${m.swz[1]}`, '']);
    if(m.swz&&m.swz[2]) swRows.push(['區間狀態', m.swz[2], '']);
    if(m.mo) swRows.push(['動能策略', `${m.mo}(${m.mos!==null?m.mos:'—'})`, '']);
  }
  const sw = swRows.length?abox('🌊 短波層(rere-encoded)', swRows):'';
  const noChipk = t => !/chipk/i.test(t);   /* 籌碼K App 已停用,不再顯示其 tag */
  const tagChips = []
    .concat(m.rt?m.rt.split(/[;,]\\s*/).filter(Boolean).map(t=>`<span class="chip ch-wt">⚠ ${t}</span>`):[])
    .concat(m.swr?m.swr.split(/;\\s*/).filter(Boolean).filter(noChipk).map(t=>`<span class="chip ch-go">${t}</span>`):[])
    .concat(m.swf?m.swf.split(/;\\s*/).filter(Boolean).filter(noChipk).map(t=>`<span class="chip">${t}</span>`):[])
    .join('');
  const lastC = (s.o && s.o.length) ? s.o[s.o.length-1][4] : null;
  /* 2026-09-23 審查 P1-2:逐檔資料日期 + 過期紅幅。全頁基準=snapshot asof;
     停牌/斷更股(如 5371 止於 08-21)的行情/特徵/模型判讀均為舊資料,必須明示 */
  const pageAsof = (((D.meta||{}).sources)||[]).filter(x=>x.key==='snapshot').map(x=>x.asof)[0]||'';
  const pxD = s.last_close_date||'', ftD = s.a_date||'';
  const staleBits = [];
  if(pageAsof && pxD && pxD < pageAsof) staleBits.push(`價格止於 ${pxD}`);
  if(pageAsof && ftD && ftD < pageAsof) staleBits.push(`特徵止於 ${ftD}`);
  const staleBanner = staleBits.length
    ? `<div class="guard" style="border-color:var(--dn);background:var(--dn-wash);color:var(--dn)">🔴 資料過期:${staleBits.join('、')}(全頁基準 ${pageAsof})。此股不在今日候選名單;下方行情、四大面向與模型判讀均以舊資料計算,僅供歷史參考,不可當最新判讀。</div>`
    : '';
  const dateLine = `<span class="crumb">資料日期:價格 ${pxD||'—'} · 特徵/四面向 ${ftD||'—'} · 模型檔 ${((D.mkt||{}).asof)||'—'}</span>`;
  CURTK = tk;
  document.getElementById('panel').innerHTML = `
   <button class="back" data-close="1">← 返回名單</button>
   <span class="crumb">個股詳情 · 四大面向為每日 snapshot 數據 · 非投資建議</span>
   ${staleBanner}
   <div class="dhead">
     <div><h1 style="font-size:32px;margin:0">${tk} ${s.n||''}</h1>
       <div class="company">${s.s||''}${row?` · <span style="color:var(--wt)">${esc((row.card||{}).label||'觀察中')}</span>`:''}</div>
       ${dateLine}</div>
     <div class="dstats">${lastC?`收盤 <b>${lastC}</b>${(pageAsof&&pxD&&pxD<pageAsof)?` <span style="color:var(--dn)">(${pxD} 舊價)</span>`:''}<br>`:''}
       ${row?`觀察區間 <b>${row.zone}</b> · 失效參考 <b style="color:var(--dn)">${row.stop}</b><br>`:''}
       ${(m.poc20!==null&&m.poc20!==undefined)?`POC20 <b>${m.poc20}</b> · VA <b>${m.va[0]}–${m.va[1]}</b><br>`:''}
       dataA 20D超額 <b>${s.p!==null&&s.p!==undefined?(s.p*100).toFixed(1)+'%':'—'}</b>
       ${m.rec?` · Champion <b class="${m.rec.includes('買')?'up':m.rec.includes('賣')?'dwn':''}">${m.rec}</b>`:''}</div></div>
   ${tagChips?`<div class="tags">${tagChips}</div>`:''}
   <div class="chart"><div class="tfbar">
     <button class="tfbtn active" data-tf="d">日K</button>
     <button class="tfbtn" data-tf="m">月K</button>
     <button class="tfbtn" data-tf="y">年K</button>
     <span class="maline" id="maline">${maText(tk,'d')}</span></div>
    <div id="kchart">${candle(tk,'d')}</div></div>
   <div class="chart">${macdChart(tk)}</div>
   ${flowChart(tk)}
   ${chipTable(tk)}
   ${tdccTable(tk)}
   <div class="acols">${tech}${fund}${val}${model}${chipk}${sw}</div>`;
  document.getElementById('panel').style.display='block';
  document.getElementById('overlay').style.display='block';
  document.getElementById('panel').scrollTop=0;
  hookK(); hookM(); hookF();
}
function hideD(){
  document.getElementById('panel').style.display='none';
  document.getElementById('overlay').style.display='none';
}
// ---- 進場帳本(每日名單損益,日期折疊) ----
(function(){
  const L = D.ledger||[];
  const el = document.getElementById('ledgerSec');
  if(!L.length){ el.innerHTML=''; return; }
  const byDate = {};
  L.forEach(r=>{ (byDate[r.trade_date]=byDate[r.trade_date]||[]).push(r); });
  const dates = Object.keys(byDate).sort().reverse();
  const closedAll = L.filter(r=>['stopped','expired'].includes(r.status));
  const holdAll = L.filter(r=>r.status==='holding');
  const LG = D.ledger_legacy||null;
  const fmtPct = v => (v===null||v===undefined||v==='') ? '—' : ((+v>=0?'+':'')+(+v).toFixed(2)+'%');
  let h = `<div class="sec">▼ 模擬進場紀錄
    <span class="hint">已結束 ${closedAll.length} 筆 · 持有中 ${holdAll.length} 筆</span></div>
    <div class="meta">這裡呈現逐筆模擬結果，沒有實際成交確認。主策略與 rere 的觀察期不同；提前停損先結束，不能只拿已平倉勝率判斷策略好壞。策略評估須使用完整觀察期的同批樣本，並分開比較。</div>`;
  if(LG && LG.since){
    h += `<div class="meta">主 lane 排序自 ${LG.since} 起改為型態乾淨度（模型分數只顯示，不過濾、不排序）。舊規則（模型不反對→模型分數排序）以影子帳本並行：已結束 ${LG.closed} 筆 均 ${fmtPct(LG.mean)} 勝率 ${LG.win===null?'—':LG.win+'%'} · 持有中 ${LG.holding}；同期新排序主 lane 已結束 ${LG.new_closed} 筆 均 ${fmtPct(LG.new_mean)} 勝率 ${LG.new_win===null?'—':LG.new_win+'%'}。累積 ≥60 日後再比較，不據此提前下判決。</div>`;
  }
  const stMap = {holding:['持有中','st-h'], stopped:['🔴 停損','st-s'], expired:['⏱ 到期','st-e'],
                 untriggered:['— 未觸發','st-u'], pending:['… 待判定','st-p']};
  dates.forEach((dt,di)=>{
    const rows = byDate[dt];
    const closed = rows.filter(r=>['stopped','expired'].includes(r.status));
    const trig = rows.filter(r=>['holding','stopped','expired'].includes(r.status));
    const dAvgArr = rows.filter(r=>r.ret_pct!=='' && ['holding','stopped','expired'].includes(r.status));
    const dAvg = dAvgArr.length? (dAvgArr.reduce((a,r)=>a+(+r.ret_pct||0),0)/dAvgArr.length) : null;
    h += `<details class="dled"${di===0?' open':''}><summary>
      <b>${dt}</b><span class="lsum mut">${rows.length} 檔 · 觸發 ${trig.length}</span>
      ${dAvg!==null?`<span class="lsum ${dAvg>=0?'pos':'neg'}">逐筆均值 ${dAvg>=0?'+':''}${dAvg.toFixed(2)}%（含持有中）</span>`:''}
      ${closed.length?`<span class="lsum mut">已平倉 ${closed.length}</span>`:''}</summary>
      <div class="tblwrap"><table class="ltbl"><thead><tr>
        <th>股票</th><th>策略</th><th>狀態</th><th>模擬進場</th><th>出場／最新</th><th>持有</th><th>逐筆損益</th></tr></thead><tbody>`;
    rows.forEach(r=>{
      const [stTxt, stCls] = stMap[r.status]||[r.status,''];
      const rp = r.ret_pct!=='' ? +r.ret_pct : null;
      const si = ST[r.ticker]||{};
      h += `<tr><td><span class="sym" data-tk="${r.ticker}" style="cursor:pointer;font-weight:700">${r.ticker} ${si.n||r.name||''}</span>${si.s?`<span class="mkt">${si.s}</span>`:''}</td>
        <td>${r.lane==='rere'?'<span style="color:var(--dn)">rere</span>':'主'}</td>
        <td class="${stCls}">${stTxt}</td>
        <td class="mono">${r.entry_price!==''?r.entry_price+'<div style="color:var(--muted);font-size:11px">'+(r.entry_date||'')+'</div>':'—'}</td>
        <td class="mono">${r.exit_price!==''?r.exit_price+'<div style="color:var(--muted);font-size:11px">'+(r.exit_reason||'')+'</div>':(r.last_close!==''?r.last_close+'<div style="color:var(--muted);font-size:11px">最新</div>':'—')}</td>
        <td>${r.days_held!==''?Math.round(r.days_held)+'d':'—'}</td>
        <td>${rp!==null?`<span class="rp ${rp>=0?'pos':'neg'}">${rp>=0?'+':''}${rp}%</span>`:'—'}</td></tr>`;
    });
    h += '</tbody></table></div></details>';
  });
  el.innerHTML = h;
})();
// ---- 我的持股 ----
(function(){
  const H = D.holdings||[]; const el = document.getElementById('tab-mine');
  if(!H.length){ el.innerHTML = '<div class="empty">無持股資料(my_holdings.db)</div>'; return; }
  let tot=0, totCost=0;
  const rows = H.map(h=>{
    const pl = (h.last&&h.ac)? (h.last-h.ac)*h.sh : null;
    const plp = (h.last&&h.ac)? (h.last/h.ac-1)*100 : null;
    if(h.last){ tot += h.last*h.sh; totCost += h.ac*h.sh; }
    const s = ST[h.tk]||{};
    // 規劃A: 移動停利鏡像 — 取「HWM-8% 與 硬停損(兩平-10%)」較高者為當前防線
    const line = (h.trail&&h.hard)? Math.max(h.trail,h.hard) : (h.trail||h.hard);
    const near = (line&&h.last)? (h.last/line-1)*100 : null;
    const lineCls = near!==null ? (near<=0?'neg':(near<3?'':'pos')) : '';
    const lineHtml = line? `<b class="${near!==null&&near<=0?'rp neg':''}">${line.toFixed(2)}</b>`+
      `<div style="color:var(--muted);font-size:10.5px">HWM ${h.hwm??'—'}→${h.trail??'—'} / 硬${h.hard??'—'}${near!==null?` · 距${near>=0?'+':''}${near.toFixed(1)}%`:''}${near!==null&&near<=0?' ⚠️已破':''}</div>` : '—';
    return `<tr><td><span data-tk="${h.tk}" style="cursor:pointer;font-weight:700">${h.tk} ${s.n||''}</span>${s.s?`<span class="mkt">${s.s}</span>`:''}
      <div style="color:var(--muted);font-size:11px">${h.ed||''} 進場</div></td>
      <td>${(+h.sh).toLocaleString()}</td><td>${h.ac}</td><td>${h.last??'—'}</td>
      <td>${plp!==null?`<span class="rp ${plp>=0?'pos':'neg'}">${plp>=0?'+':''}${plp.toFixed(2)}%</span>`:'—'}</td>
      <td>${pl!==null?Math.round(pl).toLocaleString():'—'}</td>
      <td>${lineHtml}</td>
      <td style="font-size:12.5px;color:var(--ink2);max-width:460px">${h.note||''}</td></tr>`;
  }).join('');
  const tp = totCost? (tot/totCost-1)*100 : null;
  el.innerHTML = `<div class="sec">▼ 我的持股(本地資料,點代號看四大面向)
    <span class="hint">市值 ${Math.round(tot).toLocaleString()} 元${tp!==null?` · 未實現 ${(tp>=0?'+':'')+tp.toFixed(2)}%`:''}</span></div>
    <div class="tblwrap"><table class="ltbl"><thead><tr>
    <th>股票</th><th>股數</th><th>成本</th><th>現價</th><th>報酬</th><th>損益(元)</th><th>移動停利線</th><th>行動線/備註</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
})();
// ---- 新聞 ----
document.getElementById('tab-news').innerHTML =
  D.news_html ? `<div class="sec">▼ 產業新聞日報(白名單來源,每日 pipeline 更新)</div>${D.news_html}`
              : '<div class="empty">無新聞資料</div>';
// ---- 當沖研究 ----
(function(){
  const DT = D.daytrade||{}; const el = document.getElementById('tab-daytrade');
  const rs = DT.rows||[];
  const warn = `<div style="background:var(--dn-wash);border:1px solid var(--dn);border-radius:10px;padding:12px 14px;margin:6px 0 14px;font-size:14px;line-height:1.7">
    ⚠️ <b>這是「振幅篩選器」,不是選股工具</b>。<b>回測驗證(2026年,1957樣本):此池對隔日漲跌的預測力=0</b>
    (超額報酬 -0.01pp、扣成本後 -0.45%/次,方向性與隨機無異)。它唯一有效且<b>穩定</b>的是:選出日內振幅中位 5.9%
    的股票(全市場僅 2.9%),即「今天有得沖」的標的——此優勢在 131 個交易日中 99% 成立、池內 75% 標的隔日振幅≥4%、
    空池率 0%。<b>決勝完全在你的日內執行(進場/停損/盤感),不在這張表</b>——
    這正是 rere 說「當沖靠盤感、機械化無 edge」的數據證明。振幅大是雙面刃(隔日跌逾3%佔23.8%)。
    每日 pipeline 自動更新;已排除處置股與低流動性;rere 本人強烈勸退新手。</div>`;
  if(!rs.length){ el.innerHTML = warn + '<div class="empty">今日無符合條件標的</div>'; return; }
  const body = rs.map(r=>{
    const zone = (r.sector||'').includes('半導') || (r.sector||'').includes('電子') ? 'ch-dn':'';
    return `<tr>
      <td><span data-tk="${r.ticker}" style="cursor:pointer;font-weight:700">${r.ticker} ${r.name||''}</span>
        <span class="mkt">${r.sector||''}</span></td>
      <td style="text-align:right;font-weight:700">${r.close}</td>
      <td style="text-align:right">${(r.avg5_lots||0).toLocaleString()}</td>
      <td style="text-align:right"><span class="rp ${r.vr>=1.8?'neg':''}">${r.vr}x</span></td>
      <td style="text-align:right"><span class="rp neg">-${r.wash_pct}%</span></td>
      <td style="text-align:right">${r.atr_pct}%</td></tr>`;
  }).join('');
  el.innerHTML = warn +
    `<div class="strath">⚡ 盤前當沖觀察池<span class="sub">asof ${DT.asof||'-'} · 均量≥3000張 且 量比≥1.2 且 跌深≥8% 且 ATR≥2.5% · 已排除處置股 · 依綜合分排序</span></div>
     <div class="tblwrap"><table class="ltbl"><thead><tr>
       <th>股票</th><th>收盤</th><th>5日均量(張)</th><th>量比</th><th>跌深</th><th>ATR%</th></tr></thead>
       <tbody>${body}</tbody></table></div>
     <div class="leg">量比=今日量/20日均量(越大越有人氣) · 跌深=近10日高點回落(反彈空間) · ATR%=日內振幅(太小沒得沖) · 點股票代號看四大面向</div>`;
})();
// ---- Agent 投資賽 ----
(function(){
  const A = D.arena||{}; const el = document.getElementById('tab-arena');
  if(!A.st || !A.st.length){ el.innerHTML = '<div class="empty">無投資賽資料</div>'; return; }
  const by={}, pd={}, evb={};
  (A.hold||[]).forEach(x=>{(by[x.a]=by[x.a]||[]).push(x)});
  (A.pend||[]).forEach(o=>{(pd[o.a]=pd[o.a]||[]).push(o)});
  (A.ev||[]).forEach(o=>{(evb[o.a]=evb[o.a]||[]).push(o)});
  let h = `<div class="sec">▼ Agent 投資賽(${A.date} · 出賽 ${A.adm}/${A.n} · 起始 ${(+A.cap0).toLocaleString()} → 目標 ${(+A.capT).toLocaleString()})
    <span class="hint">點 Agent 展開:持倉+掛單理由+近期成交</span></div>`;
  A.st.forEach((s,i)=>{
    const hs = by[s.id]||[], ps = pd[s.id]||[], es = (evb[s.id]||[]).slice(0,10);
    h += `<details class="dled"${i===0?' open':''}><summary>
      <b>#${s.rank} ${s.nm}</b>
      <span class="lsum mut">${s.sty||''}</span>
      <span class="lsum mut">權益 ${(+s.eq).toLocaleString()}</span>
      <span class="lsum ${s.tr>=0?'pos':'neg'}">總 ${s.tr>=0?'+':''}${s.tr}%</span>
      <span class="lsum ${s.dr>=0?'pos':'neg'}">今日 ${s.dr>=0?'+':''}${s.dr}%</span>
      <span class="lsum mut">持倉 ${s.pos} · 掛單 ${ps.length}</span></summary>
      <div class="newsbody">
      <div class="meta" style="margin:8px 0 2px">回測參考 ${s.btr!==null&&s.btr!==undefined?s.btr+'% / MDD '+s.bmdd+'%':'—'} · agent_id ${s.id}</div>`;
    if(hs.length){
      h += `<h4>持倉 ${hs.length} 檔(點股票看四大面向)</h4>
        <div class="tblwrap"><table class="ltbl"><thead><tr>
        <th>股票</th><th>進場日</th><th>進場價</th><th>HWM</th><th>股數</th><th>投入金額</th><th>出場策略</th></tr></thead><tbody>` +
        hs.map(x=>`<tr><td><span data-tk="${x.tk}" style="cursor:pointer;font-weight:700">${x.tk} ${x.nm||(ST[x.tk]||{}).n||''}</span>${(ST[x.tk]||{}).s?`<span class="mkt">${ST[x.tk].s}</span>`:''}</td>
          <td>${x.ed||''}</td><td>${x.ep??'—'}</td><td>${x.hw??'—'}</td><td>${x.sh!==null?(+x.sh).toLocaleString():'—'}</td>
          <td>${x.no?(+x.no).toLocaleString():'—'}</td><td style="font-size:12px">${x.xp||''}</td></tr>`).join('') +
        '</tbody></table></div>';
    } else { h += '<h4>持倉</h4><p>目前空手</p>'; }
    if(ps.length){
      h += `<h4>掛單中 ${ps.length} 筆</h4>
        <div class="tblwrap"><table class="ltbl"><thead><tr>
        <th>方向</th><th>股票</th><th>訊號日</th><th>訊號價</th><th>目標金額</th><th>掛單理由</th></tr></thead><tbody>` +
        ps.map(o=>`<tr><td class="${o.side==='BUY'?'st-h':'st-s'}">${o.side}</td>
          <td><span data-tk="${o.tk}" style="cursor:pointer;font-weight:700">${o.tk}</span></td>
          <td>${o.sd||''}</td><td>${o.sc??'—'}</td><td>${o.no?(+o.no).toLocaleString():''}</td>
          <td style="font-size:12px;color:var(--ink2)">${o.rsn||''}</td></tr>`).join('') + '</tbody></table></div>';
    }
    if(es.length){
      h += `<h4>近期成交/事件(最新 ${es.length} 筆)</h4>
        <div class="tblwrap"><table class="ltbl"><thead><tr>
        <th>方向</th><th>股票</th><th>狀態</th><th>訊號日</th><th>成交日</th><th>成交價</th><th>理由</th></tr></thead><tbody>` +
        es.map(o=>`<tr><td class="${o.side==='BUY'?'st-h':'st-s'}">${o.side}</td>
          <td><span data-tk="${o.tk}" style="cursor:pointer;font-weight:700">${o.tk}</span></td>
          <td>${o.st}</td><td>${o.sd||''}</td><td>${o.fd||''}</td><td>${o.fp??'—'}</td>
          <td style="font-size:12px;color:var(--ink2)">${o.rsn||''}</td></tr>`).join('') + '</tbody></table></div>';
    }
    h += '</div></details>';
  });
  el.innerHTML = h;
})();
// CSP-safe 事件委派(Artifact 會擋 inline onclick)
document.addEventListener('click', e=>{
  const tf = e.target.closest('[data-tf]');
  if(tf && CURTK){
    document.querySelectorAll('.tfbtn').forEach(b=>b.classList.toggle('active', b===tf));
    document.getElementById('kchart').innerHTML = candle(CURTK, tf.dataset.tf);
    const ml = document.getElementById('maline');
    if(ml) ml.innerHTML = maText(CURTK, tf.dataset.tf);
    hookK();
    return;
  }
  const tb = e.target.closest('[data-tab]');
  if(tb){
    document.querySelectorAll('.tabbtn').forEach(b=>b.classList.toggle('active', b===tb));
    ['list','mine','news','arena','daytrade'].forEach(k=>{
      document.getElementById('tab-'+k).hidden = (k!==tb.dataset.tab); });
    return;
  }
  const c = e.target.closest('[data-tk]');
  if(c){ showD(c.dataset.tk); return; }
  if(e.target.closest('[data-close]') || e.target.id==='overlay') hideD();
});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Select a current certified plan; historical rendering requires a frozen research bundle")
    a = ap.parse_args()
    if a.date:
        requested = _coerce_date(a.date)
        if requested is None:
            raise SystemExit(f"invalid --date: {a.date}; expected YYYYMMDD or YYYY-MM-DD")
        path = BASE_DIR / "logs" / f"entry_list_{requested:%Y%m%d}.json"
        if not path.is_file():
            raise SystemExit(f"entry list not found: {path}")
    else:
        pick = _latest_dated_file(
            BASE_DIR / "logs", prefix="entry_list_", suffix=".json", date_format="%Y%m%d"
        )
        if not pick:
            raise SystemExit("no dated entry_list_YYYYMMDD.json artifact found")
        path, _source_date = pick
    OUT.write_text(build(path), encoding="utf-8")
    kb = OUT.stat().st_size // 1024
    print(f"entry dashboard v2 -> {OUT}  (source {path.name}, {kb} KB)")


if __name__ == "__main__":
    main()
