"""REQ-029 CAUTION regime whipsaw exit overlay CL3 backtest.

Control and treatment share the same Two-Stage N=75 OOF Top30 entries.
Only the exit behaviour changes when market_regime_proxy == CAUTION.

Control : asymmetric_v2 (no change)
Variant A: min_hold_3d   – CAUTION only: suppress MA5_BREAK if hold_days < 3
Variant B: ma5_confirm_2d – CAUTION only: MA5_BREAK needs 2 consecutive days
Variant C: reentry_lock_3d – after any MA5_BREAK, same ticker locked 3 calendar days

Regime proxy (no breadth computation):
  CAUTION : TWII > MA60 AND (TWII < MA20 OR slope_5d < 0)
  OPEN    : TWII >= MA20 AND slope_5d >= 0 AND TWII > MA60
  CLOSED  : TWII < MA60
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR  # noqa: E402
from scripts.backtest_exit_v2_cl3 import (  # noqa: E402
    _calmar,
    _load_price_history,
    _load_twii,
    _max_drawdown,
    _safe_float,
    _sharpe,
    _signal_snapshot,
    _summarize,
)
from scripts.exit_policies import (  # noqa: E402
    EXIT_POLICY_ASYMMETRIC_V2,
    EXIT_REASON_MA5_BREAK,
    ExitManager,
    is_asymmetric_v2_strong_trend,
)
from scripts.order_simulation import (  # noqa: E402
    EXIT_REASON_TIME_20D,
    FILL_STATUS_FILLED,
    simulate_20d_entry,
    simulate_intraday_stop,
)

CONTROL = "Control_AsymmetricV2"
VAR_A = "Variant_A_min_hold_3d"
VAR_B = "Variant_B_ma5_confirm_2d"
VAR_C = "Variant_C_reentry_lock_3d"

REENTRY_LOCK_DAYS = 3
MIN_HOLD_PROTECTION = 3
MA5_CONFIRM_DAYS = 2

STATE_OPEN = "OPEN"
STATE_CAUTION = "CAUTION"
STATE_CLOSED = "CLOSED"


def _fmt_pct(v: Any) -> str:
    n = _safe_float(v)
    return "n/a" if n is None else f"{n * 100:.2f}%"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _build_twii_regime(twii: pd.DataFrame) -> pd.DataFrame:
    """Add regime_proxy column to TWII dataframe."""
    t = twii.copy()
    t["MA20"] = t["Close"].rolling(20, min_periods=20).mean()
    t["MA60"] = t["Close"].rolling(60, min_periods=60).mean()
    t["slope5"] = t["Close"].rolling(5, min_periods=5).apply(
        lambda x: x.iloc[-1] / x.iloc[0] - 1.0, raw=False
    )

    def _regime(row: pd.Series) -> str:
        c, ma20, ma60, slope = row["Close"], row["MA20"], row["MA60"], row["slope5"]
        if any(pd.isna(v) for v in [c, ma20, ma60, slope]):
            return STATE_CLOSED
        if c < ma60:
            return STATE_CLOSED
        if c >= ma20 and slope >= 0:
            return STATE_OPEN
        return STATE_CAUTION

    t["regime_proxy"] = t.apply(_regime, axis=1)
    return t[["Date", "regime_proxy"]]


def _get_regime(regime_map: dict[str, str], prediction_date: str) -> str:
    return regime_map.get(prediction_date, STATE_CLOSED)


def _simulate_control(
    *,
    ticker: str,
    prediction_date: str,
    month: str,
    rank: int,
    price_history: pd.DataFrame,
    twii: pd.DataFrame,
) -> dict[str, Any] | None:
    """Simulate asymmetric_v2 (no change)."""
    ref_price, price_vs_ma20 = _signal_snapshot(price_history, prediction_date)
    if ref_price is None:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(ref_price, entry_row["Open"], entry_row["High"])
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None

    ep = float(entry.fill_price)
    entry_date = entry_row["Date"].date().isoformat()
    strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
    em = ExitManager(EXIT_POLICY_ASYMMETRIC_V2, entry_price=ep,
                     disable_ma_break_for_position=strong_trend)

    exit_price = exit_date = exit_reason = None
    hold_days = None
    min_dd = 0.0

    for day_num, (_, row) in enumerate(future.head(20).iterrows(), 1):
        d = row["Date"].date().isoformat()
        policy_exit = em.before_open(row)
        if policy_exit is not None:
            op = _safe_float(row["Open"])
            if op is None:
                return None
            min_dd = min(min_dd, op / ep - 1.0)
            exit_price, exit_date, exit_reason, hold_days = float(policy_exit.exit_price), d, policy_exit.exit_reason, day_num
            break
        low = _safe_float(row["Low"])
        if low is not None:
            min_dd = min(min_dd, low / ep - 1.0)
        stop = simulate_intraday_stop(ep, row["Open"], row["Low"])
        if stop is not None:
            exit_price, exit_date, exit_reason, hold_days = float(stop.exit_price), d, stop.exit_reason, day_num
            break
        if day_num == 20:
            cp = _safe_float(row["Close"])
            if cp is None:
                return None
            exit_price, exit_date, exit_reason, hold_days = cp, d, EXIT_REASON_TIME_20D, day_num
            break
        em.after_close(row)

    if exit_price is None:
        return None

    twii_sub = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    twii_ret: float | None = None
    if len(twii_sub) >= 2:
        twii_ret = twii_sub.iloc[-1]["Close"] / twii_sub.iloc[0]["Close"] - 1.0

    realized = exit_price / ep - 1.0
    return {
        "variant": CONTROL, "month": month, "ticker": ticker, "rank": rank,
        "prediction_date": prediction_date, "entry_date": entry_date,
        "exit_date": exit_date, "exit_reason": exit_reason,
        "return_pct": realized, "twii_return_pct": twii_ret,
        "alpha_pct": realized - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days, "min_drawdown_pct": min_dd,
        "strong_trend_no_ma5": strong_trend,
    }


def _simulate_variant_a(
    *,
    ticker: str,
    prediction_date: str,
    month: str,
    rank: int,
    regime: str,
    price_history: pd.DataFrame,
    twii: pd.DataFrame,
) -> dict[str, Any] | None:
    """Variant A: CAUTION + hold_days < 3 → suppress MA5_BREAK."""
    ref_price, price_vs_ma20 = _signal_snapshot(price_history, prediction_date)
    if ref_price is None:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(ref_price, entry_row["Open"], entry_row["High"])
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None

    ep = float(entry.fill_price)
    entry_date = entry_row["Date"].date().isoformat()
    strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
    caution = (regime == STATE_CAUTION)

    exit_price = exit_date = exit_reason = None
    hold_days = None
    min_dd = 0.0
    hwm = ep
    pending_open_exit: str | None = None

    for day_num, (_, row) in enumerate(future.head(20).iterrows(), 1):
        d = row["Date"].date().isoformat()
        cp = _safe_float(row["Close"])
        op = _safe_float(row["Open"])

        if pending_open_exit is not None:
            if op is None:
                return None
            min_dd = min(min_dd, op / ep - 1.0)
            exit_price, exit_date, exit_reason, hold_days = op, d, pending_open_exit, day_num
            break

        # Intraday stop first
        stop = simulate_intraday_stop(ep, row["Open"], row["Low"])
        if stop is not None:
            low = _safe_float(row["Low"])
            if low is not None:
                min_dd = min(min_dd, low / ep - 1.0)
            exit_price, exit_date, exit_reason, hold_days = float(stop.exit_price), d, stop.exit_reason, day_num
            break

        low = _safe_float(row["Low"])
        if low is not None:
            min_dd = min(min_dd, low / ep - 1.0)

        if cp is not None:
            hwm = max(hwm, cp)

        # HWM -8% trail
        if cp is not None and hwm > 0 and (cp / hwm - 1.0) <= -0.08:
            pending_open_exit = "HWM_TRAIL"

        # MA5_BREAK: suppress if CAUTION and hold_days < MIN_HOLD_PROTECTION
        if pending_open_exit is None and not strong_trend:
            ma5 = _safe_float(row.get("MA5"))
            if ma5 is not None and cp is not None and cp < ma5:
                suppress = caution and day_num < MIN_HOLD_PROTECTION
                if not suppress:
                    pending_open_exit = EXIT_REASON_MA5_BREAK

        if day_num == 20:
            if cp is None:
                return None
            exit_price, exit_date, exit_reason, hold_days = cp, d, EXIT_REASON_TIME_20D, day_num
            break

    if exit_price is None:
        return None

    twii_sub = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    twii_ret: float | None = None
    if len(twii_sub) >= 2:
        twii_ret = twii_sub.iloc[-1]["Close"] / twii_sub.iloc[0]["Close"] - 1.0

    realized = exit_price / ep - 1.0
    return {
        "variant": VAR_A, "month": month, "ticker": ticker, "rank": rank,
        "prediction_date": prediction_date, "entry_date": entry_date,
        "exit_date": exit_date, "exit_reason": exit_reason,
        "return_pct": realized, "twii_return_pct": twii_ret,
        "alpha_pct": realized - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days, "min_drawdown_pct": min_dd,
        "strong_trend_no_ma5": strong_trend,
    }


def _simulate_variant_b(
    *,
    ticker: str,
    prediction_date: str,
    month: str,
    rank: int,
    regime: str,
    price_history: pd.DataFrame,
    twii: pd.DataFrame,
) -> dict[str, Any] | None:
    """Variant B: CAUTION → MA5_BREAK needs 2 consecutive days to trigger."""
    ref_price, price_vs_ma20 = _signal_snapshot(price_history, prediction_date)
    if ref_price is None:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(ref_price, entry_row["Open"], entry_row["High"])
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None

    ep = float(entry.fill_price)
    entry_date = entry_row["Date"].date().isoformat()
    strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
    caution = (regime == STATE_CAUTION)

    exit_price = exit_date = exit_reason = None
    hold_days = None
    min_dd = 0.0
    hwm = ep
    ma5_break_streak = 0
    pending_open_exit: str | None = None

    for day_num, (_, row) in enumerate(future.head(20).iterrows(), 1):
        d = row["Date"].date().isoformat()
        cp = _safe_float(row["Close"])
        op = _safe_float(row["Open"])

        if pending_open_exit is not None:
            if op is None:
                return None
            min_dd = min(min_dd, op / ep - 1.0)
            exit_price, exit_date, exit_reason, hold_days = op, d, pending_open_exit, day_num
            break

        stop = simulate_intraday_stop(ep, row["Open"], row["Low"])
        if stop is not None:
            low = _safe_float(row["Low"])
            if low is not None:
                min_dd = min(min_dd, low / ep - 1.0)
            exit_price, exit_date, exit_reason, hold_days = float(stop.exit_price), d, stop.exit_reason, day_num
            break

        low = _safe_float(row["Low"])
        if low is not None:
            min_dd = min(min_dd, low / ep - 1.0)

        if cp is not None:
            hwm = max(hwm, cp)

        # HWM trail
        if cp is not None and hwm > 0 and (cp / hwm - 1.0) <= -0.08:
            pending_open_exit = "HWM_TRAIL"

        # MA5_BREAK with confirm logic
        if pending_open_exit is None and not strong_trend:
            ma5 = _safe_float(row.get("MA5"))
            if ma5 is not None and cp is not None:
                if cp < ma5:
                    ma5_break_streak += 1
                else:
                    ma5_break_streak = 0

                required_streak = MA5_CONFIRM_DAYS if caution else 1
                if ma5_break_streak >= required_streak:
                    pending_open_exit = EXIT_REASON_MA5_BREAK
            else:
                ma5_break_streak = 0

        if day_num == 20:
            if cp is None:
                return None
            exit_price, exit_date, exit_reason, hold_days = cp, d, EXIT_REASON_TIME_20D, day_num
            break

    if exit_price is None:
        return None

    twii_sub = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    twii_ret: float | None = None
    if len(twii_sub) >= 2:
        twii_ret = twii_sub.iloc[-1]["Close"] / twii_sub.iloc[0]["Close"] - 1.0

    realized = exit_price / ep - 1.0
    return {
        "variant": VAR_B, "month": month, "ticker": ticker, "rank": rank,
        "prediction_date": prediction_date, "entry_date": entry_date,
        "exit_date": exit_date, "exit_reason": exit_reason,
        "return_pct": realized, "twii_return_pct": twii_ret,
        "alpha_pct": realized - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days, "min_drawdown_pct": min_dd,
        "strong_trend_no_ma5": strong_trend,
    }


def _run_with_reentry_lock(
    fold: pd.DataFrame,
    price_cache: dict[str, pd.DataFrame | None],
    twii: pd.DataFrame,
    regime_map: dict[str, str],
    variant_label: str,
) -> list[dict[str, Any]]:
    """Variant C: same asymmetric_v2 but skip re-entry if ticker locked after MA5_BREAK."""
    rows: list[dict[str, Any]] = []
    locked: dict[str, str] = {}  # ticker → locked_until_date (exclusive)

    for fold_row in fold.sort_values(["month", "prediction_date", "rank"]).to_dict("records"):
        ticker = str(fold_row["ticker"]).zfill(4)
        pred_date = str(fold_row.get("Date") or fold_row.get("date"))
        month = str(fold_row["month"])
        rank = int(fold_row["rank"])

        # Check reentry lock
        lock_until = locked.get(ticker)
        if lock_until and pred_date <= lock_until:
            continue

        ph = price_cache.get(ticker)
        if ph is None:
            continue
        future = ph[ph["Date"] > pd.Timestamp(pred_date)].reset_index(drop=True)
        if future.empty:
            continue

        ref_price, price_vs_ma20 = _signal_snapshot(ph, pred_date)
        if ref_price is None:
            continue
        entry_row = future.iloc[0]
        entry = simulate_20d_entry(ref_price, entry_row["Open"], entry_row["High"])
        if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
            continue

        ep = float(entry.fill_price)
        entry_date = entry_row["Date"].date().isoformat()
        strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
        em = ExitManager(EXIT_POLICY_ASYMMETRIC_V2, entry_price=ep,
                         disable_ma_break_for_position=strong_trend)

        exit_price = exit_date = exit_reason = None
        hold_days = None
        min_dd = 0.0

        for day_num, (_, row) in enumerate(future.head(20).iterrows(), 1):
            d = row["Date"].date().isoformat()
            policy_exit = em.before_open(row)
            if policy_exit is not None:
                op = _safe_float(row["Open"])
                if op is None:
                    break
                min_dd = min(min_dd, op / ep - 1.0)
                exit_price, exit_date, exit_reason, hold_days = float(policy_exit.exit_price), d, policy_exit.exit_reason, day_num
                break
            low = _safe_float(row["Low"])
            if low is not None:
                min_dd = min(min_dd, low / ep - 1.0)
            stop = simulate_intraday_stop(ep, row["Open"], row["Low"])
            if stop is not None:
                exit_price, exit_date, exit_reason, hold_days = float(stop.exit_price), d, stop.exit_reason, day_num
                break
            if day_num == 20:
                cp = _safe_float(row["Close"])
                if cp is None:
                    break
                exit_price, exit_date, exit_reason, hold_days = cp, d, EXIT_REASON_TIME_20D, day_num
                break
            em.after_close(row)

        if exit_price is None or exit_date is None:
            continue

        # Apply reentry lock if MA5_BREAK
        if exit_reason == EXIT_REASON_MA5_BREAK:
            lock_date = (datetime.fromisoformat(exit_date) + timedelta(days=REENTRY_LOCK_DAYS)).date().isoformat()
            locked[ticker] = lock_date

        twii_sub = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
        twii_ret: float | None = None
        if len(twii_sub) >= 2:
            twii_ret = twii_sub.iloc[-1]["Close"] / twii_sub.iloc[0]["Close"] - 1.0

        realized = exit_price / ep - 1.0
        rows.append({
            "variant": variant_label, "month": month, "ticker": ticker, "rank": rank,
            "prediction_date": pred_date, "entry_date": entry_date,
            "exit_date": exit_date, "exit_reason": exit_reason,
            "return_pct": realized, "twii_return_pct": twii_ret,
            "alpha_pct": realized - twii_ret if twii_ret is not None else None,
            "hold_days": hold_days, "min_drawdown_pct": min_dd,
            "strong_trend_no_ma5": strong_trend,
        })
    return rows


def _cl3_check(control: dict[str, Any], treatment: dict[str, Any], label: str) -> dict[str, Any]:
    c_alpha = control["monthly_alpha"]
    t_alpha = treatment["monthly_alpha"]
    c_mdd = control["max_drawdown"]
    t_mdd = treatment["max_drawdown"]
    c_sharpe = control["sharpe"]
    t_sharpe = treatment["sharpe"]
    c_calmar = control["calmar"]
    t_calmar = treatment["calmar"]

    alpha_ok = (t_alpha - c_alpha) >= -0.005  # not more than 0.5pp worse
    mdd_ok = t_mdd >= c_mdd - 0.001          # not worse (less negative is better)
    sharpe_ok = (c_sharpe == 0) or (t_sharpe / c_sharpe >= 0.95)
    calmar_ok = (c_calmar == 0) or (t_calmar / c_calmar >= 0.95)

    gate_pass = alpha_ok and mdd_ok and sharpe_ok and calmar_ok
    return {
        "label": label,
        "alpha_delta": t_alpha - c_alpha,
        "mdd_delta": t_mdd - c_mdd,
        "sharpe_delta": t_sharpe - c_sharpe,
        "calmar_delta": t_calmar - c_calmar,
        "checks": {
            "alpha_not_regress_0.5pp": {"pass": alpha_ok, "value": t_alpha - c_alpha, "threshold": -0.005},
            "mdd_not_worse": {"pass": mdd_ok, "value": t_mdd - c_mdd, "threshold": -0.001},
            "sharpe_not_regress_5pct": {"pass": sharpe_ok, "value": t_sharpe, "control": c_sharpe},
            "calmar_not_regress_5pct": {"pass": calmar_ok, "value": t_calmar, "control": c_calmar},
        },
        "overall_pass": gate_pass,
    }


def _regime_breakdown(trades: pd.DataFrame, regime_map: dict[str, str]) -> dict[str, dict[str, dict[str, Any]]]:
    breakdown: dict[str, dict[str, dict[str, Any]]] = {}
    trades = trades.copy()
    trades["regime_proxy"] = trades["prediction_date"].map(regime_map).fillna(STATE_CLOSED)
    for variant, variant_grp in trades.groupby("variant"):
        variant_key = str(variant)
        breakdown[variant_key] = {}
        for regime, grp in variant_grp.groupby("regime_proxy"):
            breakdown[variant_key][str(regime)] = {
                "n": int(len(grp)),
                "avg_return": float(grp["return_pct"].mean()),
                "avg_alpha": (
                    float(grp["alpha_pct"].dropna().mean())
                    if grp["alpha_pct"].notna().any()
                    else None
                ),
                "win_rate": float((grp["return_pct"] > 0).mean()),
            }
    return breakdown


def run(args: argparse.Namespace) -> dict[str, Any]:
    fold = pd.read_csv(args.fold_artifact, dtype={"ticker": str})
    fold["month"] = fold["month"].astype(str)
    fold["prediction_date"] = fold["Date"].astype(str)
    fold = fold[(fold["month"] >= args.start_month) & (fold["month"] <= args.end_month)].copy()
    if fold.empty:
        raise ValueError("No fold rows in window")

    twii_raw = _load_twii()
    regime_df = _build_twii_regime(twii_raw)
    regime_map = dict(zip(regime_df["Date"].dt.date.astype(str), regime_df["regime_proxy"]))

    price_cache: dict[str, pd.DataFrame | None] = {}
    # Pre-load price histories
    for ticker in fold["ticker"].unique():
        t = str(ticker).zfill(4)
        price_cache[t] = _load_price_history(t, {})

    rows: list[dict[str, Any]] = []
    skipped = 0

    for fold_row in fold.sort_values(["month", "rank"]).to_dict("records"):
        ticker = str(fold_row["ticker"]).zfill(4)
        pred_date = str(fold_row.get("Date") or fold_row.get("date"))
        month = str(fold_row["month"])
        rank = int(fold_row["rank"])
        regime = _get_regime(regime_map, pred_date)

        ph = price_cache.get(ticker)
        if ph is None or ph.empty:
            skipped += 1
            continue

        ctrl = _simulate_control(ticker=ticker, prediction_date=pred_date, month=month,
                                  rank=rank, price_history=ph, twii=twii_raw)
        var_a = _simulate_variant_a(ticker=ticker, prediction_date=pred_date, month=month,
                                     rank=rank, regime=regime, price_history=ph, twii=twii_raw)
        var_b = _simulate_variant_b(ticker=ticker, prediction_date=pred_date, month=month,
                                     rank=rank, regime=regime, price_history=ph, twii=twii_raw)

        if ctrl is None or var_a is None or var_b is None:
            skipped += 1
            continue
        rows.extend([ctrl, var_a, var_b])

    # Variant C requires chronological processing with reentry lock
    var_c_rows = _run_with_reentry_lock(fold, price_cache, twii_raw, regime_map, VAR_C)
    rows.extend(var_c_rows)

    trades = pd.DataFrame(rows)
    if trades.empty:
        raise ValueError("No trades produced")

    # Summarize per variant
    summary_by_variant: dict[str, dict[str, Any]] = {}
    for variant, grp in trades.groupby("variant"):
        s, monthly = _summarize(grp)
        summary = s.iloc[0]
        summary_by_variant[str(variant)] = {
            "monthly_alpha": float(summary["monthly_avg_alpha"]),
            "max_drawdown": float(summary["mdd"]),
            "sharpe": float(summary["sharpe"]),
            "calmar": float(summary["calmar"]),
            "monthly_return": float(summary["monthly_avg_return"]),
            "n_trades": len(grp),
            "avg_hold_days": grp["hold_days"].mean(),
            "win_rate": (grp["return_pct"] > 0).mean(),
            "ma5_break_count": (grp["exit_reason"] == EXIT_REASON_MA5_BREAK).sum(),
            "ma5_break_rate": (grp["exit_reason"] == EXIT_REASON_MA5_BREAK).mean(),
        }

    # Re-entry rate for control (reference)
    ctrl_trades = trades[trades["variant"] == CONTROL].copy()
    ctrl_trades["exit_date_dt"] = pd.to_datetime(ctrl_trades["exit_date"])
    ctrl_trades["prediction_date_dt"] = pd.to_datetime(ctrl_trades["prediction_date"])
    reentry_count = 0
    for ticker, grp in ctrl_trades.groupby("ticker"):
        grp = grp.sort_values("exit_date_dt")
        for i in range(len(grp) - 1):
            gap = (grp.iloc[i + 1]["prediction_date_dt"] - grp.iloc[i]["exit_date_dt"]).days
            if 0 <= gap <= 3:
                reentry_count += 1
    ctrl_n = len(ctrl_trades[ctrl_trades["exit_reason"] == EXIT_REASON_MA5_BREAK])
    reentry_rate = reentry_count / max(ctrl_n, 1)

    # CL3 checks
    ctrl_s = summary_by_variant.get(CONTROL, {})
    cl3_results = {}
    for var_label in [VAR_A, VAR_B, VAR_C]:
        if var_label in summary_by_variant:
            cl3_results[var_label] = _cl3_check(ctrl_s, summary_by_variant[var_label], var_label)

    regime_breakdown = _regime_breakdown(trades, regime_map)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fold_artifact": args.fold_artifact,
        "window": f"{args.start_month} ~ {args.end_month}",
        "skipped_entries": skipped,
        "ctrl_reentry_rate_3d": reentry_rate,
        "summary_by_variant": summary_by_variant,
        "cl3": cl3_results,
        "regime_breakdown": regime_breakdown,
        "ctrl_by_regime": regime_breakdown.get(CONTROL, {}),
    }
    return result


def _write_report(result: dict[str, Any], output_dir: Path, prefix: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{prefix}_{ts}.json"
    md_path = output_dir / f"{prefix}_{ts}.md"

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )

    sv = result["summary_by_variant"]
    cl3 = result["cl3"]

    lines = [
        "# REQ-029 CAUTION Whipsaw Exit Overlay CL3",
        "",
        f"Generated: {result['generated_at']}",
        f"Window: {result['window']}",
        f"Control re-entry rate (≤3d): {result['ctrl_reentry_rate_3d']:.1%}",
        "",
        "## Summary by Variant",
        "",
        "| Variant | Alpha | MDD | Sharpe | Calmar | N | Hold_days | WinRate | MA5_breaks |",
        "|---------|------:|----:|-------:|-------:|--:|----------:|--------:|-----------:|",
    ]
    for v in [CONTROL, VAR_A, VAR_B, VAR_C]:
        s = sv.get(v, {})
        if not s:
            continue
        lines.append(
            f"| {v} | {_fmt_pct(s.get('monthly_alpha'))} | {_fmt_pct(s.get('max_drawdown'))} | "
            f"{s.get('sharpe', 0):.3f} | {s.get('calmar', 0):.3f} | {s.get('n_trades', 0)} | "
            f"{s.get('avg_hold_days', 0):.1f} | {_fmt_pct(s.get('win_rate'))} | "
            f"{s.get('ma5_break_count', 0)} ({_fmt_pct(s.get('ma5_break_rate'))}) |"
        )

    lines += ["", "## CL3 Results", ""]
    for var_label, cl3r in cl3.items():
        status = "✅ PASS" if cl3r["overall_pass"] else "❌ FAIL"
        lines += [
            f"### {var_label} — {status}",
            "",
            f"| Check | Value | Threshold | Result |",
            f"|-------|------:|----------:|--------|",
        ]
        for check_name, check in cl3r["checks"].items():
            v_str = _fmt_pct(check.get("value"))
            t_str = _fmt_pct(check.get("threshold")) if "threshold" in check else f"≥95% of {_fmt_pct(check.get('control'))}"
            lines.append(f"| {check_name} | {v_str} | {t_str} | {'PASS' if check['pass'] else 'FAIL'} |")
        lines.append("")

    lines += [
        "## Per-Regime Breakdown",
        "",
        "| Variant | Regime | N | Avg Return | Avg Alpha | Alpha Δ vs Control | Win Rate |",
        "|---------|--------|--:|----------:|----------:|-------------------:|---------:|",
    ]
    regime_breakdown = result.get("regime_breakdown", {})
    control_regime = regime_breakdown.get(CONTROL, {})
    for variant in [CONTROL, VAR_A, VAR_B, VAR_C]:
        for regime in [STATE_CAUTION, STATE_OPEN, STATE_CLOSED]:
            rdata = regime_breakdown.get(variant, {}).get(regime)
            if not rdata:
                continue
            control_alpha = control_regime.get(regime, {}).get("avg_alpha")
            alpha = rdata.get("avg_alpha")
            alpha_delta = (
                alpha - control_alpha
                if alpha is not None and control_alpha is not None
                else None
            )
            lines.append(
                f"| {variant} | {regime} | {rdata['n']} | {_fmt_pct(rdata.get('avg_return'))} | "
                f"{_fmt_pct(alpha)} | {_fmt_pct(alpha_delta)} | {_fmt_pct(rdata.get('win_rate'))} |"
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[REQ-029] JSON: {json_path}")
    print(f"[REQ-029] MD:   {md_path}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="REQ-029 CAUTION whipsaw CL3 backtest.")
    parser.add_argument(
        "--fold-artifact",
        default=str(Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"),
    )
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=str(Path(REPORT_DIR)))
    parser.add_argument("--output-prefix", default="req029_caution_whipsaw_cl3")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[REQ-029] Running CAUTION whipsaw CL3 on {args.fold_artifact}")
    result = run(args)

    sv = result["summary_by_variant"]
    cl3 = result["cl3"]
    print(f"\n=== Summary ===")
    for v in [CONTROL, VAR_A, VAR_B, VAR_C]:
        s = sv.get(v, {})
        if s:
            cl3_tag = ""
            if v in cl3:
                cl3_tag = " ✅" if cl3[v]["overall_pass"] else " ❌"
            print(
                f"  {v}: alpha={_fmt_pct(s.get('monthly_alpha'))} mdd={_fmt_pct(s.get('max_drawdown'))} "
                f"n={s.get('n_trades')} hold={s.get('avg_hold_days', 0):.1f}d "
                f"ma5={s.get('ma5_break_count')}({_fmt_pct(s.get('ma5_break_rate'))}){cl3_tag}"
            )
    print(f"\n  Control 3d re-entry rate: {result['ctrl_reentry_rate_3d']:.1%}")

    _write_report(result, output_dir, args.output_prefix)


if __name__ == "__main__":
    main()
