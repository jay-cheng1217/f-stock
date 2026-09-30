"""Read-only stock workbench payload for the web UI.

This module deliberately stays advisory-only. It aggregates technical
indicators, prediction metadata, signal explanations, and global feature
importance so the UI can inspect a ticker without touching any production gate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import BASE_DIR
from backend.db.engine import query_df


ROOT = Path(BASE_DIR)
MODEL_DIR = ROOT / "ml" / "models"
REPORT_DIR = ROOT / "ml" / "reports"


def _safe_float(value: Any, digits: int | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits) if digits is not None else numeric


def _safe_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _safe_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)[:10]


def _safe_list(series: pd.Series, digits: int | None = None) -> list[Any]:
    values: list[Any] = []
    for value in series.tolist():
        safe = _safe_float(value, digits)
        values.append(safe if safe is not None else None)
    return values


def _safe_date_list(series: pd.Series) -> list[Any]:
    return [_safe_date(value) for value in series.tolist()]


def _safe_ohlc(df: pd.DataFrame) -> list[list[float | None]]:
    if df.empty:
        return []
    rows: list[list[float | None]] = []
    for _, row in df[["Open", "Close", "Low", "High"]].iterrows():
        rows.append([_safe_float(value) for value in row.tolist()])
    return rows


def _latest_file(directory: Path, pattern: str) -> Path | None:
    files = [path for path in directory.glob(pattern) if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _read_daily_frame(ticker: str, days: int) -> pd.DataFrame:
    limit = max(60, min(int(days), 1000))
    return query_df(
        f"""
        SELECT Date, Open, High, Low, Close, Volume,
               MA_5, MA_20, MA_60,
               "BBU_20_2.0" AS bb_upper,
               "BBM_20_2.0" AS bb_middle,
               "BBL_20_2.0" AS bb_lower,
               VOL_MA_5, VOL_MA_20,
               RSI_14,
               MACD_12_26_9 AS macd,
               MACDs_12_26_9 AS macd_signal,
               MACDh_12_26_9 AS macd_hist,
               K, D,
               Foreign_BuySell, Trust_BuySell, Dealer_BuySell
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {limit}
        """,
        [ticker],
    )


def _calculate_performance(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "Close" not in df:
        return {}

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if close.empty:
        return {}

    daily_return = close.pct_change().dropna()
    first = _safe_float(close.iloc[0])
    last = _safe_float(close.iloc[-1])
    total_return = None
    if first and last is not None:
        total_return = (last / first - 1.0) * 100.0

    volatility = None
    sharpe = None
    if not daily_return.empty:
        std = float(daily_return.std())
        if math.isfinite(std):
            volatility = std * math.sqrt(252.0) * 100.0
            if std > 0:
                sharpe = float(daily_return.mean()) / std * math.sqrt(252.0)

    drawdown = close / close.cummax() - 1.0
    max_drawdown = float(drawdown.min() * 100.0) if not drawdown.empty else None

    return {
        "window_days": int(len(close)),
        "total_return_pct": _safe_float(total_return, 2),
        "annual_volatility_pct": _safe_float(volatility, 2),
        "sharpe": _safe_float(sharpe, 2),
        "max_drawdown_pct": _safe_float(max_drawdown, 2),
        "return_20d_pct": _safe_float((close.iloc[-1] / close.iloc[-21] - 1.0) * 100.0, 2)
        if len(close) >= 21 and close.iloc[-21]
        else None,
    }


def _atr14(df: pd.DataFrame) -> float | None:
    if df.empty or not {"High", "Low", "Close"}.issubset(df.columns):
        return None
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14, min_periods=3).mean().iloc[-1]
    return _safe_float(atr, 2)


PREDICTION_FIELDS = [
    "date",
    "close",
    "pred_return_20d",
    "prob_edge",
    "recommendation",
    "market_sentiment",
    "entry_score",
    "phase",
    "position_52w",
    "price_vs_ma20",
    "price_vs_ma60",
    "risk_adjusted_return",
    "leaderboard_score",
    "two_stage_rank",
    "two_stage_score",
    "shortwave_action",
    "shortwave_entry_zone_low",
    "shortwave_entry_zone_high",
    "shortwave_entry_zone_status",
    "shortwave_entry_zone_note",
    "shortwave_missing_strategy_tags",
    "shortwave_entry_strategy_advice",
    "model_label",
    "model_slot",
    "risk_tags",
    "sector",
    "volume_today",
    "avg_20d_volume",
    "beta_60",
    "gap_pct",
    "foreign_cumsum_10d_norm_raw",
    "inst_buy_ratio_20d",
    "roa_annualized",
    "roe_annualized",
    "operating_margin_latest",
    "net_margin_latest",
    "pe_ratio",
]

DECIMAL_PERCENT_FIELDS = {
    "pred_return_20d",
    "prob_edge",
    "position_52w",
    "price_vs_ma20",
    "price_vs_ma60",
    "risk_adjusted_return",
    "gap_pct",
    "foreign_cumsum_10d_norm_raw",
    "inst_buy_ratio_20d",
    "roa_annualized",
    "roe_annualized",
    "operating_margin_latest",
    "net_margin_latest",
}


def _serialize_prediction_row(raw: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in PREDICTION_FIELDS:
        if field not in raw.index:
            continue
        value = raw[field]
        numeric = _safe_float(value, 6)
        payload[field] = numeric if numeric is not None else _safe_text(value)
        if field in DECIMAL_PERCENT_FIELDS and numeric is not None:
            payload[f"{field}_pct"] = _safe_float(numeric * 100.0, 2)
    return payload


def _load_prediction(ticker: str) -> tuple[dict[str, Any] | None, str | None]:
    path = _latest_file(MODEL_DIR, "predictions_20*.csv")
    if path is None:
        return None, None
    try:
        df = pd.read_csv(path, dtype={"ticker": str})
    except Exception:
        return None, str(path)
    row = df[df["ticker"].astype(str) == ticker]
    if row.empty:
        return None, str(path)

    return _serialize_prediction_row(row.iloc[0]), str(path)


def _load_unified_signal(ticker: str) -> tuple[dict[str, Any] | None, str | None]:
    path = REPORT_DIR / "unified_signals_latest.json"
    payload = None
    source = str(path) if path.exists() else None
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            source = str(payload.get("source_signal_path") or source)
    except Exception:
        payload = None

    try:
        from backend.services.warroom_service import (
            LATEST_UNIFIED_RE,
            build_unified_signals_latest_feed,
            _latest_csv_path,
        )

        latest_signal_path = _latest_csv_path(LATEST_UNIFIED_RE)
        cached_signal_path = (payload or {}).get("source_signal_path")
        cache_stale = bool(
            latest_signal_path
            and (
                not cached_signal_path
                or Path(str(cached_signal_path)).resolve() != Path(str(latest_signal_path)).resolve()
            )
        )
        if payload is None or cache_stale:
            payload = build_unified_signals_latest_feed()
            source = str(payload.get("source_signal_path") or latest_signal_path or path)
    except Exception:
        if payload is None:
            return None, source

    for bucket in ("selected", "rejected", "hard_blocked", "hard_blocks"):
        rows = payload.get(bucket) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if str(row.get("ticker", "")).strip() == ticker:
                result = dict(row)
                result["bucket"] = bucket
                return result, source
    return None, source


def _extract_feature_importance_rows(df: pd.DataFrame, limit: int = 8) -> list[dict[str, Any]]:
    if df.empty:
        return []

    feature_col = next((col for col in ("feature", "Feature", "feature_name", "name") if col in df.columns), None)
    importance_col = next(
        (col for col in ("importance", "importance_gain", "gain", "split", "weight") if col in df.columns),
        None,
    )
    if not feature_col or not importance_col:
        return []

    work = df[[feature_col, importance_col]].copy()
    work[importance_col] = pd.to_numeric(work[importance_col], errors="coerce")
    work = work.dropna(subset=[importance_col]).sort_values(importance_col, ascending=False).head(limit)
    total = float(work[importance_col].sum()) if not work.empty else 0.0
    rows: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        value = _safe_float(row[importance_col], 4)
        rows.append(
            {
                "feature": str(row[feature_col]),
                "importance": value,
                "share_pct": _safe_float((float(row[importance_col]) / total * 100.0) if total else None, 2),
            }
        )
    return rows


def _load_feature_importance() -> dict[str, Any]:
    path = _latest_file(REPORT_DIR, "*feature_importance*.csv")
    if path is None:
        return {"source": None, "top": []}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {"source": str(path), "top": []}
    return {"source": str(path), "top": _extract_feature_importance_rows(df)}


def _latest_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty:
        return None
    return df.iloc[-1]


def _analysis_cards(latest: pd.Series | None, performance: dict[str, Any], prediction: dict[str, Any] | None, signal: dict[str, Any] | None) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    if latest is not None:
        rsi = _safe_float(latest.get("RSI_14"), 1)
        macd_hist = _safe_float(latest.get("macd_hist"), 3)
        close = _safe_float(latest.get("Close"), 2)
        ma20 = _safe_float(latest.get("MA_20"), 2)
        ma60 = _safe_float(latest.get("MA_60"), 2)
        vol = _safe_float(latest.get("Volume"))
        vol_ma20 = _safe_float(latest.get("VOL_MA_20"))

        if close is not None and ma20:
            distance = (close / ma20 - 1.0) * 100.0
            tone = "good" if distance >= 0 else "warn"
            cards.append(
                {
                    "title": "價格結構",
                    "tone": tone,
                    "summary": f"Close vs MA20 {distance:+.1f}%",
                    "detail": f"MA20 {ma20:.2f} / MA60 {ma60:.2f}" if ma60 is not None else f"MA20 {ma20:.2f}",
                }
            )

        if rsi is not None:
            if rsi >= 70:
                tone, summary = "warn", "RSI 偏熱，追價需縮手"
            elif rsi <= 30:
                tone, summary = "blue", "RSI 偏冷，適合觀察止跌"
            else:
                tone, summary = "good", "RSI 未進入極端區"
            cards.append({"title": "動能溫度", "tone": tone, "summary": summary, "detail": f"RSI14 {rsi:.1f}"})

        if macd_hist is not None:
            tone = "good" if macd_hist > 0 else "warn"
            cards.append(
                {
                    "title": "MACD",
                    "tone": tone,
                    "summary": "柱狀體為正" if macd_hist > 0 else "柱狀體為負",
                    "detail": f"MACD Hist {macd_hist:+.3f}",
                }
            )

        if vol is not None and vol_ma20:
            ratio = vol / vol_ma20
            tone = "warn" if ratio >= 1.8 else "blue" if ratio >= 1.1 else "gray"
            cards.append(
                {
                    "title": "成交量",
                    "tone": tone,
                    "summary": f"量比 {ratio:.2f}x",
                    "detail": "放量需確認是否伴隨追價風險" if ratio >= 1.8 else "量能未異常放大",
                }
            )

    beta = _safe_float((prediction or {}).get("beta_60"), 2)
    pred20 = _safe_float((prediction or {}).get("pred_return_20d_pct"), 2)
    if beta is not None or pred20 is not None:
        tone = "warn" if beta is not None and beta >= 1.3 else "good"
        cards.append(
            {
                "title": "ML 風險調整",
                "tone": tone,
                "summary": f"預估20D {pred20:+.2f}%" if pred20 is not None else "無 20D 預估",
                "detail": f"Beta {beta:.2f}" if beta is not None else "Beta 缺值",
            }
        )

    if signal:
        cards.append(
            {
                "title": "進場 Gate",
                "tone": "good" if signal.get("bucket") == "selected" else "warn",
                "summary": str(signal.get("explain_action_label") or signal.get("bucket") or "signal"),
                "detail": str(signal.get("explain_summary") or signal.get("penalty_overlay_reason") or "-"),
            }
        )

    if performance:
        cards.append(
            {
                "title": "績效風險",
                "tone": "warn" if (performance.get("max_drawdown_pct") or 0) < -12 else "blue",
                "summary": f"Sharpe {performance.get('sharpe') if performance.get('sharpe') is not None else '-'}",
                "detail": f"Vol {performance.get('annual_volatility_pct') or '-'}% / MDD {performance.get('max_drawdown_pct') or '-'}%",
            }
        )

    return cards


def build_stock_workbench(ticker: str, days: int = 240) -> dict[str, Any]:
    normalized = "".join(ch for ch in str(ticker).strip() if ch.isalnum())
    if not normalized:
        return {"status": "error", "message": "ticker is required"}

    df = _read_daily_frame(normalized, days)
    if not df.empty:
        df = df.iloc[::-1].reset_index(drop=True)
    latest = _latest_row(df)
    performance = _calculate_performance(df)
    prediction, prediction_source = _load_prediction(normalized)
    signal, signal_source = _load_unified_signal(normalized)
    feature_importance = _load_feature_importance()

    name = None
    sector = None
    for source in (signal, prediction):
        if not source:
            continue
        name = name or _safe_text(source.get("name"))
        sector = sector or _safe_text(source.get("sector"))

    chart = {
        "dates": _safe_date_list(df["Date"]) if "Date" in df else [],
        "ohlc": _safe_ohlc(df),
        "close": _safe_list(df["Close"]) if "Close" in df else [],
        "volume": _safe_list(df["Volume"]) if "Volume" in df else [],
        "ma5": _safe_list(df["MA_5"]) if "MA_5" in df else [],
        "ma20": _safe_list(df["MA_20"]) if "MA_20" in df else [],
        "ma60": _safe_list(df["MA_60"]) if "MA_60" in df else [],
        "bb_upper": _safe_list(df["bb_upper"]) if "bb_upper" in df else [],
        "bb_middle": _safe_list(df["bb_middle"]) if "bb_middle" in df else [],
        "bb_lower": _safe_list(df["bb_lower"]) if "bb_lower" in df else [],
        "vol_ma20": _safe_list(df["VOL_MA_20"]) if "VOL_MA_20" in df else [],
        "macd": _safe_list(df["macd"]) if "macd" in df else [],
        "macd_signal": _safe_list(df["macd_signal"]) if "macd_signal" in df else [],
        "macd_hist": _safe_list(df["macd_hist"]) if "macd_hist" in df else [],
        "rsi14": _safe_list(df["RSI_14"]) if "RSI_14" in df else [],
        "k": _safe_list(df["K"]) if "K" in df else [],
        "d": _safe_list(df["D"]) if "D" in df else [],
        "foreign": _safe_list(df["Foreign_BuySell"]) if "Foreign_BuySell" in df else [],
        "trust": _safe_list(df["Trust_BuySell"]) if "Trust_BuySell" in df else [],
        "dealer": _safe_list(df["Dealer_BuySell"]) if "Dealer_BuySell" in df else [],
    }

    latest_indicators = {}
    if latest is not None:
        close = _safe_float(latest.get("Close"), 2)
        vol = _safe_float(latest.get("Volume"))
        vol_ma20 = _safe_float(latest.get("VOL_MA_20"))
        latest_indicators = {
            "date": _safe_date(latest.get("Date")),
            "close": close,
            "change_pct": _safe_float((close / _safe_float(df["Close"].iloc[-2]) - 1.0) * 100.0, 2)
            if close is not None and len(df) >= 2 and _safe_float(df["Close"].iloc[-2])
            else None,
            "ma20": _safe_float(latest.get("MA_20"), 2),
            "ma60": _safe_float(latest.get("MA_60"), 2),
            "price_vs_ma20_pct": _safe_float((close / _safe_float(latest.get("MA_20")) - 1.0) * 100.0, 2)
            if close is not None and _safe_float(latest.get("MA_20"))
            else None,
            "price_vs_ma60_pct": _safe_float((close / _safe_float(latest.get("MA_60")) - 1.0) * 100.0, 2)
            if close is not None and _safe_float(latest.get("MA_60"))
            else None,
            "rsi14": _safe_float(latest.get("RSI_14"), 2),
            "macd": _safe_float(latest.get("macd"), 4),
            "macd_signal": _safe_float(latest.get("macd_signal"), 4),
            "macd_hist": _safe_float(latest.get("macd_hist"), 4),
            "k": _safe_float(latest.get("K"), 2),
            "d": _safe_float(latest.get("D"), 2),
            "atr14": _atr14(df),
            "volume": vol,
            "volume_ratio": _safe_float(vol / vol_ma20, 2) if vol is not None and vol_ma20 else None,
            "foreign": _safe_float(latest.get("Foreign_BuySell")),
            "trust": _safe_float(latest.get("Trust_BuySell")),
            "dealer": _safe_float(latest.get("Dealer_BuySell")),
        }

    status = "ok" if not df.empty or prediction or signal else "not_found"
    return {
        "status": status,
        "ticker": normalized,
        "name": name,
        "sector": sector,
        "days": days,
        "source": {
            "prediction": prediction_source,
            "signal": signal_source,
            "feature_importance": feature_importance.get("source"),
        },
        "latest": latest_indicators,
        "performance": performance,
        "prediction": prediction or {},
        "signal": signal or {},
        "ml": {
            "advisory_only": True,
            "note": "Feature importance and prediction evidence are advisory UI context only; they do not alter entry gates.",
            "feature_importance": feature_importance.get("top", []),
        },
        "analysis_cards": _analysis_cards(latest, performance, prediction, signal),
        "chart": chart,
    }
