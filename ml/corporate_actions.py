"""Corporate-action factors for labels and price-continuous indicators.

The two factor meanings are deliberately separate:

``label_factor``
    Economic shareholder-return basis (official ex-right/dividend or resume
    reference divided by the prior close). Training targets use this factor.

``price_factor``
    Trading-price continuity basis (official opening/starting-trade basis
    divided by the prior close). Technical indicators use this factor. The
    values can differ for rights offerings, where shareholders receive an
    economic right but the exchange does not rebase the opening auction.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR_PATH = BASE_DIR / "ml" / "data" / "ex_dividend_calendar.csv"
DEFAULT_CORPORATE_ACTION_PATH = (
    BASE_DIR / "ml" / "data" / "corporate_action_price_factors.csv"
)


def normalize_action_calendar(
    calendar: pd.DataFrame,
    *,
    factor_column: str = "factor",
) -> pd.DataFrame:
    """Return validated ``stock_id/date/factor`` corporate-action rows."""
    required = {"stock_id", "date"}
    missing = sorted(required - set(calendar.columns))
    if missing:
        raise ValueError(f"corporate-action calendar missing columns: {missing}")

    has_prices = {"before_price", "after_price"}.issubset(calendar.columns)
    has_factor = factor_column in calendar.columns
    if not has_prices and not has_factor:
        raise ValueError(
            "corporate-action calendar requires before_price/after_price or factor"
        )

    keep = ["stock_id", "date"]
    keep.extend(
        c
        for c in ("before_price", "after_price", factor_column)
        if c in calendar
    )
    out = calendar.loc[:, keep].copy()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    direct_factor = (
        pd.to_numeric(out[factor_column], errors="coerce")
        if has_factor
        else pd.Series(np.nan, index=out.index, dtype=np.float64)
    )
    if has_prices:
        before = pd.to_numeric(out["before_price"], errors="coerce")
        after = pd.to_numeric(out["after_price"], errors="coerce")
        price_factor = after / before
        direct_factor = direct_factor.fillna(price_factor)
    out["factor"] = direct_factor
    out = out.dropna(subset=["stock_id", "date", "factor"])
    invalid = (~np.isfinite(out["factor"])) | out["factor"].le(0)
    if invalid.any():
        sample = out.loc[invalid, ["stock_id", "date", "factor"]].head(5)
        raise ValueError(
            "corporate-action calendar contains non-positive factors: "
            f"{sample.to_dict('records')}"
        )
    return out[["stock_id", "date", "factor"]].sort_values(
        ["stock_id", "date"]
    ).reset_index(drop=True)


@lru_cache(maxsize=8)
def load_action_calendar(
    path: str | Path = DEFAULT_CALENDAR_PATH,
    supplemental_path: str | Path | None = DEFAULT_CORPORATE_ACTION_PATH,
) -> pd.DataFrame:
    """Load the economic-return factor calendar used by training labels.

    The richer official action file replaces the legacy ex-dividend row for
    the same ticker/date. This prevents the same action being multiplied twice
    while retaining the legacy calendar as a compatibility fallback.
    """
    calendar_path = Path(path)
    base = pd.DataFrame(columns=["stock_id", "date", "factor"])
    if calendar_path.exists():
        raw = pd.read_csv(calendar_path, dtype={"stock_id": str}, encoding="utf-8-sig")
        base = normalize_action_calendar(raw)

    extra = pd.DataFrame(columns=["stock_id", "date", "factor"])
    if supplemental_path is not None:
        extra_path = Path(supplemental_path)
        if extra_path.exists():
            raw_extra = pd.read_csv(
                extra_path, dtype={"stock_id": str}, encoding="utf-8-sig"
            )
            factor_column = (
                "label_factor" if "label_factor" in raw_extra.columns else "factor"
            )
            extra = normalize_action_calendar(
                raw_extra, factor_column=factor_column
            )

    if base.empty and extra.empty:
        return pd.DataFrame(columns=["stock_id", "date", "factor"])

    if not extra.empty and not base.empty:
        extra_keys = pd.MultiIndex.from_frame(extra[["stock_id", "date"]])
        base_keys = pd.MultiIndex.from_frame(base[["stock_id", "date"]])
        base = base.loc[~base_keys.isin(extra_keys)].copy()
    combined = pd.concat([base, extra], ignore_index=True)
    # Distinct same-day mechanisms multiply. Exact source duplicates do not.
    combined = combined.drop_duplicates(["stock_id", "date", "factor"])
    return (
        combined.groupby(["stock_id", "date"], as_index=False)["factor"]
        .prod()
        .sort_values(["stock_id", "date"])
        .reset_index(drop=True)
    )


@lru_cache(maxsize=8)
def load_price_continuity_calendar(
    path: str | Path = DEFAULT_CORPORATE_ACTION_PATH,
) -> pd.DataFrame:
    """Load official opening-basis factors used by technical indicators."""
    action_path = Path(path)
    if not action_path.exists():
        return pd.DataFrame(columns=["stock_id", "date", "factor"])
    raw = pd.read_csv(action_path, dtype={"stock_id": str}, encoding="utf-8-sig")
    factor_column = "price_factor" if "price_factor" in raw.columns else "factor"
    normalized = normalize_action_calendar(raw, factor_column=factor_column)
    normalized = normalized.drop_duplicates(["stock_id", "date", "factor"])
    return (
        normalized.groupby(["stock_id", "date"], as_index=False)["factor"]
        .prod()
        .sort_values(["stock_id", "date"])
        .reset_index(drop=True)
    )


def backward_adjustment_multiplier(
    dates: pd.Series,
    ticker: str | None,
    calendar: pd.DataFrame | None = None,
) -> pd.Series:
    """Return a no-look-ahead-compatible price multiplier for each row.

    Raw prices before a corporate action are multiplied by every later event factor
    through the frame's last date. Price-derived features are then rescaled to each
    row's raw-price basis, so future events cannot alter ratio features or signals.
    """
    normalized_dates = pd.to_datetime(dates, errors="coerce").dt.normalize()
    result = pd.Series(1.0, index=dates.index, dtype=np.float64)
    if not ticker or normalized_dates.notna().sum() == 0:
        return result

    actions = load_price_continuity_calendar() if calendar is None else (
        calendar
        if set(calendar.columns) >= {"stock_id", "date", "factor"}
        else normalize_action_calendar(calendar)
    )
    actions = actions.loc[
        actions["stock_id"].astype(str).eq(str(ticker)), ["date", "factor"]
    ].copy()
    if actions.empty:
        return result

    actions["date"] = pd.to_datetime(actions["date"], errors="coerce").dt.normalize()
    actions["factor"] = pd.to_numeric(actions["factor"], errors="coerce")
    max_date = normalized_dates.max()
    actions = actions.dropna().loc[actions["date"].le(max_date)]
    if actions.empty:
        return result
    actions = actions.groupby("date", as_index=False)["factor"].prod().sort_values("date")

    event_dates = actions["date"].to_numpy(dtype="datetime64[ns]")
    event_factors = actions["factor"].to_numpy(dtype=np.float64)
    suffix = np.ones(len(event_factors) + 1, dtype=np.float64)
    suffix[:-1] = np.cumprod(event_factors[::-1])[::-1]

    valid = normalized_dates.notna()
    positions = np.searchsorted(
        event_dates,
        normalized_dates.loc[valid].to_numpy(dtype="datetime64[ns]"),
        side="right",
    )
    result.loc[valid] = suffix[positions]
    return result


def action_adjusted_ohlc(
    frame: pd.DataFrame,
    ticker: str | None,
    calendar: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build a company-action-continuous OHLC view without mutating raw prices."""
    adjusted = frame.copy()
    if "Date" not in adjusted.columns:
        raise ValueError("OHLC frame requires Date")
    multiplier = backward_adjustment_multiplier(adjusted["Date"], ticker, calendar)
    for column in ("Open", "High", "Low", "Close"):
        if column in adjusted.columns:
            adjusted[column] = pd.to_numeric(adjusted[column], errors="coerce") * multiplier
    return adjusted, multiplier


def cumulative_action_factor(
    dates: pd.Series,
    ticker: str | None,
    calendar: pd.DataFrame | None = None,
) -> pd.Series:
    """Return cumulative product of event factors through each observation date.

    A return over ``(start, end]`` is adjusted with
    ``cumulative[start] / cumulative[end]``. This excludes an event on the
    start date and includes one on the end date without inventing prices.
    """
    normalized_dates = pd.to_datetime(dates, errors="coerce").dt.normalize()
    factors = pd.Series(1.0, index=dates.index, dtype=np.float64)
    if not ticker:
        return factors

    actions = load_action_calendar() if calendar is None else (
        calendar
        if set(calendar.columns) >= {"stock_id", "date", "factor"}
        else normalize_action_calendar(calendar)
    )
    actions = actions.loc[actions["stock_id"].astype(str).eq(str(ticker))]
    if actions.empty:
        return factors

    by_date = actions.groupby("date", sort=True)["factor"].prod()
    factors = normalized_dates.map(by_date).fillna(1.0).astype(np.float64)
    factors.index = dates.index
    return factors.cumprod()


def action_window_factor(
    dates: pd.Series,
    ticker: str | None,
    start_offset: int,
    end_offset: int,
    calendar: pd.DataFrame | None = None,
) -> pd.Series:
    """Return inverse event-factor product for ``(t+start, t+end]``."""
    cumulative = cumulative_action_factor(dates, ticker, calendar)
    start = cumulative.shift(-start_offset)
    end = cumulative.shift(-end_offset)
    return start / end
