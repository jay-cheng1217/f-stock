from __future__ import annotations

import json
import os
import sqlite3
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR
from ml.benchmark_contract import build_trade_alpha_benchmark_contract
from ml.features.sector import SECTOR_MAPPING_PATH
from scripts.build_unified_signals import apply_penalty_overlay
from scripts.signal_explainability import explain_signal_row
from scripts.update_unified_portfolio import PORTFOLIO_START_DATE, TWENTY_DAY_HOLD

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNTIME_ROOT = os.path.abspath(
    os.environ.get(
        "STOCK_RUNTIME_ROOT",
        os.path.join(REPORT_DIR, "..", ".."),
    )
)


def _resolve_runtime_path(env_name: str, candidates: list[str], fallback: str) -> str:
    env_value = os.environ.get(env_name)
    if env_value:
        return os.path.abspath(env_value)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(fallback)


CHAMPION_DB_PATH = _resolve_runtime_path(
    "V2_CHAMPION_DB_PATH",
    candidates=[
        os.path.join(RUNTIME_ROOT, "paper_portfolio_v2_champion.db"),
        os.path.join(RUNTIME_ROOT, "unified_portfolio.db"),
    ],
    fallback=os.path.join(BASE_DIR, "paper_portfolio_v2_champion.db"),
)
SHADOW_DB_PATH = _resolve_runtime_path(
    "V2_BASELINE_SHADOW_DB_PATH",
    candidates=[
        os.path.join(RUNTIME_ROOT, "shadow_portfolio_v2_baseline.db"),
        os.path.join(RUNTIME_ROOT, "unified_portfolio_shadow_baseline.db"),
    ],
    fallback=os.path.join(BASE_DIR, "shadow_portfolio_v2_baseline.db"),
)
DASHBOARD_SUMMARY_PATH = os.path.join(REPORT_DIR, "dashboard_summary.json")
PORTFOLIO_COMPARE_PATH = os.path.join(REPORT_DIR, "portfolio_champion_vs_shadow.json")
UNIFIED_SIGNALS_FEED_PATH = os.path.join(REPORT_DIR, "unified_signals_latest.json")
REPORT_PATHS = {
    "dashboard_summary": DASHBOARD_SUMMARY_PATH,
    "portfolio_champion_vs_shadow": PORTFOLIO_COMPARE_PATH,
    "unified_signals_latest": UNIFIED_SIGNALS_FEED_PATH,
}
LATEST_PREDICTION_RE = "predictions_"
LATEST_UNIFIED_RE = "unified_signals_"


@lru_cache(maxsize=1)
def _load_name_lookup() -> dict[str, str]:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}
    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in df.columns:
        return {}
    return dict(zip(df["Ticker"], df["Name"]))


def _safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _append_tag(series: pd.Series, mask: pd.Series, tag: str) -> pd.Series:
    out = series.fillna("").astype(str).copy()
    active = mask.fillna(False)
    out.loc[active] = out.loc[active].apply(lambda value: f"{value}; {tag}".strip("; "))
    return out


def _load_latest_chipk_review_frame() -> pd.DataFrame:
    """Load the latest ChipK diagnosis fields for entry-filter display."""

    try:
        from ml.chipk import load_latest_chipk_main_force

        chipk, _meta = load_latest_chipk_main_force()
    except Exception:
        return pd.DataFrame()
    if chipk.empty or "ticker" not in chipk.columns:
        return pd.DataFrame()

    keep = [
        "ticker",
        "chipk_asof_date",
        "chipk_bucket",
        "chipk_main_force_pattern",
        "chipk_entry_alignment",
        "chipk_trap_risk",
        "chipk_distribution_risk",
        "chipk_risk_flags",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
    ]
    available = [column for column in keep if column in chipk.columns]
    out = chipk.loc[:, available].copy()
    out["ticker"] = out["ticker"].astype(str).str.strip().str.zfill(4)
    return out.drop_duplicates("ticker", keep="first")


def _apply_live_price_no_chase_guard(prediction_df: pd.DataFrame) -> pd.DataFrame:
    """Downgrade momentum entries that have already traded above the entry zone.

    The prediction file is built from the prior close. During the session, the
    web feed may receive a fresher quote column. When it does, the entry filter
    must stop treating a stale setup as actionable after the price gaps beyond
    the approved zone.
    """

    if prediction_df.empty or "momentum_action" not in prediction_df.columns:
        return prediction_df

    out = prediction_df.copy()
    live_price = pd.Series(np.nan, index=out.index, dtype="float64")
    for column in ["entry_live_price", "latest_price", "current_price", "last_price", "intraday_price"]:
        if column in out.columns:
            live_price = live_price.where(live_price.notna(), pd.to_numeric(out[column], errors="coerce"))
    entry_high = pd.to_numeric(out.get("momentum_entry_zone_high"), errors="coerce")
    action = out["momentum_action"].fillna("").astype(str)
    missed = action.eq("MOMENTUM_LIMIT_BUY") & live_price.notna() & entry_high.notna() & live_price.gt(entry_high * 1.003)
    if not missed.any():
        out["momentum_live_price"] = live_price
        out["momentum_price_status"] = np.where(live_price.notna(), "live_price_checked", "no_live_price")
        return out

    out["momentum_live_price"] = live_price
    out["momentum_price_status"] = np.where(live_price.notna(), "live_price_checked", "no_live_price")
    out.loc[missed, "momentum_price_status"] = "above_entry_zone_no_chase"
    out.loc[missed, "momentum_action"] = "MOMENTUM_MISSED_NO_CHASE"
    out["momentum_missing_strategy_tags"] = _append_tag(
        out.get("momentum_missing_strategy_tags", pd.Series("", index=out.index, dtype="object")),
        missed,
        "price_above_entry_zone_no_chase",
    )
    for idx in out.index[missed]:
        out.at[idx, "momentum_entry_strategy_advice"] = (
            "missed_no_chase; "
            f"latest_price={float(live_price.loc[idx]):.2f}; "
            f"above_entry_zone_high={float(entry_high.loc[idx]):.2f}; "
            "wait_next_setup"
        )
    return out


def _prediction_date_from_row(row: pd.Series) -> pd.Timestamp | None:
    for column in ["date", "prediction_date", "Date"]:
        if column in row.index and pd.notna(row.get(column)):
            value = pd.to_datetime(row.get(column), errors="coerce")
            if pd.notna(value):
                return pd.Timestamp(value).normalize()
    return None


def _momentum_signal_age_for_ticker(ticker: str, prediction_date: pd.Timestamp) -> dict[str, Any] | None:
    try:
        from scripts.momentum_continuation_backtest import BASE_DIR, annotate_momentum_continuation, prepare_stock_frame
    except Exception:
        return None

    path = BASE_DIR / "\u65e5K\u8cc7\u6599" / f"{str(ticker).zfill(4)}.csv"
    if not path.exists():
        return None
    try:
        frame = annotate_momentum_continuation(prepare_stock_frame(path))
    except Exception:
        return None
    if frame.empty or "momentum_setup" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
    matching = frame.index[dates.eq(prediction_date)]
    if len(matching) == 0:
        return None
    pos = int(matching[-1])
    if not bool(frame.at[pos, "momentum_setup"]):
        return None
    start = pos
    while start > 0 and bool(frame.at[start - 1, "momentum_setup"]):
        start -= 1
    first_date = pd.Timestamp(frame.at[start, "Date"]).normalize()
    return {
        "momentum_first_signal_date": first_date.strftime("%Y-%m-%d"),
        "momentum_setup_age_days": int(pos - start),
        "momentum_signal_phase": "fresh_setup" if pos == start else "active_follow_through",
    }


def _apply_momentum_signal_age_guard(prediction_df: pd.DataFrame) -> pd.DataFrame:
    if prediction_df.empty or "momentum_action" not in prediction_df.columns:
        return prediction_df

    out = prediction_df.copy()
    out["momentum_first_signal_date"] = out.get("momentum_first_signal_date", pd.Series("", index=out.index))
    out["momentum_setup_age_days"] = pd.to_numeric(
        out.get("momentum_setup_age_days", pd.Series(np.nan, index=out.index)),
        errors="coerce",
    )
    out["momentum_signal_phase"] = out.get("momentum_signal_phase", pd.Series("", index=out.index))

    active_actions = out["momentum_action"].fillna("").astype(str).isin(["MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK"])
    unknown_age = pd.Series(False, index=out.index)
    for idx, row in out.loc[active_actions].iterrows():
        prediction_date = _prediction_date_from_row(row)
        if prediction_date is None:
            unknown_age.loc[idx] = True
            continue
        age = _momentum_signal_age_for_ticker(str(row.get("ticker", "")), prediction_date)
        if not age:
            unknown_age.loc[idx] = True
            continue
        for column, value in age.items():
            out.at[idx, column] = value

    age_days = pd.to_numeric(out["momentum_setup_age_days"], errors="coerce")
    already_active = out["momentum_action"].fillna("").astype(str).eq("MOMENTUM_LIMIT_BUY") & age_days.gt(0)
    unknown_entry_age = out["momentum_action"].fillna("").astype(str).eq("MOMENTUM_LIMIT_BUY") & unknown_age
    if unknown_entry_age.any():
        out.loc[unknown_entry_age, "momentum_action"] = "MOMENTUM_REVIEW_NO_NEW_ENTRY"
        out["momentum_missing_strategy_tags"] = _append_tag(
            out.get("momentum_missing_strategy_tags", pd.Series("", index=out.index, dtype="object")),
            unknown_entry_age,
            "fresh_setup_unverified",
        )
        out.loc[unknown_entry_age, "momentum_signal_phase"] = "fresh_setup_unverified"
        out.loc[unknown_entry_age, "momentum_entry_strategy_advice"] = (
            "review_only; fresh_setup_unverified; do_not_new_enter_until_daily_k_replay_confirms"
        )
    if already_active.any():
        out.loc[already_active, "momentum_action"] = "MOMENTUM_ACTIVE_NO_NEW_ENTRY"
        out["momentum_missing_strategy_tags"] = _append_tag(
            out.get("momentum_missing_strategy_tags", pd.Series("", index=out.index, dtype="object")),
            already_active,
            "signal_already_active_no_new_entry",
        )
        for idx in out.index[already_active]:
            first_date = out.at[idx, "momentum_first_signal_date"]
            out.at[idx, "momentum_entry_strategy_advice"] = (
                f"active_follow_through_not_new_entry; first_signal_date={first_date}; "
                "manage_existing_position_or_wait_next_pullback"
            )
    return out


def _shortwave_candidate_rows(
    prediction_path: str | None,
    names: dict[str, str],
    *,
    limit: int = 60,
) -> pd.DataFrame:
    if not prediction_path or not os.path.exists(prediction_path):
        return pd.DataFrame()
    prediction_df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
    if "shortwave_action" not in prediction_df.columns:
        return pd.DataFrame()
    prediction_df = _apply_live_price_no_chase_guard(prediction_df)
    prediction_df = _apply_momentum_signal_age_guard(prediction_df)

    shortwave_action = prediction_df["shortwave_action"].fillna("").astype(str)
    tactical_action = (
        prediction_df["tactical_action"].fillna("").astype(str)
        if "tactical_action" in prediction_df.columns
        else pd.Series("", index=prediction_df.index, dtype="object")
    )
    momentum_action = (
        prediction_df["momentum_action"].fillna("").astype(str)
        if "momentum_action" in prediction_df.columns
        else pd.Series("", index=prediction_df.index, dtype="object")
    )
    swing_mask = shortwave_action.isin(["NORMAL_ENTRY", "SMALL_ENTRY"])
    tactical_mask = tactical_action.isin(["TACTICAL_LIMIT_BUY", "TACTICAL_WAIT_PULLBACK", "TACTICAL_WATCH"])
    momentum_mask = momentum_action.isin(["MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK", "MOMENTUM_WATCH"])
    momentum_tracking_mask = momentum_action.isin(
        ["MOMENTUM_ACTIVE_NO_NEW_ENTRY", "MOMENTUM_REVIEW_NO_NEW_ENTRY", "MOMENTUM_MISSED_NO_CHASE"]
    )
    candidates = prediction_df.loc[swing_mask | tactical_mask | momentum_mask | momentum_tracking_mask].copy()
    if candidates.empty:
        return pd.DataFrame()

    chipk_review = _load_latest_chipk_review_frame()
    if not chipk_review.empty and "ticker" in candidates.columns:
        chipk_cols = [column for column in chipk_review.columns if column != "ticker"]
        candidates = candidates.drop(columns=[column for column in chipk_cols if column in candidates.columns], errors="ignore")
        candidates["_chipk_join_key"] = candidates["ticker"].astype(str).str.strip().str.zfill(4)
        candidates = candidates.merge(
            chipk_review.rename(columns={"ticker": "_chipk_join_key"}),
            on="_chipk_join_key",
            how="left",
        ).drop(columns=["_chipk_join_key"])

    def _string_column(column: str) -> pd.Series:
        if column in candidates.columns:
            return candidates[column].fillna("").astype(str)
        return pd.Series([""] * len(candidates), index=candidates.index, dtype="object")

    def _numeric_column(column: str) -> pd.Series:
        if column in candidates.columns:
            return pd.to_numeric(candidates[column], errors="coerce")
        return pd.Series(np.nan, index=candidates.index, dtype="float64")

    candidates["name"] = candidates["ticker"].map(names).fillna("")
    candidates["signal_type"] = "SHORTWAVE_CANDIDATE"
    candidates["_has_swing_strategy"] = candidates["shortwave_action"].astype(str).isin(["NORMAL_ENTRY", "SMALL_ENTRY"])
    candidates["_has_tactical_strategy"] = candidates.get(
        "tactical_action",
        pd.Series("", index=candidates.index, dtype="object"),
    ).astype(str).isin(["TACTICAL_LIMIT_BUY", "TACTICAL_WAIT_PULLBACK", "TACTICAL_WATCH"])
    candidates["_has_momentum_strategy"] = candidates.get(
        "momentum_action",
        pd.Series("", index=candidates.index, dtype="object"),
    ).astype(str).isin(["MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK", "MOMENTUM_WATCH"])
    candidates["_has_momentum_tracking"] = candidates.get(
        "momentum_action",
        pd.Series("", index=candidates.index, dtype="object"),
    ).astype(str).isin(["MOMENTUM_ACTIVE_NO_NEW_ENTRY", "MOMENTUM_REVIEW_NO_NEW_ENTRY", "MOMENTUM_MISSED_NO_CHASE"])
    candidates["strategy_lane"] = np.select(
        [
            candidates["_has_momentum_strategy"] & candidates["_has_tactical_strategy"],
            candidates["_has_momentum_strategy"] & candidates["_has_swing_strategy"],
            candidates["_has_swing_strategy"] & candidates["_has_tactical_strategy"],
            candidates["_has_momentum_strategy"],
            candidates["_has_tactical_strategy"],
            candidates["_has_swing_strategy"],
            candidates["_has_momentum_tracking"],
        ],
        ["momentum+tactical", "momentum+swing", "tactical+swing", "momentum", "tactical", "swing", "momentum_tracking"],
        default="watch",
    )
    candidates["_strategy_priority"] = np.select(
        [
            candidates["_has_momentum_strategy"],
            candidates["_has_swing_strategy"] & candidates["_has_tactical_strategy"],
            candidates["_has_tactical_strategy"],
            candidates["_has_swing_strategy"],
            candidates["_has_momentum_tracking"],
        ],
        [0, 1, 2, 3, 4],
        default=5,
    )
    candidates["target_weight_pct"] = np.where(
        candidates["shortwave_action"].eq("NORMAL_ENTRY"),
        100.0,
        np.where(candidates["shortwave_action"].eq("SMALL_ENTRY"), 50.0, 25.0),
    )
    candidates["base_score"] = (_numeric_column("pred_return_20d") * 100.0).round(2)
    candidates["net_score"] = (
        _numeric_column("leaderboard_score_before_shortwave")
        .fillna(_numeric_column("leaderboard_score"))
    ).round(4)
    candidates["close_ref"] = _numeric_column("close")
    candidates["explain_action"] = np.select(
        [
            candidates["_has_momentum_strategy"],
            candidates["_has_swing_strategy"],
            candidates["_has_tactical_strategy"],
        ],
        [
            _string_column("momentum_action"),
            candidates["shortwave_action"].astype(str),
            _string_column("tactical_action"),
        ],
        default="OBSERVE",
    )
    candidates["explain_action_label"] = np.select(
        [
            candidates["_has_swing_strategy"] & candidates["_has_tactical_strategy"],
            candidates["shortwave_action"].eq("NORMAL_ENTRY"),
            candidates["shortwave_action"].eq("SMALL_ENTRY"),
            candidates["_has_tactical_strategy"],
        ],
        ["短打+波段", "波段進場", "波段小量", "短打低吸"],
        default="觀察",
    )
    candidates["explain_summary"] = (
        candidates["strategy_lane"].astype(str)
        + " / swing "
        + candidates["shortwave_action"].astype(str)
        + " / tactical "
        + _string_column("tactical_action")
        + " / momentum "
        + _string_column("momentum_action")
        + " / zone "
        + _string_column("shortwave_entry_zone_status")
    )
    candidates.loc[
        candidates["_has_momentum_strategy"] & ~candidates["_has_tactical_strategy"],
        "explain_action_label",
    ] = "強勢續攻"
    candidates.loc[
        candidates["_has_momentum_strategy"] & candidates["_has_tactical_strategy"],
        "explain_action_label",
    ] = "強勢續攻+短打低吸"
    candidates["explain_drivers"] = np.where(
        candidates["_has_momentum_strategy"] & ~candidates["_has_swing_strategy"],
        _string_column("momentum_reason"),
        _string_column("shortwave_strategy_tags"),
    )
    candidates["explain_warnings"] = np.where(
        candidates["_has_momentum_strategy"] & ~candidates["_has_swing_strategy"],
        _string_column("momentum_missing_strategy_tags"),
        _string_column("shortwave_missing_strategy_tags"),
    )
    momentum_action_clean = _string_column("momentum_action")
    candidates["candidate_scope"] = np.where(
        candidates["_has_momentum_tracking"],
        "active_follow_tracking",
        np.where(
            candidates["_has_swing_strategy"] | candidates["_has_tactical_strategy"],
            "formal_entry_candidate",
            "support_zone_observation",
        ),
    )
    candidates["explain_action_label"] = np.select(
        [
            candidates["_has_momentum_strategy"] & candidates["_has_tactical_strategy"],
            candidates["_has_momentum_strategy"] & candidates["_has_swing_strategy"],
            candidates["_has_swing_strategy"] & candidates["_has_tactical_strategy"],
            momentum_action_clean.eq("MOMENTUM_LIMIT_BUY"),
            momentum_action_clean.eq("MOMENTUM_WAIT_PULLBACK"),
            momentum_action_clean.eq("MOMENTUM_WATCH"),
            candidates["shortwave_action"].eq("NORMAL_ENTRY"),
            candidates["shortwave_action"].eq("SMALL_ENTRY"),
            candidates["_has_tactical_strategy"],
        ],
        [
            "動能+短打",
            "動能+波段",
            "短打+波段",
            "支撐區觀察",
            "等回落觀察",
            "條件式觀察",
            "波段正式進場",
            "波段小倉",
            "短打觀察",
        ],
        default="觀察候選",
    )
    candidates["tradability_status"] = "SHORTWAVE_ADVISORY"
    candidates["tradability_reason"] = np.where(
        candidates["_has_momentum_strategy"] & ~candidates["_has_swing_strategy"] & ~candidates["_has_tactical_strategy"],
        _string_column("momentum_entry_strategy_advice"),
        _string_column("shortwave_entry_strategy_advice"),
    )

    for column in [
        "shortwave_entry_zone_low",
        "shortwave_entry_zone_high",
        "shortwave_preferred_hold_days",
        "shortwave_model_combo_support",
        "shortwave_friend_combo_score",
        "tactical_score",
        "tactical_entry_limit",
        "tactical_entry_zone_low",
        "tactical_entry_zone_high",
        "tactical_stop_loss",
        "tactical_take_profit_2p",
        "tactical_take_profit_4p",
        "tactical_max_hold_days",
        "momentum_score",
        "momentum_entry_limit",
        "momentum_entry_zone_low",
        "momentum_entry_zone_high",
        "momentum_stop_loss",
        "momentum_take_profit_3p",
        "momentum_take_profit_6p",
        "momentum_live_price",
        "momentum_setup_age_days",
        "momentum_max_hold_days",
        "swing_score",
        "swing_entry_zone_low",
        "swing_entry_zone_high",
        "swing_preferred_hold_days",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
    ]:
        if column in candidates.columns:
            candidates[column] = pd.to_numeric(candidates[column], errors="coerce")

    alignment = _string_column("chipk_entry_alignment").str.upper()
    bucket = _string_column("chipk_bucket").str.lower()
    pattern = _string_column("chipk_main_force_pattern").str.lower()
    risk_flags = _string_column("chipk_risk_flags").str.lower()
    has_entry_zone = (
        _numeric_column("tactical_entry_zone_low").notna()
        | _numeric_column("momentum_entry_zone_low").notna()
        | _numeric_column("shortwave_entry_zone_low").notna()
    )
    chipk_veto = (
        alignment.isin(["AVOID", "DO_NOT_CHASE"])
        | bucket.eq("weak")
        | pattern.isin(["distribution_pressure", "short_term_chase_trap"])
        | risk_flags.str.contains("distribution|trap|veto|dump", regex=True)
    )
    actionable_setup = candidates["_has_momentum_strategy"] | candidates["_has_tactical_strategy"] | candidates["_has_swing_strategy"]
    tracking_setup = candidates["_has_momentum_tracking"]

    candidates["entry_review_state"] = np.select(
        [
            chipk_veto,
            actionable_setup & alignment.eq("CONFIRM_ENTRY") & has_entry_zone,
            actionable_setup & alignment.eq("SUPPORTIVE_ENTRY") & has_entry_zone,
            actionable_setup & alignment.eq("WAIT_FOR_TURN") & has_entry_zone,
            actionable_setup & has_entry_zone,
        ],
        [
            "blocked_chipk",
            "priority_observation",
            "observation",
            "wait_chipk_turn",
            "zone_observation",
        ],
        default="watch",
    )
    candidates.loc[
        tracking_setup & momentum_action_clean.eq("MOMENTUM_ACTIVE_NO_NEW_ENTRY") & ~chipk_veto,
        "entry_review_state",
    ] = "active_follow_tracking"
    candidates.loc[
        tracking_setup & momentum_action_clean.eq("MOMENTUM_REVIEW_NO_NEW_ENTRY") & ~chipk_veto,
        "entry_review_state",
    ] = "review_no_new_entry"
    candidates.loc[
        tracking_setup & momentum_action_clean.eq("MOMENTUM_MISSED_NO_CHASE") & ~chipk_veto,
        "entry_review_state",
    ] = "missed_no_chase"
    candidates["entry_review_label"] = np.select(
        [
            candidates["entry_review_state"].eq("blocked_chipk"),
            candidates["entry_review_state"].eq("priority_observation"),
            candidates["entry_review_state"].eq("observation"),
            candidates["entry_review_state"].eq("wait_chipk_turn"),
            candidates["entry_review_state"].eq("zone_observation"),
        ],
        ["籌碼否決", "優先觀察", "觀察", "等籌碼轉強", "區間觀察"],
        default="觀察",
    )
    candidates["entry_review_priority"] = np.select(
        [
            candidates["entry_review_state"].eq("priority_observation"),
            candidates["entry_review_state"].eq("observation"),
            candidates["entry_review_state"].eq("wait_chipk_turn"),
            candidates["entry_review_state"].eq("zone_observation"),
            candidates["entry_review_state"].eq("watch"),
            candidates["entry_review_state"].eq("blocked_chipk"),
        ],
        [0, 1, 2, 3, 6, 9],
        default=7,
    )
    candidates.loc[candidates["entry_review_state"].eq("active_follow_tracking"), "entry_review_priority"] = 4
    candidates.loc[candidates["entry_review_state"].eq("review_no_new_entry"), "entry_review_priority"] = 5
    candidates.loc[candidates["entry_review_state"].eq("missed_no_chase"), "entry_review_priority"] = 8
    candidates["entry_review_reason"] = (
        "流程: 短波段支撐/動能區間 -> 原模型 -> 明確價格帶 -> ChipK veto. "
        + "ChipK="
        + _string_column("chipk_entry_alignment").replace("", "unknown")
        + " / "
        + _string_column("chipk_bucket").replace("", "unknown")
        + " / "
        + _string_column("chipk_main_force_pattern").replace("", "unknown")
        + "; 盤中必須進區間且守住支撐，不追高。"
    )

    candidates.loc[
        candidates["entry_review_state"].eq("active_follow_tracking"),
        "entry_review_label",
    ] = "已發動追蹤"
    candidates.loc[
        candidates["entry_review_state"].eq("active_follow_tracking"),
        "entry_review_reason",
    ] = (
        "已在前幾日觸發動能訊號；若已持有則追蹤區間與停損，"
        "若未持有則等待下一次回落支撐，不列為今天 fresh entry。"
    )
    candidates.loc[
        candidates["entry_review_state"].eq("review_no_new_entry"),
        "entry_review_label",
    ] = "需確認新訊號"
    candidates.loc[
        candidates["entry_review_state"].eq("review_no_new_entry"),
        "entry_review_reason",
    ] = "動能訊號日期不足以確認今天新觸發；先列追蹤，不直接進場。"
    candidates.loc[
        candidates["entry_review_state"].eq("missed_no_chase"),
        "entry_review_label",
    ] = "已錯過不追"
    candidates.loc[
        candidates["entry_review_state"].eq("missed_no_chase"),
        "entry_review_reason",
    ] = "價格已脫離合理進場帶；不追高，等待重新回到支撐區間。"

    sort_cols: list[str] = []
    ascending: list[bool] = []
    for column, is_ascending in [
        ("entry_review_priority", True),
        ("_strategy_priority", True),
        ("momentum_action", True),
        ("momentum_score", False),
        ("tactical_action", True),
        ("tactical_score", False),
        ("shortwave_action", True),
        ("shortwave_score", False),
        ("rank_20d", True),
        ("leaderboard_score", False),
    ]:
        if column in candidates.columns:
            sort_cols.append(column)
            ascending.append(is_ascending)
    if sort_cols:
        candidates = candidates.sort_values(by=sort_cols, ascending=ascending, na_position="last")
    if limit > 0 and not candidates.empty:
        momentum_rows = candidates.loc[candidates["_has_momentum_strategy"]].head(max(1, limit // 2))
        remaining_slots = max(1, limit - len(momentum_rows))
        tactical_rows = candidates.loc[
            candidates["_has_tactical_strategy"] & ~candidates.index.isin(momentum_rows.index)
        ].head(max(1, remaining_slots // 2))
        swing_rows = candidates.loc[
            candidates["_has_swing_strategy"] & ~candidates.index.isin(momentum_rows.index) & ~candidates.index.isin(tactical_rows.index)
        ].head(max(1, limit - len(momentum_rows) - len(tactical_rows)))
        tracking_rows = candidates.loc[
            candidates["_has_momentum_tracking"]
            & ~candidates.index.isin(momentum_rows.index)
            & ~candidates.index.isin(tactical_rows.index)
            & ~candidates.index.isin(swing_rows.index)
        ].head(max(1, limit // 3))
        combined = pd.concat([momentum_rows, tactical_rows, swing_rows, tracking_rows], axis=0)
        if len(combined) < limit:
            filler = candidates.loc[~candidates.index.isin(combined.index)].head(limit - len(combined))
            combined = pd.concat([combined, filler], axis=0)
        candidates = combined
    return candidates.head(limit).reset_index(drop=True)


def _add_signal_explain_columns(df: pd.DataFrame, *, source: str) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    explanations = [explain_signal_row(row, source=source) for row in out.to_dict(orient="records")]
    out["explain_action"] = [item["action"] for item in explanations]
    out["explain_action_label"] = [item["action_label"] for item in explanations]
    out["explain_summary"] = [item["summary"] for item in explanations]
    out["explain_drivers"] = [" / ".join(item["drivers"]) for item in explanations]
    out["explain_warnings"] = [" / ".join(item["warnings"]) for item in explanations]
    return out


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    numeric = _to_float(value)
    return round(numeric, digits) if numeric is not None else None


def _json_safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe_payload(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_csv_path(prefix: str) -> str | None:
    candidates = [
        os.path.join(MODEL_DIR, name)
        for name in os.listdir(MODEL_DIR)
        if name.startswith(prefix) and name.endswith(".csv")
    ]
    return max(candidates, key=os.path.getmtime) if candidates else None


def _load_market_regime() -> dict[str, Any]:
    path = os.path.join(REPORT_DIR, "market_regime_latest.json")
    if not os.path.exists(path):
        return {
            "status": "missing",
            "state": None,
            "action": None,
            "report_path": path,
        }
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["status"] = "ok"
    data["report_path"] = path
    return data


def _load_twii_curve(start_date: str | None, end_date: str | None) -> pd.DataFrame:
    if not start_date or not end_date:
        return pd.DataFrame(columns=["date", "close", "return_pct"])

    path = os.path.join(INDEX_DIR, "index_TWII.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["date", "close", "return_pct"])

    df = pd.read_csv(path, usecols=["Date", "Close"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    window = df[
        (df["Date"] >= pd.Timestamp(start_date))
        & (df["Date"] <= pd.Timestamp(end_date))
    ].copy()
    if window.empty:
        return pd.DataFrame(columns=["date", "close", "return_pct"])

    base_close = float(window["Close"].iloc[0])
    window["date"] = window["Date"].dt.date.astype(str)
    window["return_pct"] = window["Close"] / base_close - 1.0 if base_close > 0 else np.nan
    return window.loc[:, ["date", "Close", "return_pct"]].rename(columns={"Close": "close"})


def _load_ticker_ma5_context(ticker: str) -> dict[str, Any]:
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return {"latest_trade_date": None, "ma5": None, "latest_close": None}

    try:
        df = pd.read_csv(path, usecols=["Date", "Close"])
    except Exception:
        return {"latest_trade_date": None, "ma5": None, "latest_close": None}

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    if df.empty:
        return {"latest_trade_date": None, "ma5": None, "latest_close": None}

    df["ma5"] = df["Close"].rolling(5, min_periods=5).mean()
    latest = df.iloc[-1]
    return {
        "latest_trade_date": latest["Date"].date().isoformat(),
        "ma5": _round_or_none(latest.get("ma5"), 4),
        "latest_close": _round_or_none(latest.get("Close"), 4),
    }


def _risk_state(ma5_distance_pct: float | None, hwm_drawdown_pct: float | None) -> str:
    if ma5_distance_pct is not None and ma5_distance_pct < 0:
        return "red"
    if hwm_drawdown_pct is not None and hwm_drawdown_pct <= -0.08:
        return "red"
    if ma5_distance_pct is not None and ma5_distance_pct < 0.02:
        return "yellow"
    if hwm_drawdown_pct is not None and hwm_drawdown_pct <= -0.06:
        return "yellow"
    return "green"


def _query_book_frames(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not os.path.exists(db_path):
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )

    with _connect(db_path) as conn:
        positions = pd.read_sql_query(
            """
            SELECT
                p.*,
                r.rule_version,
                r.signals_file_path
            FROM unified_positions p
            JOIN unified_runs r ON r.id = p.opened_by_run_id
            WHERE p.prediction_date >= ?
            ORDER BY p.prediction_date, p.ticker
            """,
            conn,
            params=(PORTFOLIO_START_DATE,),
        )
        marks = pd.read_sql_query(
            """
            SELECT
                m.position_id,
                m.mark_date,
                m.trading_day_number,
                m.open,
                m.high,
                m.low,
                m.close,
                m.close_return_pct,
                p.ticker,
                p.prediction_date,
                p.entry_date,
                p.exit_date,
                p.target_weight
            FROM unified_marks m
            JOIN unified_positions p ON p.id = m.position_id
            WHERE p.prediction_date >= ?
            ORDER BY m.mark_date, p.ticker
            """,
            conn,
            params=(PORTFOLIO_START_DATE,),
        )
        runs = pd.read_sql_query(
            """
            SELECT *
            FROM unified_runs
            WHERE prediction_date >= ?
            ORDER BY prediction_date
            """,
            conn,
            params=(PORTFOLIO_START_DATE,),
        )

    return positions, marks, runs


def _latest_exit_policy_counts(
    positions: pd.DataFrame,
    latest_run_date: str | None,
) -> list[dict[str, Any]]:
    if positions.empty or not latest_run_date:
        return []
    window = positions.loc[
        positions["prediction_date"].astype(str) == str(latest_run_date)
    ].copy()
    if window.empty or "exit_policy" not in window.columns:
        return []
    counts = window["exit_policy"].fillna("").astype(str).value_counts()
    return [
        {"exit_policy": str(name), "position_count": int(count)}
        for name, count in counts.items()
        if str(name).strip()
    ]


def _build_book_curve(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
) -> list[dict[str, Any]]:
    if positions.empty:
        return []

    date_candidates = []
    for column in ["prediction_date", "entry_date", "exit_date", "last_mark_date"]:
        if column in positions.columns:
            date_candidates.extend(
                positions[column].dropna().astype(str).tolist()
            )
    if not marks.empty:
        date_candidates.extend(marks["mark_date"].dropna().astype(str).tolist())
    if not date_candidates:
        return []

    start_date = min(date_candidates)
    end_date = max(date_candidates)
    twii_curve = _load_twii_curve(start_date, end_date)
    if not twii_curve.empty:
        date_index = twii_curve["date"].tolist()
    else:
        date_index = [
            value.date().isoformat()
            for value in pd.bdate_range(start=start_date, end=end_date)
        ]

    marks = marks.copy()
    if not marks.empty:
        marks["mark_date"] = marks["mark_date"].astype(str)
        marks["close_return_pct"] = pd.to_numeric(marks["close_return_pct"], errors="coerce")
    positions = positions.copy()
    positions["prediction_date"] = positions["prediction_date"].astype(str)
    for column in ["entry_date", "exit_date", "status"]:
        if column in positions.columns:
            positions[column] = positions[column].astype(str)
    for column in ["target_weight", "realized_return_pct"]:
        if column in positions.columns:
            positions[column] = pd.to_numeric(positions[column], errors="coerce")

    marks_by_position: dict[int, list[dict[str, Any]]] = {}
    for row in marks.to_dict(orient="records"):
        pos_id = int(row["position_id"])
        marks_by_position.setdefault(pos_id, []).append(row)

    positions_by_prediction: dict[str, list[dict[str, Any]]] = {}
    exits_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in positions.to_dict(orient="records"):
        positions_by_prediction.setdefault(str(row["prediction_date"]), []).append(row)
        exit_date = row.get("exit_date")
        if exit_date and exit_date != "nan" and exit_date != "None":
            exits_by_date.setdefault(str(exit_date), []).append(row)

    active_states: dict[int, dict[str, Any]] = {}
    cash = 1.0
    curve: list[dict[str, Any]] = []

    for trade_date in date_index:
        for row in positions_by_prediction.get(trade_date, []):
            pos_id = int(row["id"])
            weight = float(row.get("target_weight") or 0.0)
            if pos_id in active_states or weight <= 0:
                continue
            cash -= weight
            active_states[pos_id] = {
                "row": row,
                "weight": weight,
            }

        for row in exits_by_date.get(trade_date, []):
            pos_id = int(row["id"])
            state = active_states.pop(pos_id, None)
            if state is None:
                continue
            weight = state["weight"]
            realized = float(row.get("realized_return_pct") or 0.0)
            cash += weight * (1.0 + realized)

        equity = cash
        for pos_id, state in active_states.items():
            row = state["row"]
            weight = state["weight"]
            status = str(row.get("status") or "")
            entry_date = str(row.get("entry_date") or "")
            if status == "missed_entry_gap_up":
                equity += weight
                continue
            if not entry_date or entry_date == "nan" or trade_date < entry_date:
                equity += weight
                continue

            mark_rows = marks_by_position.get(pos_id, [])
            latest_return = None
            for mark_row in mark_rows:
                if str(mark_row["mark_date"]) <= trade_date:
                    latest_return = _to_float(mark_row.get("close_return_pct"))
                else:
                    break
            if latest_return is None:
                equity += weight
            else:
                equity += weight * (1.0 + latest_return)

        curve.append(
            {
                "date": trade_date,
                "equity": round(equity, 6),
                "return_pct": round((equity - 1.0) * 100.0, 2),
            }
        )

    return curve


def _max_drawdown_from_curve(curve: list[dict[str, Any]]) -> float | None:
    if not curve:
        return None
    equity = pd.Series([float(row["equity"]) for row in curve], dtype="float64")
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return _round_or_none(drawdown.min() * 100.0, 2)


def _build_book_payload(
    label: str,
    db_path: str,
    expected_exit_policy: str,
) -> dict[str, Any]:
    if not os.path.exists(db_path):
        return {
            "label": label,
            "db_path": db_path,
            "exists": False,
            "expected_exit_policy": expected_exit_policy,
            "summary": {},
            "active_positions": [],
            "recent_exits": [],
            "curve": [],
        }

    positions, marks, runs = _query_book_frames(db_path)
    if positions.empty:
        return {
            "label": label,
            "db_path": db_path,
            "exists": True,
            "expected_exit_policy": expected_exit_policy,
            "summary": {},
            "active_positions": [],
            "recent_exits": [],
            "curve": [],
        }

    names = _load_name_lookup()
    positions["ticker"] = positions["ticker"].astype(str)
    positions["name"] = positions["ticker"].map(names).fillna("")
    positions["target_weight"] = pd.to_numeric(positions["target_weight"], errors="coerce").fillna(0.0)
    positions["days_observed"] = pd.to_numeric(positions["days_observed"], errors="coerce")
    positions["realized_return_pct"] = pd.to_numeric(positions["realized_return_pct"], errors="coerce")

    latest_run_date = (
        str(runs["prediction_date"].astype(str).max())
        if not runs.empty
        else None
    )
    latest_mark_date = None
    if not marks.empty:
        latest_mark_date = str(marks["mark_date"].astype(str).max())
        marks["position_id"] = pd.to_numeric(marks["position_id"], errors="coerce").astype("Int64")
        marks["close"] = pd.to_numeric(marks["close"], errors="coerce")
        marks["close_return_pct"] = pd.to_numeric(marks["close_return_pct"], errors="coerce")

    latest_marks = (
        marks.sort_values(["position_id", "mark_date"])
        .groupby("position_id", as_index=False)
        .tail(1)
        .loc[:, ["position_id", "mark_date", "close", "close_return_pct"]]
        .rename(
            columns={
                "mark_date": "latest_mark_date",
                "close": "latest_price",
                "close_return_pct": "latest_close_return_pct",
            }
        )
        if not marks.empty
        else pd.DataFrame(columns=["position_id", "latest_mark_date", "latest_price", "latest_close_return_pct"])
    )
    high_water = (
        marks.groupby("position_id", as_index=False)["close"]
        .max()
        .rename(columns={"close": "high_water_close"})
        if not marks.empty
        else pd.DataFrame(columns=["position_id", "high_water_close"])
    )

    book = positions.merge(latest_marks, left_on="id", right_on="position_id", how="left")
    book = book.merge(high_water, left_on="id", right_on="position_id", how="left", suffixes=("", "_high"))
    for column in ["latest_price", "latest_close_return_pct", "high_water_close"]:
        if column in book.columns:
            book[column] = pd.to_numeric(book[column], errors="coerce")

    active = book.loc[book["status"].isin(["pending", "open"])].copy()
    closed = book.loc[book["status"].isin(["closed", "stopped_out"])].copy()

    active["target_weight_pct"] = (active["target_weight"] * 100.0).round(2)
    active["unrealized_pnl_pct"] = (
        active["latest_close_return_pct"].fillna(0.0) * 100.0
    ).round(2)
    active["days_held"] = pd.to_numeric(active["days_observed"], errors="coerce").fillna(0).astype(int)
    active["drawdown_from_high_pct"] = np.where(
        active["high_water_close"].gt(0) & active["latest_price"].gt(0),
        (active["latest_price"] / active["high_water_close"] - 1.0) * 100.0,
        np.nan,
    )

    price_context = {
        ticker: _load_ticker_ma5_context(ticker)
        for ticker in active["ticker"].astype(str).unique().tolist()
    }
    active["ma5"] = active["ticker"].map(
        lambda value: price_context.get(str(value), {}).get("ma5")
    )
    active["latest_trade_date"] = active["ticker"].map(
        lambda value: price_context.get(str(value), {}).get("latest_trade_date")
    )
    active["ma5_distance_pct"] = np.where(
        pd.to_numeric(active["ma5"], errors="coerce").gt(0)
        & pd.to_numeric(active["latest_price"], errors="coerce").gt(0),
        (active["latest_price"] / active["ma5"] - 1.0) * 100.0,
        np.nan,
    )
    active["risk_state"] = active.apply(
        lambda row: _risk_state(
            _to_float(row.get("ma5_distance_pct")) / 100.0
            if _to_float(row.get("ma5_distance_pct")) is not None
            else None,
            _to_float(row.get("drawdown_from_high_pct")) / 100.0
            if _to_float(row.get("drawdown_from_high_pct")) is not None
            else None,
        ),
        axis=1,
    )

    closed["target_weight_pct"] = (closed["target_weight"] * 100.0).round(2)
    closed["realized_return_pct"] = (closed["realized_return_pct"] * 100.0).round(2)
    closed["max_drawdown_pct"] = (
        pd.to_numeric(closed["max_drawdown_pct"], errors="coerce") * 100.0
    ).round(2)
    closed["days_held"] = pd.to_numeric(closed["days_observed"], errors="coerce").fillna(0).astype(int)

    curve = _build_book_curve(positions, marks)
    mtm_return_pct = curve[-1]["return_pct"] if curve else None

    weighted_hold_days = None
    hold_days_frame = book.loc[
        book["entry_date"].notna()
        & book["target_weight"].gt(0)
        & pd.to_numeric(book["days_observed"], errors="coerce").notna()
    ].copy()
    if not hold_days_frame.empty:
        weight_sum = float(hold_days_frame["target_weight"].sum())
        if weight_sum > 0:
            weighted_hold_days = float(
                (hold_days_frame["days_observed"] * hold_days_frame["target_weight"]).sum()
                / weight_sum
            )

    realized_win_rate_pct = None
    if not closed.empty and closed["realized_return_pct"].notna().any():
        realized_win_rate_pct = round(float((closed["realized_return_pct"] > 0).mean() * 100.0), 2)

    summary = {
        "latest_run_date": latest_run_date,
        "latest_signal_date": str(book["last_signal_date"].dropna().astype(str).max())
        if "last_signal_date" in book.columns and book["last_signal_date"].notna().any()
        else None,
        "latest_mark_date": latest_mark_date,
        "latest_rule_version": str(runs["rule_version"].iloc[-1]) if not runs.empty else None,
        "latest_run_signal_file": (
            os.path.basename(str(runs["signals_file_path"].iloc[-1]))
            if not runs.empty and "signals_file_path" in runs.columns
            else None
        ),
        "latest_run_exit_policies": _latest_exit_policy_counts(positions, latest_run_date),
        "total_runs": int(len(runs)),
        "total_positions": int(len(book)),
        "active_positions": int(len(active)),
        "pending_positions": int((book["status"] == "pending").sum()),
        "open_positions": int((book["status"] == "open").sum()),
        "closed_positions": int((book["status"] == "closed").sum()),
        "stopped_out_positions": int((book["status"] == "stopped_out").sum()),
        "invested_weight_pct": round(float(active["target_weight"].sum() * 100.0), 2),
        "avg_open_return_pct": _round_or_none(active["unrealized_pnl_pct"].mean(), 2),
        "avg_closed_return_pct": _round_or_none(closed["realized_return_pct"].mean(), 2),
        "realized_win_rate_pct": realized_win_rate_pct,
        "capital_weighted_hold_days": _round_or_none(weighted_hold_days, 2),
        "capital_turnover_20d_multiple": (
            round(TWENTY_DAY_HOLD / weighted_hold_days, 2)
            if weighted_hold_days and weighted_hold_days > 0
            else None
        ),
        "mtm_return_pct": mtm_return_pct,
        "max_drawdown_pct": _max_drawdown_from_curve(curve),
    }

    active_cols = [
        "prediction_date",
        "ticker",
        "name",
        "sector",
        "current_signal_type",
        "status",
        "target_weight_pct",
        "entry_date",
        "entry_price",
        "latest_mark_date",
        "latest_price",
        "days_held",
        "unrealized_pnl_pct",
        "ma5",
        "ma5_distance_pct",
        "high_water_close",
        "drawdown_from_high_pct",
        "exit_policy",
        "risk_state",
    ]
    active = active.loc[:, [column for column in active_cols if column in active.columns]].rename(
        columns={"current_signal_type": "signal_type"}
    )
    for column in [
        "target_weight_pct",
        "entry_price",
        "latest_price",
        "unrealized_pnl_pct",
        "ma5",
        "ma5_distance_pct",
        "high_water_close",
        "drawdown_from_high_pct",
    ]:
        if column in active.columns:
            active[column] = pd.to_numeric(active[column], errors="coerce").round(2)
    active = active.sort_values(
        by=["risk_state", "ma5_distance_pct", "drawdown_from_high_pct", "target_weight_pct"],
        ascending=[True, True, True, False],
    )

    exit_cols = [
        "exit_date",
        "ticker",
        "name",
        "sector",
        "current_signal_type",
        "target_weight_pct",
        "exit_policy",
        "exit_reason",
        "realized_return_pct",
        "max_drawdown_pct",
        "days_held",
        "status",
    ]
    recent_exits = closed.loc[:, [column for column in exit_cols if column in closed.columns]].rename(
        columns={"current_signal_type": "signal_type"}
    )
    recent_exits = recent_exits.sort_values(
        by=["exit_date", "realized_return_pct"],
        ascending=[False, False],
    ).head(30)

    return {
        "label": label,
        "db_path": db_path,
        "exists": True,
        "expected_exit_policy": expected_exit_policy,
        "summary": summary,
        "active_positions": _safe_records(active),
        "recent_exits": _safe_records(recent_exits),
        "curve": curve,
    }


def build_dashboard_summary() -> dict[str, Any]:
    champion = _build_book_payload(
        label="Two-Stage Champion",
        db_path=CHAMPION_DB_PATH,
        expected_exit_policy="asymmetric_v2",
    )
    shadow = _build_book_payload(
        label="Baseline Shadow",
        db_path=SHADOW_DB_PATH,
        expected_exit_policy="baseline_with_ma20",
    )

    all_dates = []
    for curve in [champion.get("curve", []), shadow.get("curve", [])]:
        if curve:
            all_dates.extend(point["date"] for point in curve)
    start_date = min(all_dates) if all_dates else PORTFOLIO_START_DATE
    end_date = max(all_dates) if all_dates else None
    twii_curve_df = _load_twii_curve(start_date, end_date) if end_date else pd.DataFrame()

    champion_mtm = champion.get("summary", {}).get("mtm_return_pct")
    shadow_mtm = shadow.get("summary", {}).get("mtm_return_pct")
    twii_mtm = (
        _round_or_none(twii_curve_df["return_pct"].iloc[-1] * 100.0, 2)
        if not twii_curve_df.empty
        else None
    )
    if twii_mtm is not None:
        champion["summary"]["mtm_alpha_vs_twii_pct"] = (
            _round_or_none(champion_mtm - twii_mtm, 2)
            if champion_mtm is not None
            else None
        )
        shadow["summary"]["mtm_alpha_vs_twii_pct"] = (
            _round_or_none(shadow_mtm - twii_mtm, 2)
            if shadow_mtm is not None
            else None
        )

    twii_payload: list[dict[str, Any]] = []
    if not twii_curve_df.empty:
        twii_view = twii_curve_df.copy()
        twii_view["return_pct"] = (
            pd.to_numeric(twii_view["return_pct"], errors="coerce") * 100.0
        ).round(2)
        twii_payload = _safe_records(twii_view.loc[:, ["date", "close", "return_pct"]])

    chart = {
        "start_date": start_date,
        "end_date": end_date,
        "twii": twii_payload,
        "champion": champion.get("curve", []),
        "shadow": shadow.get("curve", []),
    }

    return _json_safe_payload({
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "as_of_date": end_date,
        "benchmark_contract": build_trade_alpha_benchmark_contract(
            start_date=start_date,
            end_date=end_date,
        ),
        "market_regime": _load_market_regime(),
        "champion": champion.get("summary", {}),
        "shadow": shadow.get("summary", {}),
        "market": {
            "twii_return_pct": twii_mtm,
        },
        "chart": chart,
    })


def build_portfolio_champion_vs_shadow() -> dict[str, Any]:
    champion = _build_book_payload(
        label="Two-Stage Champion",
        db_path=CHAMPION_DB_PATH,
        expected_exit_policy="asymmetric_v2",
    )
    shadow = _build_book_payload(
        label="Baseline Shadow",
        db_path=SHADOW_DB_PATH,
        expected_exit_policy="baseline_with_ma20",
    )

    champion_active = pd.DataFrame(champion.get("active_positions", []))
    shadow_active = pd.DataFrame(shadow.get("active_positions", []))
    champion_exits = pd.DataFrame(champion.get("recent_exits", []))

    opportunity = []
    if not champion_exits.empty and not shadow_active.empty:
        latest_champion_exit = (
            champion_exits.sort_values(["exit_date"], ascending=False)
            .drop_duplicates(subset=["ticker"], keep="first")
        )
        shadow_active_view = shadow_active.loc[
            :, [column for column in ["ticker", "name", "unrealized_pnl_pct", "days_held", "risk_state"] if column in shadow_active.columns]
        ]
        compare = latest_champion_exit.merge(
            shadow_active_view,
            on=["ticker"],
            how="inner",
            suffixes=("_champion", "_shadow"),
        )
        if not compare.empty:
            opportunity = _safe_records(
                compare.loc[
                    :,
                    [
                        column
                        for column in [
                            "ticker",
                            "name_champion",
                            "exit_date",
                            "exit_reason",
                            "realized_return_pct",
                            "unrealized_pnl_pct",
                            "days_held_shadow",
                            "risk_state",
                        ]
                        if column in compare.columns
                    ],
                ].rename(
                    columns={
                        "name_champion": "name",
                        "realized_return_pct": "champion_realized_return_pct",
                        "unrealized_pnl_pct": "shadow_unrealized_pnl_pct",
                        "days_held_shadow": "shadow_days_held",
                    }
                )
            )

    overlap = {
        "shared_tickers": sorted(
            set(champion_active.get("ticker", pd.Series(dtype="object")).astype(str))
            & set(shadow_active.get("ticker", pd.Series(dtype="object")).astype(str))
        ),
        "champion_only": sorted(
            set(champion_active.get("ticker", pd.Series(dtype="object")).astype(str))
            - set(shadow_active.get("ticker", pd.Series(dtype="object")).astype(str))
        ),
        "shadow_only": sorted(
            set(shadow_active.get("ticker", pd.Series(dtype="object")).astype(str))
            - set(champion_active.get("ticker", pd.Series(dtype="object")).astype(str))
        ),
    }

    return _json_safe_payload({
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "as_of_date": max(
            value
            for value in [
                champion.get("summary", {}).get("latest_mark_date"),
                shadow.get("summary", {}).get("latest_mark_date"),
                champion.get("summary", {}).get("latest_signal_date"),
            ]
            if value
        )
        if any(
            value
            for value in [
                champion.get("summary", {}).get("latest_mark_date"),
                shadow.get("summary", {}).get("latest_mark_date"),
                champion.get("summary", {}).get("latest_signal_date"),
            ]
        )
        else None,
        "benchmark_contract": build_trade_alpha_benchmark_contract(
            start_date=champion.get("summary", {}).get("latest_run_date") or PORTFOLIO_START_DATE,
            end_date=max(
                value
                for value in [
                    champion.get("summary", {}).get("latest_mark_date"),
                    shadow.get("summary", {}).get("latest_mark_date"),
                    champion.get("summary", {}).get("latest_signal_date"),
                ]
                if value
            )
            if any(
                value
                for value in [
                    champion.get("summary", {}).get("latest_mark_date"),
                    shadow.get("summary", {}).get("latest_mark_date"),
                    champion.get("summary", {}).get("latest_signal_date"),
                ]
            )
            else None,
        ),
        "champion": {
            "summary": champion.get("summary", {}),
            "active_positions": champion.get("active_positions", []),
            "recent_exits": champion.get("recent_exits", []),
        },
        "shadow": {
            "summary": shadow.get("summary", {}),
            "active_positions": shadow.get("active_positions", []),
            "recent_exits": shadow.get("recent_exits", []),
        },
        "opportunity_cost": opportunity,
        "overlap": overlap,
    })


def build_unified_signals_latest_feed() -> dict[str, Any]:
    signal_path = _latest_csv_path(LATEST_UNIFIED_RE)
    prediction_path = _latest_csv_path(LATEST_PREDICTION_RE)
    if signal_path is None:
        return {
            "generated_at": pd.Timestamp.utcnow().isoformat(),
            "status": "missing",
            "message": "No unified signal artifact found.",
            "selected": [],
            "rejected": [],
            "hard_blocks": [],
        }

    names = _load_name_lookup()
    signal_df = pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})
    if "ticker" in signal_df.columns:
        signal_df["name"] = signal_df["ticker"].map(names).fillna("")

    for column in [
        "target_units",
        "target_weight_ratio",
        "close_ref",
        "rank_20d",
        "pred_return_20d",
        "penalty_overlay_score",
        "penalty_overlay_total",
        "penalty_foreign_selling",
        "penalty_gap_risk",
        "penalty_quality",
        "penalty_beta",
    ]:
        if column in signal_df.columns:
            signal_df[column] = pd.to_numeric(signal_df[column], errors="coerce")

    signal_df["target_weight_pct"] = (
        pd.to_numeric(signal_df.get("target_weight_ratio"), errors="coerce") * 100.0
    ).round(2)
    signal_df["base_score"] = (pd.to_numeric(signal_df.get("pred_return_20d"), errors="coerce") * 100.0).round(2)
    signal_df["net_score"] = (
        pd.to_numeric(signal_df.get("penalty_overlay_score"), errors="coerce") * 100.0
    ).round(2)
    for column in [
        "penalty_overlay_total",
        "penalty_foreign_selling",
        "penalty_gap_risk",
        "penalty_quality",
        "penalty_beta",
    ]:
        if column in signal_df.columns:
            signal_df[column] = (signal_df[column] * 100.0).round(2)

    selected = signal_df.loc[
        pd.to_numeric(signal_df.get("target_units"), errors="coerce").fillna(0).gt(0)
    ].copy()
    selected = selected.sort_values(
        by=["target_weight_pct", "rank_20d", "net_score"],
        ascending=[False, True, False],
    )
    selected = _add_signal_explain_columns(selected, source="unified")

    rejected = signal_df.loc[
        signal_df.get("tradability_blocked", False).fillna(False).astype(bool)
        | pd.to_numeric(signal_df.get("target_units"), errors="coerce").fillna(0).eq(0)
    ].copy()
    if not rejected.empty:
        rejected["rejected_reason"] = (
            rejected.get("tradability_reason", "").fillna("").astype(str).str.strip()
        )
        rejected = rejected.sort_values(by=["base_score"], ascending=False)
        rejected = _add_signal_explain_columns(rejected, source="unified")

    hard_blocks = pd.DataFrame()
    if prediction_path and os.path.exists(prediction_path):
        prediction_df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
        prediction_df["name"] = prediction_df["ticker"].map(names).fillna("")
        prediction_df = apply_penalty_overlay(prediction_df, version="v1")
        hard_blocks = prediction_df.loc[
            prediction_df["penalty_hard_block"].fillna(False).astype(bool)
        ].copy()
        if not hard_blocks.empty:
            hard_blocks["base_score"] = (
                pd.to_numeric(hard_blocks.get("pred_return_20d"), errors="coerce") * 100.0
            ).round(2)
            hard_blocks["net_score"] = (
                pd.to_numeric(hard_blocks.get("penalty_overlay_score"), errors="coerce") * 100.0
            ).round(2)
            hard_blocks = hard_blocks.sort_values(by=["base_score"], ascending=False)
            hard_blocks = _add_signal_explain_columns(hard_blocks, source="prediction")

    shortwave_candidates = _shortwave_candidate_rows(prediction_path, names)

    strategy_cols = [
        "strategy_lane",
        "tactical_strategy_name",
        "tactical_version",
        "tactical_action",
        "tactical_score",
        "tactical_reason",
        "tactical_missing_strategy_tags",
        "tactical_entry_strategy_advice",
        "tactical_entry_limit",
        "tactical_entry_zone_low",
        "tactical_entry_zone_high",
        "tactical_stop_loss",
        "tactical_take_profit_2p",
        "tactical_take_profit_4p",
        "tactical_max_hold_days",
        "tactical_position_size_hint",
        "tactical_backtest_ref",
        "momentum_strategy_name",
        "momentum_version",
        "momentum_action",
        "momentum_score",
        "momentum_reason",
        "momentum_missing_strategy_tags",
        "momentum_entry_strategy_advice",
        "momentum_entry_limit",
        "momentum_entry_zone_low",
        "momentum_entry_zone_high",
        "momentum_stop_loss",
        "momentum_take_profit_3p",
        "momentum_take_profit_6p",
        "momentum_live_price",
        "momentum_price_status",
        "momentum_setup_age_days",
        "momentum_first_signal_date",
        "momentum_signal_phase",
        "momentum_max_hold_days",
        "momentum_position_size_hint",
        "momentum_backtest_ref",
        "swing_strategy_name",
        "swing_action",
        "swing_score",
        "swing_entry_zone_low",
        "swing_entry_zone_high",
        "swing_entry_zone_status",
        "swing_entry_strategy_advice",
        "swing_preferred_hold_days",
        "swing_reason",
        "swing_missing_strategy_tags",
    ]
    selected_cols = [
        "prediction_date",
        "ticker",
        "name",
        "sector",
        "signal_type",
        "target_units",
        "target_weight_pct",
        "rank_20d",
        "close_ref",
        "base_score",
        "penalty_foreign_selling",
        "penalty_gap_risk",
        "penalty_quality",
        "penalty_beta",
        "penalty_overlay_total",
        "net_score",
        "penalty_overlay_reason",
        *strategy_cols,
        "explain_action",
        "explain_action_label",
        "explain_summary",
        "explain_drivers",
        "explain_warnings",
    ]
    shortwave_cols = [
        "date",
        "ticker",
        "name",
        "sector",
        "signal_type",
        "recommendation",
        "target_weight_pct",
        "rank_20d",
        "close_ref",
        "base_score",
        "net_score",
        "shortwave_action",
        "shortwave_score",
        "shortwave_strategy_tags",
        "shortwave_missing_strategy_tags",
        "shortwave_entry_strategy_advice",
        "shortwave_entry_zone_low",
        "shortwave_entry_zone_high",
        "shortwave_entry_zone_status",
        "shortwave_entry_zone_note",
        "shortwave_preferred_hold_days",
        "shortwave_model_combo_support",
        "shortwave_friend_combo_score",
        "candidate_scope",
        "entry_review_state",
        "entry_review_label",
        "entry_review_priority",
        "entry_review_reason",
        "chipk_asof_date",
        "chipk_bucket",
        "chipk_main_force_pattern",
        "chipk_entry_alignment",
        "chipk_trap_risk",
        "chipk_distribution_risk",
        "chipk_risk_flags",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
        *strategy_cols,
        "explain_action",
        "explain_action_label",
        "explain_summary",
        "explain_drivers",
        "explain_warnings",
        "tradability_status",
        "tradability_reason",
    ]
    rejected_cols = [
        "prediction_date",
        "ticker",
        "name",
        "sector",
        "signal_type",
        "base_score",
        "rejected_reason",
        "tradability_status",
        "tradability_reason",
        "explain_action",
        "explain_action_label",
        "explain_summary",
        "explain_drivers",
        "explain_warnings",
    ]
    hard_block_cols = [
        "ticker",
        "name",
        "sector",
        "base_score",
        "net_score",
        "penalty_overlay_reason",
        "risk_tags",
        "explain_action",
        "explain_action_label",
        "explain_summary",
        "explain_drivers",
        "explain_warnings",
    ]

    prediction_date = None
    if not selected.empty and "prediction_date" in selected.columns:
        prediction_date = str(selected["prediction_date"].iloc[0])
    elif not signal_df.empty and "prediction_date" in signal_df.columns:
        prediction_date = str(signal_df["prediction_date"].iloc[0])
    elif not shortwave_candidates.empty and "date" in shortwave_candidates.columns:
        prediction_date = str(shortwave_candidates["date"].iloc[0])

    tactical_candidate_count = 0
    swing_candidate_count = 0
    momentum_candidate_count = 0
    tracking_candidate_count = 0
    if not shortwave_candidates.empty:
        if "tactical_action" in shortwave_candidates.columns:
            tactical_candidate_count = int(
                shortwave_candidates["tactical_action"]
                .fillna("")
                .astype(str)
                .isin(["TACTICAL_LIMIT_BUY", "TACTICAL_WAIT_PULLBACK", "TACTICAL_WATCH"])
                .sum()
            )
        if "momentum_action" in shortwave_candidates.columns:
            momentum_candidate_count = int(
                shortwave_candidates["momentum_action"]
                .fillna("")
                .astype(str)
                .isin(["MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK", "MOMENTUM_WATCH"])
                .sum()
            )
            tracking_candidate_count = int(
                shortwave_candidates["momentum_action"]
                .fillna("")
                .astype(str)
                .isin(["MOMENTUM_ACTIVE_NO_NEW_ENTRY", "MOMENTUM_REVIEW_NO_NEW_ENTRY", "MOMENTUM_MISSED_NO_CHASE"])
                .sum()
            )
        if "shortwave_action" in shortwave_candidates.columns:
            swing_candidate_count = int(
                shortwave_candidates["shortwave_action"]
                .fillna("")
                .astype(str)
                .isin(["NORMAL_ENTRY", "SMALL_ENTRY"])
                .sum()
            )

    return _json_safe_payload({
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "status": "ok",
        "prediction_date": prediction_date,
        "source_signal_path": signal_path,
        "source_prediction_path": prediction_path,
        "selected_count": int(len(selected)),
        "rejected_count": int(len(rejected)),
        "hard_block_count": int(len(hard_blocks)),
        "shortwave_candidate_count": int(len(shortwave_candidates)),
        "tactical_candidate_count": tactical_candidate_count,
        "momentum_candidate_count": momentum_candidate_count,
        "swing_candidate_count": swing_candidate_count,
        "tracking_candidate_count": tracking_candidate_count,
        "selected": _safe_records(selected.loc[:, [column for column in selected_cols if column in selected.columns]].head(30)),
        "shortwave_candidates": _safe_records(shortwave_candidates.loc[:, [column for column in shortwave_cols if column in shortwave_candidates.columns]].head(60)),
        "rejected": _safe_records(rejected.loc[:, [column for column in rejected_cols if column in rejected.columns]].head(30)),
        "hard_blocks": _safe_records(hard_blocks.loc[:, [column for column in hard_block_cols if column in hard_blocks.columns]].head(30)),
    })


def export_warroom_json_feeds() -> dict[str, str]:
    os.makedirs(REPORT_DIR, exist_ok=True)
    payloads = {
        "dashboard_summary": build_dashboard_summary(),
        "portfolio_champion_vs_shadow": build_portfolio_champion_vs_shadow(),
        "unified_signals_latest": build_unified_signals_latest_feed(),
    }
    written: dict[str, str] = {}
    for key, payload in payloads.items():
        path = REPORT_PATHS[key]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        written[key] = path
    return written
