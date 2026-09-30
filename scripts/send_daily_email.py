"""Send the daily ML leaderboard and paper portfolio summary by email."""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
from email import policy
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR
from ml.cross_confirm import get_dual_confirmed_tickers
from ml.features.sector import SECTOR_MAPPING_PATH
from ml.predict import apply_sector_cap, _sort_prediction_df
from scripts.signal_explainability import explain_signal_row


def _load_name_lookup() -> dict[str, str]:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}
    _df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in _df.columns:
        return {}
    return dict(zip(_df["Ticker"], _df["Name"]))


_NAME_LOOKUP: dict[str, str] = _load_name_lookup()
from scripts.update_paper_portfolio import (
    DEFAULT_DB_PATH,
    connect_db,
    summarize_ledger,
)
from scripts.update_paper_portfolio_t1 import (
    DEFAULT_DB_PATH as T1_DB_PATH,
    connect_db as t1_connect_db,
    summarize_ledger as t1_summarize_ledger,
)

DEFAULT_EMAIL_TOP_N = 30
DEFAULT_PORTFOLIO_LIMIT = 12
DEFAULT_SUBJECT_PREFIX = "[Stock ML]"
DEFAULT_PREVIEW_PATH = os.path.join(BASE_DIR, "logs", "daily_email_preview.html")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
CHIPK_STATUS_REPORT_PATH = os.path.join(REPORT_DIR, "chipk_snapshot_status_latest.json")
SPECIAL_STATUS_REPORT_PATH = os.path.join(REPORT_DIR, "special_stock_status_latest.json")
DISPOSITION_ACTIVE_PATH = os.path.join(BASE_DIR, "disposition_active.csv")
UNIFIED_SIGNALS_LATEST_JSON_PATH = os.path.join(REPORT_DIR, "unified_signals_latest.json")
MOMENTUM_LATEST_SETUP_GLOB = os.path.join(
    REPORT_DIR, "momentum_continuation_backtest_*_latest_setups.csv"
)
PRODUCTION_PREDICTION_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_\d{4}-\d{2}-\d{2}\.csv$")
PLAIN_TEXT_FALLBACK = "請使用支援 HTML 的郵件客戶端查看每日 ML 預測與帳本觀察。"


@dataclass
class EmailSettings:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    to_emails: list[str]
    use_ssl: bool
    from_name: str = "Stock ML Bot"
    subject_prefix: str = DEFAULT_SUBJECT_PREFIX
    top_n: int = DEFAULT_EMAIL_TOP_N
    portfolio_limit: int = DEFAULT_PORTFOLIO_LIMIT


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            values[key] = value
    return values


def _env_value(key: str, env_map: dict[str, str], default: str = "") -> str:
    value = os.environ.get(key)
    if value is not None and value != "":
        return value
    return env_map.get(key, default)


def _as_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_email_settings() -> EmailSettings | None:
    env_map: dict[str, str] = {}
    for path in (os.path.join(BASE_DIR, ".env.email"), os.path.join(BASE_DIR, ".env")):
        env_map.update(_parse_env_file(path))

    from_email = _env_value("SMTP_FROM", env_map)
    to_value = _env_value("SMTP_TO", env_map)
    user = _env_value("SMTP_USER", env_map, from_email)
    password = _env_value("SMTP_PASSWORD", env_map)

    if not (from_email and to_value and user and password):
        return None

    host = _env_value("SMTP_HOST", env_map, "smtp.gmail.com")
    port = int(_env_value("SMTP_PORT", env_map, "465"))
    use_ssl = _as_bool(_env_value("SMTP_USE_SSL", env_map, "true"), default=True)
    from_name = _env_value("SMTP_FROM_NAME", env_map, "Stock ML Bot")
    subject_prefix = _env_value("EMAIL_SUBJECT_PREFIX", env_map, DEFAULT_SUBJECT_PREFIX)
    top_n = max(DEFAULT_EMAIL_TOP_N, int(_env_value("EMAIL_TOP_N", env_map, str(DEFAULT_EMAIL_TOP_N))))
    portfolio_limit = int(
        _env_value("EMAIL_PORTFOLIO_LIMIT", env_map, str(DEFAULT_PORTFOLIO_LIMIT))
    )

    to_emails = [item.strip() for item in to_value.split(",") if item.strip()]
    if not to_emails:
        return None

    return EmailSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        from_email=from_email,
        to_emails=to_emails,
        use_ssl=use_ssl,
        from_name=from_name,
        subject_prefix=subject_prefix,
        top_n=top_n,
        portfolio_limit=portfolio_limit,
    )


def _latest_prediction_path(prediction_file: str | None = None, as_of_date: str | None = None) -> str:
    if prediction_file:
        if os.path.isabs(prediction_file):
            return prediction_file
        return os.path.join(BASE_DIR, prediction_file)

    if as_of_date:
        exact = os.path.join(MODEL_DIR, f"predictions_{as_of_date}.csv")
        if not os.path.exists(exact):
            raise FileNotFoundError(f"No predictions file for {as_of_date}: {exact}")
        return exact

    candidates = sorted(
        f for f in glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv"))
        if PRODUCTION_PREDICTION_RE.match(os.path.basename(f))
    )
    if not candidates:
        raise FileNotFoundError("No predictions_YYYY-MM-DD.csv were found.")
    return candidates[-1]


def _latest_t1_prediction_path() -> str | None:
    candidates = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    return candidates[-1] if candidates else None


def _latest_unified_signal_path(as_of_date: str | None = None) -> str | None:
    if as_of_date:
        exact = os.path.join(MODEL_DIR, f"unified_signals_{as_of_date}.csv")
        return exact if os.path.exists(exact) else None
    candidates = sorted(
        path
        for path in glob.glob(os.path.join(MODEL_DIR, "unified_signals_*.csv"))
        if UNIFIED_SIGNAL_RE.match(os.path.basename(path))
    )
    return candidates[-1] if candidates else None


def _load_unified_signal_stats(as_of_date: str | None = None) -> dict[str, object]:
    signal_path = _latest_unified_signal_path(as_of_date)
    if signal_path is None:
        return {}

    df = pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})
    if df.empty:
        return {
            "prediction_date": None,
            "source_file": os.path.basename(signal_path),
            "total_candidates": 0,
            "total_units": 0,
            "signal_type_counts": {"Dual": 0, "20D_only": 0, "T1_only": 0},
        }

    signal_counts = {"Dual": 0, "20D_only": 0, "T1_only": 0}
    for signal_type, count in df["signal_type"].fillna("").astype(str).value_counts().items():
        signal_counts[signal_type] = int(count)

    total_units = int(pd.to_numeric(df.get("target_units"), errors="coerce").fillna(0).sum())
    prediction_date = (
        str(df["prediction_date"].iloc[0])
        if "prediction_date" in df.columns and not df.empty
        else None
    )
    return {
        "prediction_date": prediction_date,
        "source_file": os.path.basename(signal_path),
        "total_candidates": int(len(df)),
        "total_units": total_units,
        "signal_type_counts": signal_counts,
    }


def _load_json_object(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_chipk_status() -> dict[str, object]:
    return _load_json_object(CHIPK_STATUS_REPORT_PATH)


def _entry_special_status_gate() -> tuple[bool, str]:
    status = _load_json_object(SPECIAL_STATUS_REPORT_PATH)
    if not status:
        return False, "special stock status missing"
    today = datetime.now().strftime("%Y-%m-%d")
    effective_date = str(status.get("effective_date") or "")
    gate_action = str(status.get("gate_action") or "")
    missing_sources = status.get("missing_sources") or []
    is_ok = (
        str(status.get("status") or "").upper() == "OK"
        and effective_date == today
        and gate_action == "ALLOW_T1_AND_DUAL"
        and not missing_sources
    )
    if is_ok:
        return True, f"special status current: {effective_date}"
    reason = (
        f"special status not current: effective={effective_date or '-'} "
        f"today={today} gate={gate_action or '-'} missing={missing_sources or []}"
    )
    return False, reason


def _load_active_disposition_tickers(asof_date: str | None = None) -> set[str]:
    try:
        active = pd.read_csv(DISPOSITION_ACTIVE_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
    except (OSError, pd.errors.EmptyDataError, UnicodeDecodeError):
        return set()
    if active.empty or "stock_id" not in active.columns:
        return set()

    filtered = active
    if asof_date and {"period_start", "period_end"}.issubset(active.columns):
        asof = pd.to_datetime(asof_date, errors="coerce")
        starts = pd.to_datetime(active["period_start"], errors="coerce")
        ends = pd.to_datetime(active["period_end"], errors="coerce")
        if pd.notna(asof):
            filtered = active[(starts.isna() | (starts <= asof)) & (ends.isna() | (ends >= asof))]

    return {
        ticker
        for ticker in filtered["stock_id"].astype(str).str.strip().str.zfill(4)
        if ticker and ticker.lower() != "nan"
    }


def _entry_zone_status_label(value: object) -> str:
    text = str(value or "").strip()
    labels = {
        "in_entry_zone": "目前在買進區",
        "wait_pullback_to_zone": "等回落到買進區",
        "wait_reclaim_zone": "等重新站回買進區",
        "zone_unavailable": "買進區不足",
    }
    return labels.get(text, text or "-")


def _format_entry_zone(row: dict[str, object]) -> str:
    low = row.get("shortwave_entry_zone_low")
    high = row.get("shortwave_entry_zone_high")
    if pd.notna(low) and pd.notna(high):
        try:
            return f"{float(low):.2f} ~ {float(high):.2f}"
        except (TypeError, ValueError):
            return f"{low} ~ {high}"
    return "-"


ENTRY_TAG_LABELS = {
    "friend_pullback_support": "回落支撐",
    "volume_quiet": "量能冷卻",
    "macd_hist_positive": "MACD柱正",
    "macd_improving": "MACD改善",
    "friend_combo_primary": "短打組合完整",
    "friend_combo_developing": "短打組合成形中",
    "ml_buy_signal": "20D模型支持",
    "two_stage_supported": "排名支持",
    "alpha_supported": "勝率支持",
    "positive_prob_edge": "勝率優勢為正",
    "macro_ok": "宏觀允許",
}

MISSING_TAG_LABELS = {
    "missing_friend_pullback_support": "缺回落支撐確認",
    "missing_volume_quiet": "量能未降溫",
    "missing_macd_hist_positive": "MACD柱未轉正",
    "missing_macd_improving": "MACD尚未改善",
}


def _safe_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fmt_price(value: object, digits: int = 2) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric:.{digits}f}"


def _fmt_ratio_pct(value: object, digits: int = 2) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric * 100:+.{digits}f}%"


def _translated_tags(raw: object, mapping: dict[str, str], limit: int = 4) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() == "nan":
        return "-"
    tags = [item.strip() for item in re.split(r"[;,]", text) if item.strip()]
    labels = [mapping.get(tag, tag) for tag in tags[:limit]]
    if len(tags) > limit:
        labels.append(f"+{len(tags) - limit}")
    return "、".join(labels) if labels else "-"


def _recommendation_is_buy(value: object) -> bool:
    text = str(value or "")
    return "買" in text and "賣" not in text


def _recommendation_is_sell(value: object) -> bool:
    return "賣" in str(value or "")


def _latest_momentum_setup_path() -> str | None:
    candidates = [path for path in glob.glob(MOMENTUM_LATEST_SETUP_GLOB) if os.path.exists(path)]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _load_momentum_latest_setups(prediction_date: str) -> tuple[pd.DataFrame, str | None]:
    path = _latest_momentum_setup_path()
    if path is None:
        return pd.DataFrame(), None
    source_file = os.path.basename(path)
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    except (OSError, pd.errors.EmptyDataError, UnicodeDecodeError):
        return pd.DataFrame(), source_file
    if df.empty or "Date" not in df.columns:
        return pd.DataFrame(), source_file
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    same_day = df[df["Date"].dt.strftime("%Y-%m-%d").eq(str(prediction_date))].copy()
    return same_day.reset_index(drop=True), source_file


def _entry_candidate_label(action: object) -> str:
    text = str(action or "").strip()
    if text in {"NORMAL_ENTRY", "SMALL_ENTRY"}:
        return "可掛價"
    if text == "WAIT_NEW_STRATEGY":
        return "條件式低接"
    if text == "NO_CHASE":
        return "等回落不追"
    return "候選觀察"


def _build_entry_candidate_records_legacy(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    limit: int = 10,
) -> tuple[list[dict[str, object]], str | None, dict[str, int]]:
    special_ok, special_note = _entry_special_status_gate()
    if not special_ok:
        return [], None, {"setup_rows": 0, "excluded": 0, "special_gate_closed": 1, "note": special_note}
    latest, source_file = _load_momentum_latest_setups(prediction_date)
    stats = {"setup_rows": int(len(latest)), "excluded": 0, "special_gate_closed": 0, "note": special_note}
    if latest.empty or all_pred_df.empty or "ticker" not in all_pred_df.columns:
        return [], source_file, stats

    pred = all_pred_df.copy()
    pred["ticker"] = pred["ticker"].astype(str).str.strip().str.zfill(4)
    latest["ticker"] = latest["ticker"].astype(str).str.strip().str.zfill(4)
    pred_by_ticker = pred.drop_duplicates("ticker").set_index("ticker", drop=False)
    active_dispositions = _load_active_disposition_tickers(datetime.now().strftime("%Y-%m-%d"))
    stats["disposition_blocked"] = 0

    records: list[dict[str, object]] = []
    for _, setup in latest.iterrows():
        ticker = str(setup.get("ticker") or "").strip().zfill(4)
        if ticker not in pred_by_ticker.index:
            continue
        row = pred_by_ticker.loc[ticker]
        action = str(row.get("shortwave_action") or "").strip()
        recommendation = row.get("recommendation")
        pred_return = _safe_float(row.get("pred_return_20d"))
        special_blocked = any(
            _truthy(row.get(column))
            for column in (
                "is_disposition",
                "is_attention",
                "is_full_delivery",
                "is_suspended",
                "tradability_blocked",
                "penalty_hard_block",
                "guardrail_blocked",
            )
        )
        if ticker in active_dispositions:
            special_blocked = True
            stats["disposition_blocked"] += 1
        if (
            action in {"BLOCK", "EXIT_BIAS"}
            or special_blocked
            or _recommendation_is_sell(recommendation)
            or (pred_return is not None and pred_return < 0)
        ):
            stats["excluded"] += 1
            continue
        if not (
            action in {"NORMAL_ENTRY", "SMALL_ENTRY", "WAIT_NEW_STRATEGY", "NO_CHASE"}
            or _recommendation_is_buy(recommendation)
        ):
            stats["excluded"] += 1
            continue

        entry_low = _safe_float(setup.get("momentum_entry_low"))
        entry_high = _safe_float(setup.get("momentum_entry_high"))
        entry_limit = _safe_float(setup.get("momentum_entry_limit") or setup.get("Close"))
        if entry_low is None or entry_high is None:
            stats["excluded"] += 1
            continue

        score = _safe_float(setup.get("momentum_score")) or 0.0
        priority = {
            "NORMAL_ENTRY": 0,
            "SMALL_ENTRY": 1,
            "WAIT_NEW_STRATEGY": 2,
            "NO_CHASE": 3,
        }.get(action, 4)
        reason_bits = [
            f"20D {_fmt_ratio_pct(pred_return)}",
            f"動能分數 {score:.3f}",
            f"RSI {_fmt_price(setup.get('RSI_14'), 1)}",
            f"量比 {_fmt_price(setup.get('volume_ratio_20d'), 2)}",
            "MACD柱正" if (_safe_float(setup.get("MACDh_12_26_9")) or 0.0) > 0 else "MACD待確認",
        ]
        records.append(
            {
                "ticker": ticker,
                "name": setup.get("name") or _NAME_LOOKUP.get(ticker, ""),
                "sector": setup.get("sector") or row.get("sector") or "-",
                "label": _entry_candidate_label(action),
                "entry_zone": f"{entry_low:.2f} ~ {entry_high:.2f}",
                "entry_limit": entry_limit,
                "stop_loss": entry_low * 0.97,
                "resistance": entry_high,
                "reason": " / ".join(reason_bits),
                "support_tags": _translated_tags(row.get("shortwave_strategy_tags"), ENTRY_TAG_LABELS),
                "missing_tags": _translated_tags(
                    row.get("shortwave_missing_strategy_tags"),
                    MISSING_TAG_LABELS,
                ),
                "priority": priority,
                "score": score,
                "pred_return": pred_return or 0.0,
            }
        )

    records.sort(key=lambda item: (item["priority"], -float(item["score"]), -float(item["pred_return"])))
    return records[:limit], source_file, stats


def _build_entry_candidate_email_section_legacy(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    limit: int = 10,
) -> str:
    rows, source_file, stats = _build_entry_candidate_records(
        prediction_date,
        all_pred_df,
        limit=limit,
    )
    payload = _load_json_object(UNIFIED_SIGNALS_LATEST_JSON_PATH)
    selected_count = (
        len(payload.get("selected") or [])
        if str(payload.get("prediction_date") or "") == str(prediction_date)
        else 0
    )
    if stats.get("special_gate_closed"):
        source_note = "special status gate closed"
    else:
        source_note = source_file or "momentum setup 檔案未產生"
    source_note = html.escape(source_note)
    gate_note = html.escape(str(stats.get("note") or "-"))
    if not rows:
        body_rows = """
        <tr>
          <td colspan="5">今天沒有通過條件式入場表的候選。若盤中想買，只能等新的進場過濾器或手動補籌碼K確認。</td>
        </tr>
        """
    else:
        body_rows = "\n".join(
            f"""
            <tr>
              <td><strong>{html.escape(str(item["ticker"]))}</strong><br><span class="muted">{html.escape(str(item["name"] or ""))}</span></td>
              <td>{html.escape(str(item["label"]))}<br><span class="muted">{html.escape(str(item["sector"] or "-"))}</span></td>
              <td><strong>{html.escape(str(item["entry_zone"]))}</strong><br><span class="muted">掛價參考 {html.escape(_fmt_price(item["entry_limit"]))}</span></td>
              <td>停損 {html.escape(_fmt_price(item["stop_loss"]))}<br><span class="muted">上緣/壓力 {html.escape(_fmt_price(item["resistance"]))}</span></td>
              <td>{html.escape(str(item["reason"]))}<br><span class="muted">符合：{html.escape(str(item["support_tags"]))}<br>缺少：{html.escape(str(item["missing_tags"]))}</span></td>
            </tr>
            """
            for item in rows
        )

    return f"""
    <div class="section" id="sec-entry-candidates">
      <h2>可進場候選表</h2>
      <div class="muted">
        正式 selected：{selected_count} 檔。下表是「條件式可掛價」清單：只在價格落入入場區間時考慮，超過區間上緣不追；
        處置股、交易限制、BLOCK、EXIT_BIAS、20D賣出方向一律排除。
      </div>
      <div class="muted" style="margin-top:8px;">來源：{source_note} / setups {stats.get("setup_rows", 0)} / 排除 {stats.get("excluded", 0)} / special gate: {gate_note}</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>動作</th>
            <th>入場區間</th>
            <th>停損 / 壓力</th>
            <th>理由</th>
          </tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
</div>
"""


ENTRY_SUPPORT_ACTIONS = {"MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK", "MOMENTUM_WATCH"}


def _clean_entry_tags(raw: object, *, limit: int = 5) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() == "nan":
        return "-"
    mapping = {
        **ENTRY_TAG_LABELS,
        **MISSING_TAG_LABELS,
        "watch_only": "觀察優先",
        "wait_for_full_setup": "等待完整條件",
    }
    tags = [item.strip() for item in re.split(r"[;,]", text) if item.strip()]
    labels = [mapping.get(tag, tag.replace("_", " ")) for tag in tags[:limit]]
    if len(tags) > limit:
        labels.append(f"+{len(tags) - limit}")
    return "、".join(labels) if labels else "-"


def _clean_entry_candidate_label(action: object, *, formal: bool) -> str:
    text = str(action or "").strip()
    if formal and text in {"NORMAL_ENTRY", "SMALL_ENTRY"}:
        return "動能研究條件成立"
    if text == "MOMENTUM_LIMIT_BUY":
        return "支撐區觀察"
    if text == "MOMENTUM_WAIT_PULLBACK":
        return "等回落觀察"
    if text == "MOMENTUM_WATCH":
        return "條件式觀察"
    if text == "WAIT_NEW_STRATEGY":
        return "等待新策略確認"
    if text == "NO_CHASE":
        return "不追價"
    return "觀察候選"


def _daily_k_dir_path() -> str | None:
    candidates = [
        os.path.join(BASE_DIR, "\u65e5K\u8cc7\u6599"),
        os.path.join(BASE_DIR, "daily_k"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    for name in os.listdir(BASE_DIR):
        path = os.path.join(BASE_DIR, name)
        if os.path.isdir(path) and name.encode("unicode_escape").decode("ascii") == r"\u65e5K\u8cc7\u6599":
            return path
    return None


def _load_daily_ohlc(ticker: str, date_value: object) -> dict[str, float] | None:
    daily_dir = _daily_k_dir_path()
    if not daily_dir:
        return None
    path = os.path.join(daily_dir, f"{str(ticker).zfill(4)}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype={"Date": str},
            usecols=lambda column: column in {"Date", "Open", "High", "Low", "Close"},
        )
    except Exception:
        return None
    if df.empty or "Date" not in df.columns:
        return None
    target = pd.to_datetime(str(date_value), errors="coerce")
    if pd.isna(target):
        return None
    dates = pd.to_datetime(df["Date"], errors="coerce")
    match = df.loc[dates.dt.strftime("%Y-%m-%d").eq(target.strftime("%Y-%m-%d"))].copy()
    if match.empty:
        return None
    row = match.iloc[-1]
    values = {key: _safe_float(row.get(key)) for key in ("Open", "High", "Low", "Close")}
    if any(value is None for value in values.values()):
        return None
    return {key.lower(): float(value) for key, value in values.items() if value is not None}


def _format_ohlc(ohlc: dict[str, float] | None) -> str:
    if not ohlc:
        return "-"
    return " / ".join(
        _fmt_price(ohlc.get(key), 2)
        for key in ("open", "high", "low", "close")
    )


def _entry_support_review(
    *,
    entry_low: float,
    entry_high: float,
    stop_loss: float,
    ohlc: dict[str, float] | None,
    special_blocked: bool,
    formal_ok: bool,
) -> tuple[str, str, str]:
    if ohlc is None:
        return "-", "-", "日K資料不足，僅能列為觀察，不做進場判斷。"

    high = ohlc["high"]
    low = ohlc["low"]
    close = ohlc["close"]
    touched = low <= entry_high and high >= entry_low
    if not touched:
        return "否", "未測試", "尚未落入進場區間，等回落到區間內再評估，不追價。"

    if special_blocked:
        return "有", "否", "排除。特殊狀態或處置股風險未解除，即使進入區間也不進場。"

    if close < entry_low:
        return "有", "否", "排除。收盤跌破區間下緣，支撐沒有守住，明天需重新站回區間才可再看。"

    if low <= stop_loss:
        return "有", "不乾淨", "盤中跌破停損參考後收回，支撐不乾淨；明天不追，需重新站穩區間且不能再破今日低點附近。"

    if low < entry_low:
        return "有", "有守停損但有下洗", "當日價格曾落入區間且收盤回到區間內；低點未破失效參考價，但日K不能證明盤中守穩，下次觸價仍待確認。"

    if close > entry_high:
        return "有", "有", "當日低點未破失效參考價，收盤高於區間上緣；不追高，等回落後再確認盤中支撐。"

    if formal_ok:
        return "有", "有", "收盤仍在區間內且當日低點未破失效參考價，符合動能研究條件；僅為日K回顧，盤中仍待確認。"
    return "有", "有", "收盤仍在區間內且當日低點未破失效參考價，屬觀察候選；盤中守穩、人工分點與主力成本仍待確認。"


def _build_entry_candidate_records(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    limit: int = 10,
) -> tuple[list[dict[str, object]], str | None, dict[str, int | str]]:
    """Build momentum-research rows plus support-zone observations for the email.

    The internal formal_ok predicate belongs to the momentum research table,
    not production selected membership. Labels must preserve that distinction.
    """

    special_ok, special_note = _entry_special_status_gate()
    if not special_ok:
        return [], None, {"setup_rows": 0, "excluded": 0, "special_gate_closed": 1, "note": special_note}

    latest, source_file = _load_momentum_latest_setups(prediction_date)
    stats: dict[str, int | str] = {
        "setup_rows": int(len(latest)),
        "excluded": 0,
        "not_in_prediction": 0,
        "disposition_blocked": 0,
        "formal_rows": 0,
        "observation_rows": 0,
        "blocked_rows": 0,
        "special_gate_closed": 0,
        "note": special_note,
    }
    if latest.empty or all_pred_df.empty or "ticker" not in all_pred_df.columns:
        return [], source_file, stats

    pred = all_pred_df.copy()
    pred["ticker"] = pred["ticker"].astype(str).str.strip().str.zfill(4)
    latest["ticker"] = latest["ticker"].astype(str).str.strip().str.zfill(4)
    pred_by_ticker = pred.drop_duplicates("ticker").set_index("ticker", drop=False)
    active_dispositions = _load_active_disposition_tickers(datetime.now().strftime("%Y-%m-%d"))

    records: list[dict[str, object]] = []
    for _, setup in latest.iterrows():
        ticker = str(setup.get("ticker") or "").strip().zfill(4)
        if ticker not in pred_by_ticker.index:
            stats["not_in_prediction"] = int(stats["not_in_prediction"]) + 1
            continue

        row = pred_by_ticker.loc[ticker]
        shortwave_action = str(row.get("shortwave_action") or "").strip()
        momentum_action = str(row.get("momentum_action") or "").strip()
        pred_return = _safe_float(row.get("pred_return_20d"))
        special_blocked = any(
            _truthy(row.get(column))
            for column in (
                "is_disposition",
                "is_attention",
                "is_full_delivery",
                "is_suspended",
                "tradability_blocked",
                "penalty_hard_block",
                "guardrail_blocked",
            )
        )
        if ticker in active_dispositions:
            special_blocked = True
            stats["disposition_blocked"] = int(stats["disposition_blocked"]) + 1
        if shortwave_action == "BLOCK":
            stats["excluded"] = int(stats["excluded"]) + 1
            continue

        entry_low = _safe_float(setup.get("momentum_entry_low")) or _safe_float(row.get("momentum_entry_zone_low"))
        entry_high = _safe_float(setup.get("momentum_entry_high")) or _safe_float(row.get("momentum_entry_zone_high"))
        entry_limit = _safe_float(setup.get("momentum_entry_limit")) or _safe_float(row.get("momentum_entry_limit")) or _safe_float(setup.get("Close"))
        if entry_low is None or entry_high is None:
            stats["excluded"] = int(stats["excluded"]) + 1
            continue

        formal_ok = (
            shortwave_action in {"NORMAL_ENTRY", "SMALL_ENTRY", "WAIT_NEW_STRATEGY", "NO_CHASE"}
            and (pred_return is None or pred_return >= 0)
            and not special_blocked
        )
        observation_ok = momentum_action in ENTRY_SUPPORT_ACTIONS
        if not formal_ok and not observation_ok:
            stats["excluded"] = int(stats["excluded"]) + 1
            continue

        score = _safe_float(setup.get("momentum_score")) or _safe_float(row.get("momentum_score")) or 0.0
        raw_stop_loss = _safe_float(row.get("momentum_stop_loss"))
        stop_loss = raw_stop_loss if raw_stop_loss is not None and raw_stop_loss < entry_low else entry_low * 0.97
        label_action = shortwave_action if formal_ok else momentum_action
        if special_blocked:
            scope = "排除"
            stats["blocked_rows"] = int(stats["blocked_rows"]) + 1
            priority = 9
        elif formal_ok:
            scope = "研究條件"
            stats["formal_rows"] = int(stats["formal_rows"]) + 1
            priority = {"NORMAL_ENTRY": 0, "SMALL_ENTRY": 1, "WAIT_NEW_STRATEGY": 2, "NO_CHASE": 3}.get(shortwave_action, 4)
        else:
            scope = "觀察"
            stats["observation_rows"] = int(stats["observation_rows"]) + 1
            priority = {"MOMENTUM_LIMIT_BUY": 4, "MOMENTUM_WAIT_PULLBACK": 5, "MOMENTUM_WATCH": 6}.get(momentum_action, 7)

        notes = []
        if special_blocked:
            notes.append("特殊狀態排除")
        if not formal_ok:
            notes.append("非正式買進")
        if shortwave_action in {"EXIT_BIAS", "BLOCK"}:
            notes.append(f"短波段={shortwave_action}")
        if pred_return is not None and pred_return < 0:
            notes.append("20D 模型偏弱")

        macd_hist = _safe_float(setup.get("MACDh_12_26_9"))
        reason_bits = [
            f"20D {_fmt_ratio_pct(pred_return)}",
            f"動能 {score:.3f}",
            f"RSI {_fmt_price(setup.get('RSI_14'), 1)}",
            f"量比 {_fmt_price(setup.get('volume_ratio_20d'), 2)}",
            "MACD 偏多" if (macd_hist or 0.0) > 0 else "MACD 未確認",
        ]
        if notes:
            reason_bits.append("；".join(notes))
        ohlc = _load_daily_ohlc(ticker, setup.get("Date") or prediction_date)
        zone_touched, support_hold, conclusion = _entry_support_review(
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            ohlc=ohlc,
            special_blocked=special_blocked,
            formal_ok=formal_ok,
        )

        records.append(
            {
                "ticker": ticker,
                "name": setup.get("name") or _NAME_LOOKUP.get(ticker, ""),
                "sector": setup.get("sector") or row.get("sector") or "-",
                "scope": scope,
                "source_date": str(setup.get("Date") or prediction_date)[:10],
                "label": _clean_entry_candidate_label(label_action, formal=formal_ok),
                "entry_zone": f"{entry_low:.2f} ~ {entry_high:.2f}",
                "entry_limit": entry_limit,
                "stop_loss": stop_loss,
                "resistance": entry_high,
                "ohlc_text": _format_ohlc(ohlc),
                "zone_touched": zone_touched,
                "support_hold": support_hold,
                "conclusion": conclusion,
                "reason": " / ".join(reason_bits),
                "support_tags": _clean_entry_tags(row.get("momentum_reason") or row.get("shortwave_strategy_tags")),
                "missing_tags": _clean_entry_tags(row.get("momentum_missing_strategy_tags") or row.get("shortwave_missing_strategy_tags")),
                "priority": priority,
                "score": score,
                "pred_return": pred_return or 0.0,
            }
        )

    records.sort(key=lambda item: (item["priority"], -float(item["score"]), -float(item["pred_return"])))
    return records[:limit], source_file, stats


def _build_entry_candidate_email_section(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    limit: int = 10,
) -> str:
    rows, source_file, stats = _build_entry_candidate_records(prediction_date, all_pred_df, limit=limit)
    payload = _load_json_object(UNIFIED_SIGNALS_LATEST_JSON_PATH)
    selected_count = (
        len(payload.get("selected") or [])
        if str(payload.get("prediction_date") or "") == str(prediction_date)
        else 0
    )
    source_note = "special status gate closed" if stats.get("special_gate_closed") else (source_file or "momentum setup not found")
    gate_note = html.escape(str(stats.get("note") or "-"))

    if not rows:
        body_rows = """
        <tr>
          <td colspan="6">目前沒有動能研究或支撐區觀察候選。production 名單請另看「今日 production 訊號」；研究型態入列不代表正式放行。</td>
        </tr>
        """
    else:
        body_rows = "\n".join(
            f"""
            <tr class="entry-main">
              <td data-label="股票"><span class="entry-field-label">股票</span><strong>{html.escape(str(item["ticker"]))}</strong><br><span class="muted">{html.escape(str(item["name"] or ""))}</span></td>
              <td data-label="策略動作"><span class="entry-field-label">策略動作</span>{html.escape(str(item["scope"]))} / {html.escape(str(item["label"]))}<br><span class="muted">{html.escape(str(item["sector"] or "-"))}</span></td>
              <td data-label="進場區間"><span class="entry-field-label">進場區間</span><strong>{html.escape(str(item["entry_zone"]))}</strong><br><span class="muted">掛價參考 {html.escape(_fmt_price(item["entry_limit"]))}</span></td>
              <td data-label="O/H/L/C"><span class="entry-field-label">O/H/L/C</span>{html.escape(str(item["ohlc_text"]))}<br><span class="muted">日K資料日：{html.escape(str(item["source_date"]))}（盤後）</span></td>
              <td data-label="落入區間?"><span class="entry-field-label">落入區間?</span>{html.escape(str(item["zone_touched"]))}</td>
              <td data-label="日K支撐回顧"><span class="entry-field-label">日K支撐回顧</span>{html.escape(str(item["support_hold"]))}<br><span class="muted">停損 {html.escape(_fmt_price(item["stop_loss"]))}</span></td>
            </tr>
            <tr class="entry-detail">
              <td colspan="6"><strong>結論：{html.escape(str(item["conclusion"]))}</strong><br><span class="muted">{html.escape(str(item["reason"]))}<br>符合：{html.escape(str(item["support_tags"]))}<br>缺少：{html.escape(str(item["missing_tags"]))}</span></td>
            </tr>
            """
            for item in rows
        )

    return f"""
    <div class="section" id="sec-entry-candidates">
      <h2>進場區間候選表 · 動能研究</h2>
      <div class="muted">
        Production 已選：{selected_count} 檔；這張動能研究表與 production 名單、rere／主策略 canonical 觀察名單分開計算。
        「研究條件」僅表示符合本表動能條件，不代表 production 放行；「觀察」表示支撐/壓力區間可盯盤。
        區間碰觸與支撐欄是所標日期的日K回顧，不能證明盤中守穩或已成交。
        若列出「20D 模型偏弱」或「短波段=EXIT_BIAS」，只能當盤中條件觀察，不可直接追價。
      </div>
      <div class="muted" style="margin-top:8px;">
        預測資料日：{html.escape(str(prediction_date))} / 來源：{html.escape(str(source_note))} / setups {stats.get("setup_rows", 0)} / 研究條件 {stats.get("formal_rows", 0)} / 觀察 {stats.get("observation_rows", 0)} / 排除列出 {stats.get("blocked_rows", 0)} / 其他排除 {stats.get("excluded", 0)} / special gate: {gate_note}
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>策略動作</th>
            <th>進場區間</th>
            <th>O/H/L/C</th>
            <th>落入區間?</th>
            <th>日K支撐回顧</th>
          </tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>
""".strip()


def _chipk_mobile_targets(
    prediction_date: str,
    leaderboard_df: pd.DataFrame,
    limit: int = 3,
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    seen: set[str] = set()
    payload = _load_json_object(UNIFIED_SIGNALS_LATEST_JSON_PATH)
    payload_date = str(payload.get("prediction_date") or "")

    if payload_date == str(prediction_date):
        for row in payload.get("shortwave_candidates") or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            targets.append(
                {
                    "ticker": ticker,
                    "name": row.get("name") or row.get("stock_name") or _NAME_LOOKUP.get(ticker, ""),
                    "sector": row.get("sector") or "-",
                    "entry_zone": _format_entry_zone(row),
                    "status": _entry_zone_status_label(row.get("shortwave_entry_zone_status")),
                    "source": "進場過濾候選",
                }
            )
            if len(targets) >= limit:
                return targets

    for _, row in leaderboard_df.head(limit * 2).iterrows():
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        targets.append(
            {
                "ticker": ticker,
                "name": _NAME_LOOKUP.get(ticker, ""),
                "sector": row.get("sector") or "-",
                "entry_zone": "-",
                "status": "模型榜候選，買前再複核",
                "source": "模型候選",
            }
        )
        if len(targets) >= limit:
            break
    return targets


def _build_chipk_mobile_review_section(
    prediction_date: str,
    leaderboard_df: pd.DataFrame,
) -> str:
    targets = _chipk_mobile_targets(prediction_date, leaderboard_df, limit=3)
    status = _load_chipk_status()
    asof = html.escape(str(status.get("asof_date") or "-"))
    status_text = html.escape(str(status.get("status") or "unknown"))
    user_action = str(status.get("user_action") or "").strip()
    if str(status.get("status") or "").strip().lower() == "retired":
        status_note = "桌面自動來源已退役，不作選股判斷；分點、主力成本、散戶/大戶請用手機 App 人工截圖確認。"
    else:
        status_note = (
            html.escape(user_action)
            if user_action
            else "桌面快照的日期與狀態仍需確認；手機 App 用來補分點、主力成本、散戶/大戶細節。"
        )
    target_rows = "\n".join(
        f"""
        <tr>
          <td><strong>{html.escape(str(item.get("ticker") or "-"))}</strong><br><span class="muted">{html.escape(str(item.get("name") or ""))}</span></td>
          <td>{html.escape(str(item.get("sector") or "-"))}</td>
          <td>{html.escape(str(item.get("entry_zone") or "-"))}<br><span class="muted">{html.escape(str(item.get("status") or "-"))}</span></td>
          <td>{html.escape(str(item.get("source") or "-"))}</td>
        </tr>
        """
        for item in targets
    )
    if not target_rows:
        target_rows = """
        <tr>
          <td colspan="4">今日沒有明確手機複核名單；若你有想買的個股，仍照下方 checklist 查。</td>
        </tr>
        """

    return f"""
    <div class="section">
      <h2>籌碼K手機 App 複核提醒</h2>
      <div class="muted">
        每天買進前，只查你真的想買的 1 到 3 檔。K 線、量、MACD 本地系統已有；手機 App 重點補「分點、主力成本、散戶/大戶」。
      </div>
      <div class="muted" style="margin-top:8px;">桌面快照 as-of: {asof} / status: {status_text}<br>{status_note}</div>
      <table class="data-table">
        <thead>
          <tr><th>股票</th><th>產業</th><th>買進區 / 狀態</th><th>來源</th></tr>
        </thead>
        <tbody>{target_rows}</tbody>
      </table>
      <div class="muted" style="margin-top:12px;">
        手機籌碼K固定查四項：1. 主力動向 1D / 5D / 20D；
        2. 分點明細 1D / 5D / 20D 的前 10/15 券商、買賣超、均價；
        3. 主力成本是否貼近目前價格；
        4. 散戶/大戶變化，確認是不是主力吃貨、散戶退場，而不是隔日沖誘多。
        查完後把截圖貼回來，我再補進場判斷。
      </div>
    </div>
"""


def _load_t1_leaderboard(top_n: int = 20) -> tuple[str, pd.DataFrame]:
    """載入最新 T+1 預測 leaderboard，回傳 (prediction_date, df)."""
    path = _latest_t1_prediction_path()
    if path is None:
        return "", pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return "", pd.DataFrame()
    prediction_date = str(df["date"].iloc[0]) if "date" in df.columns else ""
    selected = df[df.get("selected_for_trade", pd.Series(dtype=bool)) == True].copy()
    if selected.empty:
        selected = df.head(top_n).copy()
    else:
        selected = selected.head(top_n)
    return prediction_date, selected.reset_index(drop=True)


def _load_leaderboard(prediction_path: str, top_n: int) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    pred_df = pd.read_csv(prediction_path)
    pred_df = _sort_prediction_df(pred_df)

    if {"pred_return_20d", "recommendation"} <= set(pred_df.columns):
        leaderboard = apply_sector_cap(pred_df, top_n=top_n).reset_index(drop=True)
    else:
        leaderboard = pred_df.head(top_n).copy().reset_index(drop=True)

    leaderboard["selection_rank"] = range(1, len(leaderboard) + 1)
    prediction_date = str(pred_df["date"].iloc[0]) if "date" in pred_df.columns else ""
    return prediction_date, pred_df, leaderboard


def _load_portfolio_snapshot(
    db_path: str = DEFAULT_DB_PATH,
    limit: int | None = None,
) -> tuple[dict[str, object], pd.DataFrame]:
    if not os.path.exists(db_path):
        return {}, pd.DataFrame()

    with connect_db(db_path) as conn:
        summary = summarize_ledger(conn)
        query = """
            WITH latest_marks AS (
                SELECT position_id, mark_date, close, close_return_pct
                FROM (
                    SELECT
                        position_id,
                        mark_date,
                        close,
                        close_return_pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY position_id
                            ORDER BY mark_date DESC
                        ) AS rn
                    FROM portfolio_marks
                ) ranked
                WHERE rn = 1
            )
            SELECT
                p.prediction_date,
                p.selection_rank,
                p.ticker,
                p.recommendation,
                p.status,
                ROUND(p.entry_open, 2) AS entry_open,
                ROUND(lm.close, 2) AS latest_close,
                ROUND(lm.close_return_pct * 100, 2) AS unrealized_pct,
                ROUND(p.max_drawdown_pct * 100, 2) AS max_drawdown_pct,
                lm.mark_date AS latest_mark_date
            FROM portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.prediction_date >= ?
            ORDER BY
                CASE p.status
                    WHEN 'open' THEN 0
                    WHEN 'pending' THEN 1
                    ELSE 2
                END,
                COALESCE(lm.close_return_pct, -999) DESC,
                p.prediction_date DESC,
                p.selection_rank ASC
            """
        params: tuple[object, ...] = ("2026-03-18",)
        if limit is not None and int(limit) > 0:
            query += "\n            LIMIT ?"
            params = ("2026-03-18", int(limit))

        detail_df = pd.read_sql_query(query, conn, params=params)
    return summary, detail_df


def _load_t1_portfolio_snapshot(
    db_path: str = T1_DB_PATH,
) -> tuple[dict[str, object], pd.DataFrame]:
    if not os.path.exists(db_path):
        return {}, pd.DataFrame()

    with t1_connect_db(db_path) as conn:
        summary = t1_summarize_ledger(conn)
        detail_df = pd.read_sql_query(
            """
            SELECT
                prediction_date,
                selection_rank,
                ticker,
                sector,
                recommendation,
                setup_tags,
                status,
                ROUND(selection_close, 2) AS selection_close,
                ROUND(entry_open, 2) AS entry_open,
                ROUND(exit_close, 2) AS exit_close,
                ROUND(realized_return_pct * 100, 2) AS return_pct,
                ROUND(max_intraday_gain_pct * 100, 2) AS max_gain_pct,
                ROUND(max_intraday_drawdown_pct * 100, 2) AS max_dd_pct,
                hit_3pct
            FROM t1_positions
            WHERE prediction_date >= ?
            ORDER BY prediction_date DESC, selection_rank ASC
            """,
            conn,
            params=("2026-03-23",),
        )
    return summary, detail_df


def _fmt_pct(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.{digits}f}%"


def _fmt_num(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _html_cell(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return html.escape(str(value))


def _format_risk_html(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"

    parts = [part.strip() for part in re.split(r"\s+(?=[⚠💡])", text) if part.strip()]
    if not parts:
        return html.escape(text)
    return "<br>".join(html.escape(part) for part in parts)


def _signed_value_class(value: object) -> str:
    if value is None or pd.isna(value):
        return "value-flat"
    numeric = float(value)
    if numeric > 0:
        return "value-up"
    if numeric < 0:
        return "value-down"
    return "value-flat"


def _badge_class(value: object, *, kind: str) -> str:
    text = str(value or "")
    if kind == "recommendation":
        if text == "強力買進":
            return "badge badge-strong-buy"
        if text == "建議買進":
            return "badge badge-buy"
        if text.startswith("觀望"):
            return "badge badge-watch"
        if "賣出" in text:
            return "badge badge-sell"
        return "badge badge-neutral"

    if kind == "status":
        if text == "open":
            return "badge badge-open"
        if text == "pending":
            return "badge badge-pending"
        if text == "closed":
            return "badge badge-closed"
    return "badge badge-neutral"


def _render_leaderboard_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        rank = int(row["selection_rank"])
        ticker = _html_cell(row.get("ticker"))
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
        sector = _html_cell(row.get("sector") or "-")
        close = _fmt_num(row.get("close"), 1)
        pred_return = _fmt_pct(
            float(row.get("pred_return_20d")) * 100 if pd.notna(row.get("pred_return_20d")) else None
        )
        recommendation = _html_cell(row.get("recommendation"))
        risk_html = _format_risk_html(row.get("risk_tags"))
        explain = explain_signal_row(row.to_dict(), source="prediction")
        explain_action = html.escape(str(explain["action_label"]))
        explain_summary = html.escape(str(explain["summary"]))
        explain_drivers = html.escape(" / ".join(explain["drivers"]))
        explain_warnings = html.escape(" / ".join(explain["warnings"]))
        shortwave_action = _html_cell(row.get("shortwave_action") or "-")
        shortwave_tags = _format_risk_html(row.get("shortwave_strategy_tags"))
        shortwave_missing = _format_risk_html(row.get("shortwave_missing_strategy_tags"))
        shortwave_advice = _format_risk_html(row.get("shortwave_entry_strategy_advice"))
        shortwave_note = _format_risk_html(row.get("shortwave_watch_note"))
        hold_days_raw = row.get("shortwave_preferred_hold_days")
        hold_days = "-"
        if pd.notna(hold_days_raw):
            try:
                hold_days = f"{int(float(hold_days_raw))}D"
            except (TypeError, ValueError):
                hold_days = html.escape(str(hold_days_raw))
        combo_score_raw = row.get("shortwave_friend_combo_score")
        combo_score = "-"
        if pd.notna(combo_score_raw):
            try:
                combo_score = _fmt_pct(float(combo_score_raw) * 100, 1)
            except (TypeError, ValueError):
                combo_score = html.escape(str(combo_score_raw))
        zone_low_raw = row.get("shortwave_entry_zone_low")
        zone_high_raw = row.get("shortwave_entry_zone_high")
        entry_zone = "-"
        if pd.notna(zone_low_raw) and pd.notna(zone_high_raw):
            try:
                entry_zone = f"{float(zone_low_raw):.2f} ~ {float(zone_high_raw):.2f}"
            except (TypeError, ValueError):
                entry_zone = f"{html.escape(str(zone_low_raw))} ~ {html.escape(str(zone_high_raw))}"
        zone_status = _html_cell(row.get("shortwave_entry_zone_status") or "-")
        zone_note = _format_risk_html(row.get("shortwave_entry_zone_note"))
        model_support = _html_cell(row.get("shortwave_model_combo_support") or "-")
        shortwave_strategy_html = (
            f"{shortwave_action} / combo {combo_score} / hold {hold_days} / model support {model_support}"
            f"<br><strong>Entry Zone:</strong> {entry_zone} / {zone_status}"
            f"<br><strong>Met:</strong> {shortwave_tags}"
            f"<br><strong>Missing:</strong> {shortwave_missing}"
            f"<br><strong>Advice:</strong> {shortwave_advice}"
            f"<br><strong>Zone Note:</strong> {zone_note}"
            f"<br>{shortwave_note}"
        )
        rows.append(
            """
            <tr class="main-row">
              <td class="col-rank">#{rank}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}{name_html}</div>
                <div class="cell-sub">{sector} / 收盤 {close}</div>
                <div class="cell-badges">
                  <span class="{rec_class}">{recommendation}</span>
                  <span class="badge badge-neutral">{explain_action}</span>
                </div>
              </td>
              <td class="col-return">{pred_return}</td>
            </tr>
            <tr class="detail-row">
              <td class="detail-spacer"></td>
              <td colspan="2" class="detail-cell">
                <span class="detail-label">風險標籤</span>
                <div class="risk-text">{risk_html}</div>
                <span class="detail-label">New Entry Strategy</span>
                <div class="risk-text">{shortwave_strategy_html}</div>
                <span class="detail-label">Explain</span>
                <div class="risk-text">{explain_summary}</div>
                <div class="risk-text"><strong>Drivers:</strong> {explain_drivers}</div>
                <div class="risk-text"><strong>Warnings:</strong> {explain_warnings}</div>
              </td>
            </tr>
            """.format(
                rank=rank,
                ticker=ticker,
                name_html=name_html,
                sector=sector,
                close=close,
                pred_return=pred_return,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                risk_html=risk_html,
                shortwave_strategy_html=shortwave_strategy_html,
                explain_action=explain_action,
                explain_summary=explain_summary,
                explain_drivers=explain_drivers,
                explain_warnings=explain_warnings,
            )
        )
    return "\n".join(rows)


def _render_t1_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        rank = int(row.get("selection_rank", 0)) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
        sector = _html_cell(row.get("sector") or "-")
        close = _fmt_num(row.get("close"), 1)
        hit_prob = _fmt_pct(float(row.get("hit_prob_3pct", 0)) * 100) if pd.notna(row.get("hit_prob_3pct")) else "-"
        t1_score = f"{float(row.get('t1_score', 0)):.3f}" if pd.notna(row.get("t1_score")) else "-"
        recommendation = _html_cell(row.get("recommendation"))
        setup = _html_cell(row.get("setup_tags") or "-")
        risk_html = _format_risk_html(row.get("risk_tags"))
        tp = _fmt_pct(float(row.get("take_profit", 0)) * 100) if pd.notna(row.get("take_profit")) else "-"
        sl = _fmt_pct(float(row.get("stop_loss", 0)) * -100) if pd.notna(row.get("stop_loss")) else "-"
        rows.append(
            """
            <tr>
              <td class="col-rank">#{rank}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}{name_html}</div>
                <div class="cell-sub">{sector} / 收盤 {close}</div>
                <div class="cell-badges">
                  <span class="{rec_class}">{recommendation}</span>
                </div>
                <div class="cell-sub" style="margin-top:4px;">型態：{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title">{hit_prob}</div>
                <div class="cell-sub">T1分數 {t1_score}</div>
                <div class="cell-sub">停利 {tp} / 停損 {sl}</div>
              </td>
            </tr>
            """.format(
                rank=rank,
                ticker=ticker,
                name_html=name_html,
                sector=sector,
                close=close,
                hit_prob=hit_prob,
                t1_score=t1_score,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                setup=setup,
                risk_html=risk_html,
                tp=tp,
                sl=sl,
            )
        )
    return "\n".join(rows)


def _render_portfolio_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        prediction_date = _html_cell(row.get("prediction_date"))
        rank = int(row.get("selection_rank")) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        status = _html_cell(row.get("status"))
        recommendation = _html_cell(row.get("recommendation"))
        entry_open = _fmt_num(row.get("entry_open"), 2)
        latest_close = _fmt_num(row.get("latest_close"), 2)
        unrealized_pct = _fmt_pct(row.get("unrealized_pct"))
        max_drawdown_pct = _fmt_pct(row.get("max_drawdown_pct"))
        unrealized_class = _signed_value_class(row.get("unrealized_pct"))
        rows.append(
            """
            <tr>
              <td class="col-date">
                <div class="cell-title">{prediction_date}</div>
                <div class="cell-sub">#{rank} / {ticker}{name_suffix}</div>
              </td>
              <td class="col-target">
                <div class="cell-badges">
                  <span class="{status_class}">{status}</span>
                  <span class="{rec_class} portfolio-rec">{recommendation}</span>
                </div>
                <div class="cell-sub">成本 {entry_open} → 最新 {latest_close}</div>
              </td>
              <td class="col-return">
                <div class="cell-title {unrealized_class}">{unrealized_pct}</div>
                <div class="cell-sub">回撤 {max_drawdown_pct}</div>
              </td>
            </tr>
            """.format(
                prediction_date=prediction_date,
                rank=rank,
                status_class=_badge_class(row.get("status"), kind="status"),
                status=status,
                rec_class=_badge_class(row.get("recommendation"), kind="recommendation"),
                recommendation=recommendation,
                entry_open=entry_open,
                latest_close=latest_close,
                unrealized_pct=unrealized_pct,
                max_drawdown_pct=max_drawdown_pct,
                unrealized_class=unrealized_class,
                ticker=ticker,
                name_suffix=f" {html.escape(name)}" if name else "",
            )
        )
    return "\n".join(rows)


def _render_t1_portfolio_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        pred_date = _html_cell(row.get("prediction_date"))
        rank = int(row.get("selection_rank")) if pd.notna(row.get("selection_rank")) else "-"
        ticker = _html_cell(row.get("ticker"))
        name = _NAME_LOOKUP.get(str(row.get("ticker", "")), "")
        status = _html_cell(row.get("status"))
        setup = _html_cell(row.get("setup_tags") or "-")
        entry = _fmt_num(row.get("entry_open"), 2)
        exit_c = _fmt_num(row.get("exit_close"), 2)
        ret = _fmt_pct(row.get("return_pct"))
        max_gain = _fmt_pct(row.get("max_gain_pct"))
        max_dd = _fmt_pct(row.get("max_dd_pct"))
        hit = row.get("hit_3pct")
        hit_str = "O" if hit == 1 else ("X" if hit == 0 else "-")
        hit_class = "value-up" if hit == 1 else ("value-down" if hit == 0 else "value-flat")
        ret_class = _signed_value_class(row.get("return_pct"))
        rows.append(
            """
            <tr>
              <td class="col-date">
                <div class="cell-title">{pred_date}</div>
                <div class="cell-sub">#{rank} / {ticker}{name_suffix}</div>
              </td>
              <td class="col-target">
                <div class="cell-badges">
                  <span class="{status_class}">{status}</span>
                </div>
                <div class="cell-sub">開 {entry} → 收 {exit_c}</div>
                <div class="cell-sub" style="margin-top:2px;">{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title {ret_class}">{ret}</div>
                <div class="cell-sub">高 {max_gain} / 低 {max_dd}</div>
                <div class="cell-sub {hit_class}">命中 {hit_str}</div>
              </td>
            </tr>
            """.format(
                pred_date=pred_date,
                rank=rank,
                ticker=ticker,
                status_class=_badge_class(row.get("status"), kind="status"),
                status=status,
                entry=entry,
                exit_c=exit_c,
                ret=ret,
                max_gain=max_gain,
                max_dd=max_dd,
                hit_str=hit_str,
                hit_class=hit_class,
                ret_class=ret_class,
                setup=setup,
                name_suffix=f" {html.escape(name)}" if name else "",
            )
        )
    return "\n".join(rows)


def _render_cross_confirm_rows(items: list[dict]) -> str:
    rows = []
    for i, item in enumerate(items, 1):
        ticker = html.escape(str(item.get("ticker", "")))
        name = _NAME_LOOKUP.get(str(item.get("ticker", "")), "")
        name_html = f' <span style="color:#999;font-size:12px;">{html.escape(name)}</span>' if name else ""
        close_ref = _fmt_num(item.get("close_ref"), 2)
        t1_prob = f"{float(item.get('t1_prob', 0)) * 100:.1f}%" if item.get("t1_prob") else "-"
        t1_rank = f"#{item['t1_rank']}" if item.get("t1_rank") else "-"
        d20_ret = _fmt_pct(float(item.get("d20_pred_return", 0)) * 100) if item.get("d20_pred_return") else "-"
        d20_rec = html.escape(str(item.get("d20_recommendation", "")))
        cross_score = f"{float(item.get('cross_score', 0)):.4f}" if item.get("cross_score") else "-"
        setup = html.escape(str(item.get("t1_setup_tags", "") or "-"))
        rows.append(
            f"""
            <tr>
              <td class="col-rank">#{i}</td>
              <td class="col-target">
                <div class="cell-title">{ticker}{name_html}</div>
                <div class="cell-sub">收盤 {close_ref} / T+1 排名 {t1_rank} / 機率 {t1_prob}</div>
                <div class="cell-badges">
                  <span class="{_badge_class(d20_rec, kind='recommendation')}">{d20_rec}</span>
                </div>
                <div class="cell-sub" style="margin-top:4px;">型態：{setup}</div>
              </td>
              <td class="col-return">
                <div class="cell-title">{d20_ret}</div>
                <div class="cell-sub">綜合分數 {cross_score}</div>
              </td>
            </tr>
            """
        )
    return "\n".join(rows)


def _wrap_email_html(title: str, subtitle: str, body_html: str, generated_at: str) -> str:
    """Wrap section HTML in the shared email shell (head/style/hero/foot)."""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      margin: 0;
      background: #eef2f7;
      color: #0f172a;
      font-family: 'Segoe UI', 'Microsoft JhengHei', sans-serif;
      -webkit-text-size-adjust: 100%;
      text-size-adjust: 100%;
    }}
    .wrap {{
      max-width: 760px;
      margin: 0 auto;
      padding: 18px 12px 26px;
    }}
    .hero {{
      background: #ffffff;
      color: #0f172a;
      border-radius: 16px;
      padding: 18px 18px 16px;
      border: 1px solid #d8e0ea;
      border-top: 6px solid #2f6fed;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }}
    h1, h2 {{
      margin: 0 0 10px;
      color: inherit;
    }}
    .hero-kicker {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
      color: #2f6fed;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .hero-title {{
      margin: 0 0 12px;
      font-size: 30px;
      line-height: 1.22;
      font-weight: 800;
      color: #183b63 !important;
      -webkit-text-fill-color: #183b63;
    }}
    .hero-meta {{
      margin-top: 8px;
      font-size: 15px;
      line-height: 1.55;
      color: #395170 !important;
      -webkit-text-fill-color: #395170;
    }}
    .section {{
      margin-top: 16px;
      background: #ffffff;
      border-radius: 16px;
      padding: 16px 16px 14px;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
      border: 1px solid #d8e0ea;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .summary-card {{
      background: #f8fafc;
      border: 1px solid #d8e0ea;
      border-radius: 14px;
      padding: 14px 14px 12px;
    }}
    .label {{
      font-size: 12px;
      color: #475569;
      margin-bottom: 6px;
      font-weight: 600;
      line-height: 1.45;
    }}
    .value {{
      font-size: 24px;
      font-weight: 700;
      color: #0f172a;
    }}
    .muted {{
      color: #475569;
      font-size: 14px;
      line-height: 1.6;
      word-wrap: break-word;
      overflow-wrap: anywhere;
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      margin-top: 14px;
      background: #ffffff;
      border: 1px solid #d8e0ea;
      border-radius: 14px;
      overflow: hidden;
    }}
    .data-table th,
    .data-table td {{
      padding: 11px 10px;
      border-bottom: 1px solid #e5ebf2;
      vertical-align: top;
      text-align: left;
      color: #0f172a;
      font-size: 13px;
      line-height: 1.5;
      word-break: break-word;
    }}
    .data-table th {{
      background: #f4f7fb;
      color: #334155;
      font-weight: 700;
      font-size: 12px;
    }}
    .data-table tr:last-child td {{
      border-bottom: none;
    }}
    #sec-entry-candidates .entry-detail td {{
      background: #f8fafc;
      border-bottom: 2px solid #cbd5e1;
    }}
    #sec-entry-candidates .entry-field-label {{
      display: none;
    }}
    .detail-row td {{
      padding-top: 6px;
      padding-bottom: 10px;
      background: #fafcff;
    }}
    .detail-spacer {{
      border-bottom: 1px solid #e5ebf2;
    }}
    .detail-cell {{
      border-bottom: 1px solid #e5ebf2;
    }}
    .col-rank {{
      width: 54px;
      text-align: center;
      white-space: nowrap;
      font-weight: 700;
    }}
    .leaderboard-table .col-target {{
      width: 52%;
    }}
    .leaderboard-table .col-return {{
      width: 24%;
      white-space: nowrap;
      font-weight: 700;
    }}
    .portfolio-table .col-date {{
      width: 28%;
    }}
    .portfolio-table .col-target {{
      width: 42%;
    }}
    .portfolio-table .col-return {{
      width: 30%;
      white-space: nowrap;
      font-weight: 700;
    }}
    .cell-title {{
      font-size: 15px;
      font-weight: 800;
      color: #0f172a;
      line-height: 1.35;
    }}
    .cell-sub {{
      margin-top: 2px;
      font-size: 12px;
      color: #64748b;
      line-height: 1.45;
    }}
    .value-up {{
      color: #c62828;
    }}
    .value-down {{
      color: #0f8a3b;
    }}
    .value-flat {{
      color: #0f172a;
    }}
    .cell-badges {{
      margin-top: 6px;
    }}
    .cell-badges .badge {{
      margin-right: 6px;
      margin-bottom: 4px;
    }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      line-height: 1.4;
      white-space: nowrap;
    }}
    .badge-strong-buy {{
      background: #e7f8ee;
      color: #166534;
      border: 1px solid #9dd8b4;
    }}
    .badge-buy {{
      background: #eaf4ff;
      color: #1d4ed8;
      border: 1px solid #b6d3ff;
    }}
    .badge-watch {{
      background: #fff7df;
      color: #a16207;
      border: 1px solid #f2d08a;
    }}
    .badge-sell {{
      background: #fdebed;
      color: #be123c;
      border: 1px solid #f2b7c0;
    }}
    .badge-open {{
      background: #eaf4ff;
      color: #1d4ed8;
      border: 1px solid #b6d3ff;
    }}
    .badge-pending {{
      background: #fff7df;
      color: #a16207;
      border: 1px solid #f2d08a;
    }}
    .badge-closed {{
      background: #f1ecff;
      color: #6d28d9;
      border: 1px solid #d5c4ff;
    }}
    .badge-neutral {{
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #d7dee7;
    }}
    .risk-text {{
      color: #7f1d1d;
      font-size: 13px;
      font-weight: 600;
      line-height: 1.55;
    }}
    .detail-label {{
      display: inline-block;
      margin-bottom: 4px;
      color: #9a3412;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
    }}
    .portfolio-rec {{
      margin-top: 0;
    }}
    .foot {{
      margin-top: 18px;
      color: #64748b;
      font-size: 12px;
      line-height: 1.55;
    }}
    @media screen and (max-width: 540px) {{
      .wrap {{
        padding: 14px 8px 22px;
      }}
      .hero {{
        padding: 16px 14px 14px;
      }}
      .hero-title {{
        font-size: 24px;
      }}
      .section {{
        padding: 14px 12px 12px;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .data-table th,
      .data-table td {{
        padding: 8px 6px;
        font-size: 11px;
      }}
      .cell-title {{
        font-size: 13px;
      }}
      .cell-sub {{
        font-size: 11px;
      }}
      .badge {{
        padding: 3px 7px;
        font-size: 10px;
      }}
      .leaderboard-table .col-target {{
        width: 50%;
      }}
      .leaderboard-table .col-return {{
        width: 26%;
      }}
      .portfolio-table .col-date {{
        width: 26%;
      }}
      .portfolio-table .col-target {{
        width: 44%;
      }}
      .portfolio-table .col-return {{
        width: 30%;
      }}
      #sec-entry-candidates .data-table,
      #sec-entry-candidates tbody,
      #sec-entry-candidates tr,
      #sec-entry-candidates td {{
        display: block;
      }}
      #sec-entry-candidates thead {{
        display: none;
      }}
      #sec-entry-candidates .entry-main td {{
        padding: 10px;
        font-size: 13px;
        min-height: 20px;
      }}
      #sec-entry-candidates .entry-field-label {{
        display: block;
        margin-bottom: 4px;
        color: #475569;
        font-size: 12px;
        font-weight: 700;
      }}
      #sec-entry-candidates .entry-detail td {{
        padding: 12px 10px 16px;
        font-size: 13px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="hero-kicker">Stock ML Bot</div>
      <h1 class="hero-title" style="margin:0 0 12px;color:#183b63 !important;-webkit-text-fill-color:#183b63;">{html.escape(title)}</h1>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">{subtitle}</div>
      <div class="hero-meta" style="color:#395170 !important;-webkit-text-fill-color:#395170;">產出時間：{generated_at}</div>
    </div>

    {body_html}

    <div class="foot">
      此信件由 F:\\stock 每日 pipeline 自動產生。
    </div>
  </div>
</body>
</html>
"""


def build_email_html(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    leaderboard_df: pd.DataFrame,
    portfolio_summary: dict[str, object],
    portfolio_df: pd.DataFrame,
    t1_date: str = "",
    t1_df: pd.DataFrame | None = None,
    t1_portfolio_summary: dict[str, object] | None = None,
    t1_portfolio_df: pd.DataFrame | None = None,
    cross_confirmed: list[dict] | None = None,
) -> str:
    """Build single combined email (kept for backward compat / preview)."""
    sentiment = _html_cell(all_pred_df["market_sentiment"].iloc[0]) if "market_sentiment" in all_pred_df.columns else "-"
    q25 = (
        _fmt_pct(float(all_pred_df["market_return_q25"].iloc[0]) * 100)
        if "market_return_q25" in all_pred_df.columns else "-"
    )
    median = (
        _fmt_pct(float(all_pred_df["market_return_median"].iloc[0]) * 100)
        if "market_return_median" in all_pred_df.columns else "-"
    )
    q75 = (
        _fmt_pct(float(all_pred_df["market_return_q75"].iloc[0]) * 100)
        if "market_return_q75" in all_pred_df.columns else "-"
    )

    top_count = len(leaderboard_df)
    summary_cards = [
        ("總批次", portfolio_summary.get("total_runs")),
        ("持倉中", portfolio_summary.get("open_positions")),
        ("待進場", portfolio_summary.get("pending_positions")),
        ("已平倉", portfolio_summary.get("closed_positions")),
        ("10% 回撤警示", portfolio_summary.get("breach_10pct_count")),
        ("已平倉平均報酬", _fmt_pct(
            float(portfolio_summary["avg_realized_return_pct"]) * 100
            if portfolio_summary.get("avg_realized_return_pct") is not None else None
        )),
    ]

    cards_html = "\n".join(
        f"""
        <div class="summary-card">
          <div class="label">{html.escape(str(label))}</div>
          <div class="value">{html.escape(str(value if value not in (None, '') else '-'))}</div>
        </div>
        """
        for label, value in summary_cards
    )

    leaderboard_section = (
        f"""
        <table class="data-table leaderboard-table">
          <thead>
            <tr>
              <th class="col-rank">名次</th>
              <th class="col-target">標的 / 推薦</th>
              <th class="col-return">預估 20 日報酬</th>
            </tr>
          </thead>
          <tbody>
            {_render_leaderboard_rows(leaderboard_df)}
          </tbody>
        </table>
        """
        if not leaderboard_df.empty
        else "<p class='muted'>目前沒有可顯示的 ML 排行資料。</p>"
    )

    portfolio_section = (
        f"""
        <table class="data-table portfolio-table">
          <thead>
            <tr>
              <th class="col-date">入選日 / 名次 / 代號</th>
              <th class="col-target">狀態 / 推薦 / 價格</th>
              <th class="col-return">帳面報酬 / 回撤</th>
            </tr>
          </thead>
          <tbody>
            {_render_portfolio_rows(portfolio_df)}
          </tbody>
        </table>
        """
        if not portfolio_df.empty
        else "<p class='muted'>目前沒有可顯示的帳本部位資料。</p>"
    )

    _t1_df = t1_df if t1_df is not None else pd.DataFrame()
    t1_section = ""
    if not _t1_df.empty:
        t1_count = len(_t1_df)
        t1_section = f"""
    <div class="section" id="sec-t1">
      <h2>T+1 次日動能排行（{html.escape(t1_date)}）<a href="#" class="back-top">&#8679; 頂部</a></h2>
      <div class="muted">短線隔日沖 Top {t1_count}。命中率 = 隔日漲幅 &ge; 3% 的機率。</div>
      <table class="data-table leaderboard-table">
        <thead>
          <tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / 推薦 / 型態</th>
            <th class="col-return">命中率 / 停利停損</th>
          </tr>
        </thead>
        <tbody>
          {_render_t1_rows(_t1_df)}
        </tbody>
      </table>
    </div>
"""
    else:
        t1_section = ""

    # T+1 portfolio section
    _t1_port_df = t1_portfolio_df if t1_portfolio_df is not None else pd.DataFrame()
    _t1_port_summary = t1_portfolio_summary or {}
    if not _t1_port_df.empty or _t1_port_summary:
        t1_total = _t1_port_summary.get("closed_positions", 0)
        t1_hit = _t1_port_summary.get("hit_count", 0)
        t1_hit_rate = _fmt_pct(float(_t1_port_summary["hit_rate"]) * 100, 1) if _t1_port_summary.get("hit_rate") is not None else "-"
        t1_avg_ret = _fmt_pct(float(_t1_port_summary["avg_realized_return_pct"]) * 100) if _t1_port_summary.get("avg_realized_return_pct") is not None else "-"
        t1_pending = _t1_port_summary.get("pending_positions", 0)

        t1_cards_html = f"""
        <div class="summary-card"><div class="label">已結算</div><div class="value">{t1_total}</div></div>
        <div class="summary-card"><div class="label">待結算</div><div class="value">{t1_pending}</div></div>
        <div class="summary-card"><div class="label">命中率（盤中 &ge;3%）</div><div class="value">{t1_hit}/{t1_total} = {t1_hit_rate}</div></div>
        <div class="summary-card"><div class="label">平均開→收報酬</div><div class="value">{t1_avg_ret}</div></div>
        """

        t1_port_table = (
            f"""
            <table class="data-table portfolio-table">
              <thead>
                <tr>
                  <th class="col-date">預測日 / 名次 / 代號</th>
                  <th class="col-target">狀態 / 型態 / 價格</th>
                  <th class="col-return">報酬 / 高低 / 命中</th>
                </tr>
              </thead>
              <tbody>
                {_render_t1_portfolio_rows(_t1_port_df)}
              </tbody>
            </table>
            """
            if not _t1_port_df.empty
            else "<p class='muted'>目前沒有 T+1 帳本資料。</p>"
        )

        t1_portfolio_section = f"""
    <div class="section" id="sec-t1port">
      <h2>T+1 實戰帳本<a href="#" class="back-top">&#8679; 頂部</a></h2>
      <div class="grid">
        {t1_cards_html}
      </div>
      {t1_port_table}
    </div>
"""
    else:
        t1_portfolio_section = ""

    # Cross-confirmation section
    _cross = cross_confirmed or []
    if _cross:
        cross_count = len(_cross)
        cross_section = f"""
    <div class="section" id="sec-cross">
      <h2>T+1 x 20D 雙重確認<a href="#" class="back-top">&#8679; 頂部</a></h2>
      <div class="muted">以下 {cross_count} 檔同時被 T+1（次日動能）和 20D（中期趨勢）模型看好，訊號一致性較高。</div>
      <table class="data-table leaderboard-table">
        <thead>
          <tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / T+1 排名 / 20D 推薦</th>
            <th class="col-return">20D 預估報酬 / 綜合分數</th>
          </tr>
        </thead>
        <tbody>
          {_render_cross_confirm_rows(_cross)}
        </tbody>
      </table>
    </div>
"""
    else:
        cross_section = ""

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    market_subtitle = f"市場氣氛：{sentiment} / 25%：{q25} / 中位：{median} / 75%：{q75}"
    body_sections = f"""
    <div class="section">
      <h2>今日 ML 排行（20 日）</h2>
      <div class="muted">Top {top_count}。</div>
      {leaderboard_section}
    </div>

    {t1_section}

    {cross_section}

    <div class="section">
      <h2>20 日實戰帳本</h2>
      <div class="grid">
        {cards_html}
      </div>
      {portfolio_section}
    </div>

    {t1_portfolio_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} 每日 ML 預測與帳本觀察",
        subtitle=market_subtitle,
        body_html=body_sections,
        generated_at=generated_at,
    )


# --- Individual email builders for split-send mode ---


def _build_market_subtitle(all_pred_df: pd.DataFrame) -> str:
    sentiment = _html_cell(all_pred_df["market_sentiment"].iloc[0]) if "market_sentiment" in all_pred_df.columns else "-"
    q25 = _fmt_pct(float(all_pred_df["market_return_q25"].iloc[0]) * 100) if "market_return_q25" in all_pred_df.columns else "-"
    median = _fmt_pct(float(all_pred_df["market_return_median"].iloc[0]) * 100) if "market_return_median" in all_pred_df.columns else "-"
    q75 = _fmt_pct(float(all_pred_df["market_return_q75"].iloc[0]) * 100) if "market_return_q75" in all_pred_df.columns else "-"
    return f"市場氣氛：{sentiment} / 25%：{q25} / 中位：{median} / 75%：{q75}"


def build_email_ml(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    leaderboard_df: pd.DataFrame,
    cross_confirmed: list[dict] | None = None,
) -> str:
    """信1: ML 排行 + 雙重確認"""
    top_count = len(leaderboard_df)

    leaderboard_section = (
        f"""
        <table class="data-table leaderboard-table">
          <thead><tr>
            <th class="col-rank">名次</th>
            <th class="col-target">標的 / 推薦</th>
            <th class="col-return">預估 20 日報酬</th>
          </tr></thead>
          <tbody>{_render_leaderboard_rows(leaderboard_df)}</tbody>
        </table>
        """
        if not leaderboard_df.empty
        else "<p class='muted'>目前沒有可顯示的 ML 排行資料。</p>"
    )

    _cross = cross_confirmed or []
    cross_section = ""
    if _cross:
        cross_count = len(_cross)
        cross_section = f"""
    <div class="section">
      <h2>T+1 x 20D 雙重確認</h2>
      <div class="muted">以下 {cross_count} 檔同時被 T+1 和 20D 模型看好。</div>
      <table class="data-table leaderboard-table">
        <thead><tr>
          <th class="col-rank">名次</th>
          <th class="col-target">標的 / T+1 排名 / 20D 推薦</th>
          <th class="col-return">20D 預估報酬 / 綜合分數</th>
        </tr></thead>
        <tbody>{_render_cross_confirm_rows(_cross)}</tbody>
      </table>
    </div>
"""

    body = f"""
    <div class="section">
      <h2>今日 ML 排行（20 日）</h2>
      <div class="muted">Top {top_count}。</div>
      {leaderboard_section}
    </div>
    {cross_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} ML 排行 + 雙重確認",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_email_t1(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    t1_date: str,
    t1_df: pd.DataFrame,
) -> str:
    """信2: T+1 動能"""
    t1_count = len(t1_df)
    body = f"""
    <div class="section">
      <h2>T+1 次日動能排行（{html.escape(t1_date)}）</h2>
      <div class="muted">短線隔日沖 Top {t1_count}。命中率 = 隔日漲幅 &ge; 3% 的機率。</div>
      <table class="data-table leaderboard-table">
        <thead><tr>
          <th class="col-rank">名次</th>
          <th class="col-target">標的 / 推薦 / 型態</th>
          <th class="col-return">命中率 / 停利停損</th>
        </tr></thead>
        <tbody>{_render_t1_rows(t1_df)}</tbody>
      </table>
    </div>
"""
    return _wrap_email_html(
        title=f"{prediction_date} T+1 動能預測",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_email_portfolio(
    prediction_date: str,
    all_pred_df: pd.DataFrame,
    portfolio_summary: dict[str, object],
    portfolio_df: pd.DataFrame,
    t1_portfolio_summary: dict[str, object] | None = None,
    t1_portfolio_df: pd.DataFrame | None = None,
) -> str:
    """信3: 帳本總覽（20D + T+1）"""
    summary_cards = [
        ("總批次", portfolio_summary.get("total_runs")),
        ("持倉中", portfolio_summary.get("open_positions")),
        ("待進場", portfolio_summary.get("pending_positions")),
        ("已平倉", portfolio_summary.get("closed_positions")),
        ("10% 回撤警示", portfolio_summary.get("breach_10pct_count")),
        ("已平倉平均報酬", _fmt_pct(
            float(portfolio_summary["avg_realized_return_pct"]) * 100
            if portfolio_summary.get("avg_realized_return_pct") is not None else None
        )),
    ]
    cards_html = "\n".join(
        f'<div class="summary-card"><div class="label">{html.escape(str(label))}</div>'
        f'<div class="value">{html.escape(str(value if value not in (None, "") else "-"))}</div></div>'
        for label, value in summary_cards
    )

    portfolio_section = (
        f"""
        <table class="data-table portfolio-table">
          <thead><tr>
            <th class="col-date">入選日 / 名次 / 代號</th>
            <th class="col-target">狀態 / 推薦 / 價格</th>
            <th class="col-return">帳面報酬 / 回撤</th>
          </tr></thead>
          <tbody>{_render_portfolio_rows(portfolio_df)}</tbody>
        </table>
        """
        if not portfolio_df.empty
        else "<p class='muted'>目前沒有可顯示的帳本部位資料。</p>"
    )

    # T+1 portfolio
    _t1_port_df = t1_portfolio_df if t1_portfolio_df is not None else pd.DataFrame()
    _t1_port_summary = t1_portfolio_summary or {}
    t1_portfolio_section = ""
    if not _t1_port_df.empty or _t1_port_summary:
        t1_total = _t1_port_summary.get("closed_positions", 0)
        t1_hit_rate = _fmt_pct(float(_t1_port_summary["hit_rate"]) * 100, 1) if _t1_port_summary.get("hit_rate") is not None else "-"
        t1_avg_ret = _fmt_pct(float(_t1_port_summary["avg_realized_return_pct"]) * 100) if _t1_port_summary.get("avg_realized_return_pct") is not None else "-"
        t1_pending = _t1_port_summary.get("pending_positions", 0)
        t1_hit = _t1_port_summary.get("hit_count", 0)

        t1_cards = f"""
        <div class="summary-card"><div class="label">已結算</div><div class="value">{t1_total}</div></div>
        <div class="summary-card"><div class="label">待結算</div><div class="value">{t1_pending}</div></div>
        <div class="summary-card"><div class="label">命中率（盤中 &ge;3%）</div><div class="value">{t1_hit}/{t1_total} = {t1_hit_rate}</div></div>
        <div class="summary-card"><div class="label">平均開→收報酬</div><div class="value">{t1_avg_ret}</div></div>
        """

        t1_port_table = (
            f"""
            <table class="data-table portfolio-table">
              <thead><tr>
                <th class="col-date">預測日 / 名次 / 代號</th>
                <th class="col-target">狀態 / 型態 / 價格</th>
                <th class="col-return">報酬 / 高低 / 命中</th>
              </tr></thead>
              <tbody>{_render_t1_portfolio_rows(_t1_port_df)}</tbody>
            </table>
            """
            if not _t1_port_df.empty
            else "<p class='muted'>目前沒有 T+1 帳本資料。</p>"
        )

        t1_portfolio_section = f"""
    <div class="section">
      <h2>T+1 實戰帳本</h2>
      <div class="grid">{t1_cards}</div>
      {t1_port_table}
    </div>
"""

    body = f"""
    <div class="section">
      <h2>20 日實戰帳本</h2>
      <div class="grid">{cards_html}</div>
      {portfolio_section}
    </div>
    {t1_portfolio_section}
"""
    return _wrap_email_html(
        title=f"{prediction_date} 帳本總覽",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# --- (end of old inline template removed) ---



def _build_email_message(settings: EmailSettings, subject: str, html_body: str) -> EmailMessage:
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = subject
    message["From"] = formataddr((settings.from_name, settings.from_email), charset="utf-8")
    message["To"] = ", ".join(settings.to_emails)
    message.set_content(PLAIN_TEXT_FALLBACK, subtype="plain", charset="utf-8", cte="base64")
    message.add_alternative(html_body, subtype="html", charset="utf-8", cte="base64")
    return message


def send_email(settings: EmailSettings, subject: str, html_body: str) -> None:
    message = _build_email_message(settings, subject, html_body)

    # Try SSL(465) → STARTTLS(587) fallback with retries for transient network/Gmail issues
    attempts: list[tuple[bool, int]]
    if settings.use_ssl:
        attempts = [(True, settings.port), (False, 587)]
    else:
        attempts = [(False, settings.port), (True, 465)]

    last_err: Exception | None = None
    for mode_ssl, port in attempts:
        for retry in range(3):
            try:
                if mode_ssl:
                    with smtplib.SMTP_SSL(settings.host, port, timeout=60) as server:
                        server.login(settings.user, settings.password)
                        server.send_message(message)
                else:
                    with smtplib.SMTP(settings.host, port, timeout=60) as server:
                        server.starttls()
                        server.login(settings.user, settings.password)
                        server.send_message(message)
                return
            except (TimeoutError, OSError, smtplib.SMTPException) as exc:
                last_err = exc
                print(
                    f"[email] SMTP {'SSL' if mode_ssl else 'STARTTLS'} "
                    f"{settings.host}:{port} attempt {retry + 1}/3 failed: {exc}"
                )
                time.sleep(5 * (retry + 1))

    raise RuntimeError(f"send_email failed after all retries: {last_err}")


def send_latest_email(
    prediction_file: str | None = None,
    as_of_date: str | None = None,
    dry_run: bool = False,
    preview_path: str | None = None,
    remote_url: str | None = None,
) -> dict[str, object]:
    settings = load_email_settings()
    if settings is None:
        print("[email] SMTP 未設定，跳過寄信。")
        return {"status": "skipped", "reason": "missing_smtp_settings"}

    prediction_path = _latest_prediction_path(prediction_file, as_of_date=as_of_date)
    prediction_date, all_pred_df, leaderboard_df = _load_leaderboard(prediction_path, settings.top_n)

    # AI 每日投資總結
    from scripts.ai_summary import generate_daily_summary
    ai_html = generate_daily_summary(
        prediction_date=prediction_date,
        all_pred_df=all_pred_df,
        leaderboard_df=leaderboard_df,
    )
    unified_signal_stats = _load_unified_signal_stats(prediction_date)
    entry_candidate_section = _build_entry_candidate_email_section(
        prediction_date,
        all_pred_df,
    )
    chipk_mobile_section = _build_chipk_mobile_review_section(prediction_date, leaderboard_df)

    unified_signal_section = ""
    if unified_signal_stats:
        counts = unified_signal_stats.get("signal_type_counts", {}) or {}
        cards_html = "\n".join(
            (
                f'<div class="summary-card"><div class="label">{label}</div>'
                f'<div class="value">{value}</div></div>'
            )
            for label, value in [
                ("Unified Date", unified_signal_stats.get("prediction_date") or "-"),
                ("Dual", counts.get("Dual", 0)),
                ("20D_only", counts.get("20D_only", 0)),
                ("T1_only", counts.get("T1_only", 0)),
            ]
        )
        latest_source = html.escape(str(unified_signal_stats.get("source_file") or "-"))
        total_candidates = int(unified_signal_stats.get("total_candidates") or 0)
        total_units = int(unified_signal_stats.get("total_units") or 0)
        unified_signal_section = f"""
    <div class="section">
      <h2>Unified Signal Mix</h2>
      <div class="grid">
        {cards_html}
      </div>
      <div class="muted">Latest unified CSV: {latest_source} / candidates {total_candidates} / target units {total_units}</div>
    </div>
"""

    # Remote URL
    remote_section = ""
    if remote_url:
        remote_section = (
            f'<div style="text-align:center;margin:20px 0;">'
            f'<a href="{html.escape(remote_url)}" '
            f'style="display:inline-block;background:#2f6fed;color:#fff;'
            f'padding:14px 32px;border-radius:8px;font-size:16px;'
            f'font-weight:700;text-decoration:none;">'
            f'&#x1F310; 開啟遠端看盤</a></div>'
        )

    body = f"""
    {remote_section}
    {unified_signal_section}
    {entry_candidate_section}
    {chipk_mobile_section}
    <div class="section">
      <h2>AI 每日投資總結</h2>
      <div style="font-size:14px;line-height:1.8;color:#1e293b;">
        {ai_html}
      </div>
    </div>
"""

    subject = f"{settings.subject_prefix} {prediction_date} 每日投資總結"
    email_html = _wrap_email_html(
        title=f"{prediction_date} 每日投資總結",
        subtitle=_build_market_subtitle(all_pred_df),
        body_html=body,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Save preview
    preview_target = preview_path or DEFAULT_PREVIEW_PATH
    os.makedirs(os.path.dirname(preview_target), exist_ok=True)
    with open(preview_target, "w", encoding="utf-8") as f:
        f.write(email_html)

    if dry_run:
        try:
            from scripts.ai_summary import _summary_html_to_text
            print("[email] AI summary preview:")
            print(_summary_html_to_text(ai_html))
        except Exception:
            print("[email] AI summary preview unavailable")
        print(f"[email] Dry run 完成：{preview_target}")
        return {
            "status": "preview",
            "preview_path": preview_target,
            "subjects": [subject],
            "prediction_path": prediction_path,
        }

    send_email(settings, subject, email_html)
    print(f"[email] 已寄出 1 封至: {', '.join(settings.to_emails)}")
    return {
        "status": "sent",
        "preview_path": preview_target,
        "subjects": [subject],
        "prediction_path": prediction_path,
        "to": settings.to_emails,
    }


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Send the daily ML email report.")
    parser.add_argument("--prediction-file", help="Specific predictions_YYYY-MM-DD.csv path.")
    parser.add_argument("--as-of-date", help="Use predictions/unified_signals for YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true", help="Render HTML preview without sending.")
    parser.add_argument(
        "--preview-path",
        default=DEFAULT_PREVIEW_PATH,
        help="Where to write the HTML preview. Default: %(default)s",
    )
    args = parser.parse_args(argv)

    return send_latest_email(
        prediction_file=args.prediction_file,
        as_of_date=args.as_of_date,
        dry_run=args.dry_run,
        preview_path=args.preview_path,
    )


if __name__ == "__main__":
    main()
