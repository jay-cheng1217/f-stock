"""Advisory price-scenario forecast service for the web UI.

The forecast is a scenario envelope anchored on the current 20D production
prediction, recent realized volatility, local news sentiment, and observable
technical risk. It is intentionally read-only and does not feed any production
entry gate.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from backend.config import BASE_DIR, DAILY_K_DIR
from backend.db.engine import query_df
from backend.services.macro_event_service import build_macro_event_context
from backend.services.news_service import score_stock_news_sentiment


ROOT = Path(BASE_DIR)
MODEL_DIR = ROOT / "ml" / "models"
ETF_FORECAST_COMPONENTS_PATH = ROOT / "config" / "etf_forecast_components.yaml"
PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
DEFAULT_HISTORY_DAYS = 160
DEFAULT_CONTEXT_DAYS = 800
DEFAULT_HORIZON_DAYS = 20
DEFAULT_ETF_MIN_PREDICTION_COVERAGE = 0.40


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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _latest_prediction_file() -> Path | None:
    files = [
        path
        for path in sorted(MODEL_DIR.glob("predictions_*.csv"))
        if path.is_file() and PRODUCTION_PREDICTION_RE.match(path.name)
    ]
    return files[-1] if files else None


def _load_latest_prediction_row(ticker: str) -> tuple[dict[str, Any] | None, str | None]:
    path = _latest_prediction_file()
    if path is None:
        return None, None
    try:
        df = _load_prediction_frame(path)
    except Exception:
        return None, str(path)
    return _prediction_row_from_frame(df, ticker), str(path)


def _load_prediction_frame(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"ticker": str})


def _prediction_row_from_frame(df: pd.DataFrame, ticker: str) -> dict[str, Any] | None:
    if "ticker" not in df.columns:
        return None
    normalized = str(ticker or "").strip().upper()
    row = df[df["ticker"].astype(str).str.strip().str.upper() == normalized]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def _load_name_sector_lookup(ticker: str) -> tuple[str | None, str | None]:
    try:
        from ml.features.sector import SECTOR_MAPPING_PATH

        path = Path(SECTOR_MAPPING_PATH)
        if not path.exists():
            return None, None
        df = pd.read_csv(path, dtype={"Ticker": str})
        row = df[df["Ticker"].astype(str).str.strip() == ticker]
        if row.empty:
            return None, None
        return _safe_text(row.iloc[0].get("Name")), _safe_text(row.iloc[0].get("Sector"))
    except Exception:
        return None, None


def _normalize_daily_frame(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    for column in ("Date", "Close"):
        if column not in work.columns:
            return pd.DataFrame()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    work["Close"] = pd.to_numeric(work["Close"], errors="coerce")
    for column in ("Open", "High", "Low", "Volume", "MA_20", "MA_60", "RSI_14", "ATR_14", "VOL_MA_20"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["Date", "Close"]).sort_values("Date").tail(limit)
    return work.reset_index(drop=True)


def _read_daily_history(ticker: str, limit: int = DEFAULT_HISTORY_DAYS) -> tuple[pd.DataFrame, str | None]:
    limit = max(60, min(int(limit), 1000))
    sql = f"""
        SELECT Date, Open, High, Low, Close, Volume,
               MA_20, MA_60, RSI_14, ATR_14, VOL_MA_20
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {limit}
    """
    try:
        df = query_df(sql, [ticker])
        normalized = _normalize_daily_frame(df, limit)
        if not normalized.empty:
            return normalized, "duckdb:daily_k"
    except Exception:
        pass

    csv_path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not csv_path.exists():
        return pd.DataFrame(), None
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return pd.DataFrame(), str(csv_path)
    normalized = _normalize_daily_frame(df, limit)
    return normalized, str(csv_path)


def _future_weekdays(last_date: pd.Timestamp, horizon_days: int) -> list[str]:
    dates: list[str] = []
    current = last_date.to_pydatetime()
    while len(dates) < horizon_days:
        current = current + timedelta(days=1)
        if current.weekday() < 5:
            dates.append(current.date().isoformat())
    return dates


def _max_drawdown_pct(close: pd.Series, window: int = 60) -> float | None:
    series = pd.to_numeric(close, errors="coerce").dropna().tail(window)
    if series.empty:
        return None
    drawdown = series / series.cummax() - 1.0
    return _safe_float(float(drawdown.min()) * 100.0, 2)


def _prediction_signal_expected_return(row: dict[str, Any] | None) -> tuple[float, str] | None:
    if row:
        pred_return = _safe_float(row.get("pred_return_20d"))
        if pred_return is not None:
            return _clamp(pred_return, -0.35, 0.35), "production_pred_return_20d"
        up_prob = _safe_float(row.get("up_prob"))
        down_prob = _safe_float(row.get("down_prob"))
        if up_prob is not None and down_prob is not None:
            return _clamp((up_prob - down_prob) * 0.08, -0.12, 0.12), "probability_edge_fallback"
    return None


def _recent_return_expected_return(returns: pd.Series) -> tuple[float, str]:
    recent = pd.to_numeric(returns, errors="coerce").dropna().tail(60)
    if recent.empty:
        return 0.0, "neutral_fallback"
    return _clamp(float(recent.mean()) * DEFAULT_HORIZON_DAYS, -0.12, 0.12), "recent_return_fallback"


def _prediction_expected_return(row: dict[str, Any] | None, returns: pd.Series) -> tuple[float, str]:
    signal = _prediction_signal_expected_return(row)
    if signal is not None:
        return signal
    return _recent_return_expected_return(returns)


def _component_weight(value: Any) -> float | None:
    weight = _safe_float(value)
    if weight is None:
        return None
    if 1.0 < weight <= 100.0:
        weight = weight / 100.0
    if weight <= 0.0 or weight > 1.0:
        return None
    return weight


def _load_etf_component_config(ticker: str) -> dict[str, Any] | None:
    if not ETF_FORECAST_COMPONENTS_PATH.exists():
        return None
    try:
        with ETF_FORECAST_COMPONENTS_PATH.open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
    except Exception:
        return None

    etfs = payload.get("etfs") if isinstance(payload, dict) else None
    if not isinstance(etfs, dict):
        return None
    config = etfs.get(str(ticker).strip().upper())
    if not isinstance(config, dict):
        return None

    components: list[dict[str, Any]] = []
    for raw in config.get("components") or []:
        if not isinstance(raw, dict):
            continue
        component_ticker = str(raw.get("ticker") or "").strip().upper()
        weight = _component_weight(raw.get("weight"))
        if not component_ticker or weight is None:
            continue
        component = dict(raw)
        component["ticker"] = component_ticker
        component["weight"] = weight
        components.append(component)
    if not components:
        return None

    normalized = dict(config)
    normalized["components"] = components
    return normalized


def _etf_component_weighted_expected_return(
    ticker: str,
    prediction_source: str | None,
    returns: pd.Series,
) -> dict[str, Any] | None:
    config = _load_etf_component_config(ticker)
    if not config or not prediction_source:
        return None

    try:
        prediction_frame = _load_prediction_frame(prediction_source)
    except Exception:
        return {
            "status": "prediction_frame_unavailable",
            "method": config.get("method") or "component_weighted_proxy",
            "message": "latest prediction CSV could not be read for ETF component proxy",
        }

    configured_weight = 0.0
    covered_weight = 0.0
    component_contribution = 0.0
    used_count = 0
    missing_count = 0
    components: list[dict[str, Any]] = []

    for component in config.get("components") or []:
        weight = float(component["weight"])
        configured_weight += weight
        component_ticker = str(component["ticker"])
        row = _prediction_row_from_frame(prediction_frame, component_ticker)
        expected_signal = _prediction_signal_expected_return(row)
        item = {
            "ticker": component_ticker,
            "name": _safe_text(component.get("name")),
            "weight": _safe_float(weight, 4),
            "weight_pct": _safe_float(weight * 100.0, 2),
            "used": False,
        }
        if expected_signal is None:
            missing_count += 1
            item["reason"] = "missing_prediction_signal"
            components.append(item)
            continue

        expected_return, expected_source = expected_signal
        contribution = weight * expected_return
        covered_weight += weight
        component_contribution += contribution
        used_count += 1
        item.update(
            {
                "used": True,
                "expected_return_20d_pct": _safe_float(expected_return * 100.0, 2),
                "expected_return_source": expected_source,
                "contribution_20d_pct": _safe_float(contribution * 100.0, 2),
                "recommendation": _safe_text((row or {}).get("recommendation")),
                "prob_edge_pct": _safe_float(
                    (_safe_float((row or {}).get("prob_edge")) or 0.0) * 100.0,
                    2,
                ),
            }
        )
        components.append(item)

    min_coverage = _safe_float(config.get("min_prediction_coverage")) or DEFAULT_ETF_MIN_PREDICTION_COVERAGE
    residual_return, residual_source = _recent_return_expected_return(returns)
    residual_weight = max(0.0, 1.0 - covered_weight)
    expected_return = component_contribution + residual_weight * residual_return
    status = "ok" if covered_weight >= min_coverage else "insufficient_prediction_coverage"

    used_components = [component for component in components if component.get("used")]
    top_component = max(used_components, key=lambda item: float(item.get("weight") or 0.0), default=None)
    return {
        "status": status,
        "method": config.get("method") or "component_weighted_proxy",
        "name": _safe_text(config.get("name")),
        "source_label": _safe_text(config.get("source_label")),
        "source_url": _safe_text(config.get("source_url")),
        "source_as_of": _safe_text(config.get("source_as_of")),
        "weights_basis": _safe_text(config.get("weights_basis")),
        "expected_return_20d": _safe_float(expected_return, 6),
        "expected_return_20d_pct": _safe_float(expected_return * 100.0, 2),
        "expected_return_source": "etf_component_weighted_prediction",
        "configured_weight": _safe_float(configured_weight, 4),
        "configured_weight_pct": _safe_float(configured_weight * 100.0, 2),
        "covered_weight": _safe_float(covered_weight, 4),
        "covered_weight_pct": _safe_float(covered_weight * 100.0, 2),
        "min_prediction_coverage_pct": _safe_float(min_coverage * 100.0, 2),
        "residual_weight": _safe_float(residual_weight, 4),
        "residual_weight_pct": _safe_float(residual_weight * 100.0, 2),
        "residual_return_20d_pct": _safe_float(residual_return * 100.0, 2),
        "residual_return_source": residual_source,
        "component_contribution_20d_pct": _safe_float(component_contribution * 100.0, 2),
        "components_used": used_count,
        "components_missing": missing_count,
        "top_component": top_component,
        "components": components,
    }


def _build_forecast_paths(
    last_close: float,
    last_date: pd.Timestamp,
    expected_return_20d: float,
    realized_vol_daily: float,
    horizon_days: int,
) -> list[dict[str, float | str]]:
    future_dates = _future_weekdays(last_date, horizon_days)
    daily_mu = math.log1p(_clamp(expected_return_20d, -0.45, 0.45)) / DEFAULT_HORIZON_DAYS
    vol = _clamp(realized_vol_daily, 0.004, 0.08)
    rows: list[dict[str, float | str]] = []
    for step, date_value in enumerate(future_dates, start=1):
        drift = daily_mu * step
        width = vol * math.sqrt(step)
        rows.append(
            {
                "date": date_value,
                "base": round(last_close * math.exp(drift), 2),
                "upside": round(last_close * math.exp(drift + 0.75 * width), 2),
                "downside": round(last_close * math.exp(drift - 0.75 * width), 2),
                "stress": round(last_close * math.exp(drift - 1.40 * width), 2),
            }
        )
    return rows


def _risk_component(label: str, value: float | None, score: float, detail: str) -> dict[str, Any]:
    return {
        "label": label,
        "value": _safe_float(value, 2),
        "score": _safe_float(_clamp(score, 0.0, 100.0), 2),
        "detail": detail,
    }


def _risk_from_history(
    df: pd.DataFrame,
    returns: pd.Series,
    prediction: dict[str, Any] | None,
    expected_return_20d: float,
    ticker: str,
) -> tuple[float, list[dict[str, Any]], dict[str, Any]]:
    latest = df.iloc[-1]
    vol_daily = _safe_float(pd.to_numeric(returns, errors="coerce").dropna().tail(60).std()) or 0.018
    annual_vol_pct = vol_daily * math.sqrt(252.0) * 100.0
    vol_score = _clamp((annual_vol_pct - 12.0) / 38.0 * 26.0, 0.0, 26.0)

    max_drawdown = _max_drawdown_pct(df["Close"], window=60)
    drawdown_score = _clamp(abs(max_drawdown or 0.0) / 35.0 * 22.0, 0.0, 22.0)

    close = _safe_float(latest.get("Close")) or 0.0
    ma20 = _safe_float(latest.get("MA_20"))
    overheat_pct = ((close / ma20 - 1.0) * 100.0) if close and ma20 else None
    overheat_score = _clamp(abs(overheat_pct or 0.0) / 18.0 * 14.0, 0.0, 14.0)

    beta = _safe_float((prediction or {}).get("beta_60"))
    beta_score = _clamp(((beta or 1.0) - 1.0) / 1.0 * 10.0, 0.0, 10.0)

    risk_tags = str((prediction or {}).get("risk_tags") or "").strip()
    tag_count = len([part for part in re.split(r"[;,|]\s*", risk_tags) if part])
    tag_score = _clamp(tag_count * 4.0, 0.0, 14.0)

    model_score = _clamp(abs(min(expected_return_20d, 0.0)) * 180.0, 0.0, 10.0)

    try:
        news_score, news_detail = score_stock_news_sentiment(ticker, days=5, limit=5)
    except Exception:
        news_score, news_detail = 50.0, "news sentiment unavailable"
    news_risk = _clamp((50.0 - news_score) / 30.0 * 9.0, 0.0, 9.0)

    factors = [
        _risk_component("Realized Volatility", annual_vol_pct, vol_score, "60D annualized volatility"),
        _risk_component("Drawdown", max_drawdown, drawdown_score, "60D max drawdown"),
        _risk_component("MA20 Distance", overheat_pct, overheat_score, "absolute close-vs-MA20 distance"),
        _risk_component("Beta", beta, beta_score, "model beta_60 when available"),
        _risk_component("Risk Tags", float(tag_count), tag_score, risk_tags or "no model risk tag"),
        _risk_component("Model Direction", expected_return_20d * 100.0, model_score, "negative 20D forecast adds risk"),
        _risk_component("News Sentiment", news_score, news_risk, news_detail),
    ]
    risk_score = _clamp(sum(float(item["score"] or 0.0) for item in factors), 0.0, 100.0)
    metrics = {
        "annual_volatility_pct": _safe_float(annual_vol_pct, 2),
        "max_drawdown_pct": max_drawdown,
        "price_vs_ma20_pct": _safe_float(overheat_pct, 2),
        "beta_60": beta,
        "news_score": _safe_float(news_score, 1),
        "news_detail": news_detail,
        "risk_tag_count": tag_count,
    }
    return round(risk_score, 1), factors, metrics


def _volatility_pulse(df: pd.DataFrame, returns: pd.Series) -> dict[str, Any]:
    abs_return = pd.to_numeric(returns, errors="coerce").abs()
    pulse = abs_return.rolling(5, min_periods=3).mean()
    baseline = pulse.rolling(120, min_periods=30)
    z_score = (pulse - baseline.mean()) / baseline.std().replace(0, pd.NA)
    latest_z = _safe_float(z_score.dropna().iloc[-1] if not z_score.dropna().empty else None)
    latest_pulse = _safe_float(pulse.dropna().iloc[-1] if not pulse.dropna().empty else None)
    weekly_max_z = _safe_float(z_score.dropna().tail(5).max() if not z_score.dropna().empty else None)

    clean_pulse = pulse.dropna()
    percentile = None
    if latest_pulse is not None and not clean_pulse.empty:
        percentile = float((clean_pulse <= latest_pulse).mean() * 100.0)

    gate_z = 2.8
    scale = int(round(_clamp(((latest_z or 0.0) / gate_z) * 4.0, 0.0, 10.0)))
    status = "active" if scale >= 4 else "below_gate"
    return {
        "label": "volatility_pulse",
        "scale": scale,
        "scale_max": 10,
        "z_score": _safe_float(latest_z, 2),
        "percentile": _safe_float(percentile, 1),
        "weekly_max_z": _safe_float(weekly_max_z, 2),
        "gate_scale": 4,
        "gate_z": gate_z,
        "status": status,
        "message": "meets rebound-watch gate" if status == "active" else "below action gate",
    }


def _crash_detector(
    returns: pd.Series,
    risk_metrics: dict[str, Any],
    expected_return_20d: float,
) -> dict[str, Any]:
    ret5 = _safe_float((1.0 + pd.to_numeric(returns, errors="coerce").dropna().tail(5)).prod() - 1.0)
    vol_daily = (risk_metrics.get("annual_volatility_pct") or 0.0) / math.sqrt(252.0) / 100.0
    drawdown_abs = abs(float(risk_metrics.get("max_drawdown_pct") or 0.0)) / 100.0
    news_drag = max(0.0, (50.0 - float(risk_metrics.get("news_score") or 50.0)) / 50.0)
    tag_drag = min(float(risk_metrics.get("risk_tag_count") or 0.0) / 4.0, 1.0)
    negative_5d = abs(min(ret5 or 0.0, 0.0))
    negative_model = abs(min(expected_return_20d, 0.0))

    logit = (
        -4.15
        + vol_daily * 12.0
        + negative_5d * 5.0
        + drawdown_abs * 1.1
        + news_drag * 0.35
        + tag_drag * 0.35
        + negative_model * 1.4
    )
    probability = 1.0 / (1.0 + math.exp(-logit))
    probability_pct = _clamp(probability * 100.0, 0.0, 100.0)

    if probability_pct >= 10.0:
        level, label = 3, "EXTREME"
    elif probability_pct >= 6.0:
        level, label = 2, "DANGER"
    elif probability_pct >= 3.0:
        level, label = 1, "WATCH"
    else:
        level, label = 0, "NORMAL"

    return {
        "probability_5d_pct": _safe_float(probability_pct, 1),
        "level": level,
        "label": label,
        "segments": ["normal", "watch", "danger", "extreme"],
        "message": "crash probability entered danger zone" if level >= 2 else "crash probability below danger zone",
        "drivers": {
            "ret_5d_pct": _safe_float((ret5 or 0.0) * 100.0, 2),
            "daily_vol_pct": _safe_float(vol_daily * 100.0, 2),
            "drawdown_pct": _safe_float(-drawdown_abs * 100.0, 2),
            "news_score": risk_metrics.get("news_score"),
        },
    }


def _feature_context_frame(df: pd.DataFrame, returns: pd.Series) -> pd.DataFrame:
    close = pd.to_numeric(df["Close"], errors="coerce")
    ma20 = pd.to_numeric(df.get("MA_20"), errors="coerce")
    ma60 = pd.to_numeric(df.get("MA_60"), errors="coerce")
    abs_return = pd.to_numeric(returns, errors="coerce").abs()
    features = pd.DataFrame(index=df.index)
    features["date"] = pd.to_datetime(df["Date"], errors="coerce")
    features["pulse"] = abs_return.rolling(5, min_periods=3).mean()
    features["vol20"] = pd.to_numeric(returns, errors="coerce").rolling(20, min_periods=10).std()
    features["ret5"] = close.pct_change(5)
    features["ret20"] = close.pct_change(20)
    features["ma20_distance"] = close / ma20 - 1.0
    features["ma60_distance"] = close / ma60 - 1.0
    features["drawdown60"] = close / close.rolling(60, min_periods=20).max() - 1.0
    features["future_return_10d"] = close.shift(-10) / close - 1.0
    return features


def _historical_context(df: pd.DataFrame, returns: pd.Series) -> dict[str, Any]:
    features = _feature_context_frame(df, returns)
    feature_cols = ["pulse", "vol20", "ret5", "ret20", "ma20_distance", "ma60_distance", "drawdown60"]
    usable = features.dropna(subset=feature_cols + ["future_return_10d"]).copy()
    current_candidates = features.dropna(subset=feature_cols)
    if usable.empty or current_candidates.empty:
        return {"status": "insufficient", "sample_count": 0, "message": "not enough historical contexts"}

    current = current_candidates.iloc[-1][feature_cols].astype(float)
    means = usable[feature_cols].mean()
    stds = usable[feature_cols].std().replace(0, 1.0)
    normalized = (usable[feature_cols] - means) / stds
    current_norm = (current - means) / stds
    usable["distance"] = ((normalized - current_norm) ** 2).sum(axis=1) ** 0.5
    similar = usable.sort_values("distance").head(min(656, len(usable))).copy()
    future = pd.to_numeric(similar["future_return_10d"], errors="coerce").dropna()
    if future.empty:
        return {"status": "insufficient", "sample_count": 0, "message": "no forward-return labels"}

    close = pd.to_numeric(df["Close"], errors="coerce").reset_index(drop=True)
    max_drawdowns: list[float] = []
    for index in similar.index.tolist():
        if index + 10 >= len(close) or not close.iloc[index]:
            continue
        path = close.iloc[index : index + 11]
        max_drawdowns.append(float((path / close.iloc[index] - 1.0).min()))

    win_rate = float((future > 0).mean() * 100.0)
    loss_rate = 100.0 - win_rate
    avg_return = float(future.mean() * 100.0)
    best_return = float(future.max() * 100.0)
    worst_return = float(future.min() * 100.0)
    avg_max_drawdown = float(pd.Series(max_drawdowns).mean() * 100.0) if max_drawdowns else None
    score = int(round(_clamp(5.0 + avg_return + (win_rate - 50.0) / 12.0, 0.0, 10.0)))

    return {
        "status": "ok",
        "score": score,
        "sample_count": int(len(similar)),
        "horizon_days": 10,
        "up_rate_pct": _safe_float(win_rate, 1),
        "down_rate_pct": _safe_float(loss_rate, 1),
        "avg_return_pct": _safe_float(avg_return, 2),
        "best_return_pct": _safe_float(best_return, 2),
        "worst_return_pct": _safe_float(worst_return, 2),
        "avg_max_drawdown_pct": _safe_float(avg_max_drawdown, 2),
        "message": "historical contexts lean positive" if avg_return > 0 and win_rate >= 50 else "historical contexts are weak or mixed",
    }


def _advanced_signals(
    df: pd.DataFrame,
    returns: pd.Series,
    risk_metrics: dict[str, Any],
    expected_return_20d: float,
) -> dict[str, Any]:
    return {
        "rebound_strength": _volatility_pulse(df, returns),
        "crash_detector": _crash_detector(returns, risk_metrics, expected_return_20d),
        "historical_context": _historical_context(df, returns),
    }


def _risk_label(score: float) -> str:
    if score >= 66.0:
        return "high"
    if score >= 36.0:
        return "medium"
    return "low"


def _market_state(score: float, expected_return_pct: float) -> str:
    if score <= 35.0 and expected_return_pct >= 1.0:
        return "RISK-ON"
    if score >= 66.0 or expected_return_pct <= -2.0:
        return "RISK-OFF"
    return "NEUTRAL"


def build_price_forecast(ticker: str, horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict[str, Any]:
    key = str(ticker or "").strip().upper()
    if not key:
        return {"status": "error", "message": "ticker is required"}
    horizon = max(5, min(int(horizon_days or DEFAULT_HORIZON_DAYS), 60))

    df, history_source = _read_daily_history(key, limit=max(DEFAULT_CONTEXT_DAYS, horizon + 80))
    if df.empty or len(df) < 20:
        return {
            "status": "not_found",
            "ticker": key,
            "message": "daily_k history unavailable or insufficient",
            "source": {"history": history_source},
        }

    prediction, prediction_source = _load_latest_prediction_row(key)
    if prediction_source is None:
        prediction_status = "prediction_file_missing"
    elif prediction:
        prediction_status = "matched"
    else:
        prediction_status = "ticker_not_in_prediction_universe"
    name, sector = _load_name_sector_lookup(key)
    if prediction:
        name = _safe_text(prediction.get("name")) or name
        sector = _safe_text(prediction.get("sector")) or sector

    close = pd.to_numeric(df["Close"], errors="coerce")
    returns = close.pct_change().dropna()
    last = df.iloc[-1]
    last_close = float(last["Close"])
    last_date = pd.to_datetime(last["Date"])
    etf_proxy = None
    if prediction is None and prediction_source is not None:
        etf_proxy = _etf_component_weighted_expected_return(key, prediction_source, returns)

    if etf_proxy and etf_proxy.get("status") == "ok":
        expected_return_20d = float(etf_proxy.get("expected_return_20d") or 0.0)
        expected_source = str(etf_proxy.get("expected_return_source") or "etf_component_weighted_prediction")
        prediction_status = "etf_component_proxy"
        name = name or _safe_text(etf_proxy.get("name"))
        sector = sector or "ETF"
    else:
        expected_return_20d, expected_source = _prediction_expected_return(prediction, returns)
    realized_vol_daily = _safe_float(returns.tail(60).std()) or 0.018

    forecast_rows = _build_forecast_paths(
        last_close=last_close,
        last_date=last_date,
        expected_return_20d=expected_return_20d,
        realized_vol_daily=realized_vol_daily,
        horizon_days=horizon,
    )
    risk_score, risk_factors, risk_metrics = _risk_from_history(
        df=df,
        returns=returns,
        prediction=prediction,
        expected_return_20d=expected_return_20d,
        ticker=key,
    )
    macro_events = build_macro_event_context(
        as_of=pd.to_datetime(last_date).date().isoformat(),
        horizon_days=horizon,
    )
    macro_score = _safe_float(macro_events.get("risk_score")) or 0.0
    macro_count = int(macro_events.get("event_count") or 0)
    next_macro = macro_events.get("next_event") or {}
    macro_penalty = _clamp(macro_score * 0.12, 0.0, 12.0)
    if macro_count:
        risk_factors.append(
            _risk_component(
                "Macro Event Cluster",
                macro_score,
                macro_penalty,
                f"{macro_count} events in forecast horizon; next: {next_macro.get('title') or '-'}",
            )
        )
        risk_score = round(_clamp(risk_score + macro_penalty, 0.0, 100.0), 1)
    risk_metrics.update(
        {
            "macro_event_risk_score": _safe_float(macro_score, 1),
            "macro_event_count": macro_count,
            "macro_event_level": macro_events.get("risk_level"),
        }
    )
    advanced_signals = _advanced_signals(
        df=df,
        returns=returns,
        risk_metrics=risk_metrics,
        expected_return_20d=expected_return_20d,
    )
    expected_return_pct = expected_return_20d * 100.0
    state = _market_state(risk_score, expected_return_pct)
    recommendation = _safe_text((prediction or {}).get("recommendation"))
    risk_tags = _safe_text((prediction or {}).get("risk_tags"))

    history_rows = [
        {"date": pd.to_datetime(row["Date"]).date().isoformat(), "close": _safe_float(row["Close"], 2)}
        for _, row in df.tail(DEFAULT_HISTORY_DAYS).iterrows()
    ]

    notes = [
        "Scenario forecast is advisory-only and does not alter entry filters.",
        f"Expected path source: {expected_source}.",
    ]
    if prediction_source is None:
        notes.append("Production prediction CSV missing; forecast fell back to recent returns.")
    elif etf_proxy and etf_proxy.get("status") == "ok":
        notes.append(
            "ETF is outside the direct stock model; expected path uses component-weighted predictions plus ETF residual fallback."
        )
        notes.append(
            f"ETF proxy coverage: {etf_proxy.get('covered_weight_pct')}% components, "
            f"{etf_proxy.get('residual_weight_pct')}% residual."
        )
    elif prediction is None:
        notes.append(
            "Ticker is not in the production prediction universe; expected path fell back to recent returns."
        )
    if etf_proxy and etf_proxy.get("status") == "insufficient_prediction_coverage":
        notes.append(
            "ETF component proxy was configured but below minimum prediction coverage; expected path fell back to recent returns."
        )
    if history_source and str(history_source).endswith(".csv"):
        notes.append("DuckDB history unavailable; used local daily K CSV fallback.")

    return {
        "status": "ok",
        "ticker": key,
        "name": name,
        "sector": sector,
        "as_of": pd.to_datetime(last_date).date().isoformat(),
        "last_close": _safe_float(last_close, 2),
        "horizon_days": horizon,
        "forecast_model": "scenario_envelope_v1",
        "market_state": state,
        "risk_score": risk_score,
        "risk_label": _risk_label(risk_score),
        "expected_return_20d_pct": _safe_float(expected_return_pct, 2),
        "expected_return_source": expected_source,
        "recommendation": recommendation,
        "risk_tags": risk_tags,
        "history": history_rows,
        "forecast": forecast_rows,
        "risk_factors": risk_factors,
        "risk_metrics": risk_metrics,
        "advanced_signals": advanced_signals,
        "macro_events": macro_events,
        "etf_forecast_proxy": etf_proxy,
        "source": {
            "history": history_source,
            "prediction": prediction_source,
            "prediction_status": prediction_status,
        },
        "model": {
            "prediction_available": bool(prediction),
            "etf_proxy_available": bool(etf_proxy and etf_proxy.get("status") == "ok"),
            "prediction_date": _safe_text((prediction or {}).get("date")),
            "model_label": _safe_text((prediction or {}).get("model_label")),
            "model_slot": _safe_text((prediction or {}).get("model_slot")),
            "prob_edge_pct": _safe_float((_safe_float((prediction or {}).get("prob_edge")) or 0.0) * 100.0, 2)
            if prediction
            else None,
            "leaderboard_score": _safe_float((prediction or {}).get("leaderboard_score"), 4),
            "two_stage_rank": _safe_float((prediction or {}).get("two_stage_rank"), 0),
        },
        "notes": notes,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
