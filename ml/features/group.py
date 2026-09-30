"""Supply-chain/theme group mapping for portfolio concentration controls."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ml.config import BASE_DIR

GROUP_MAPPING_PATH = Path(BASE_DIR) / "ml" / "data" / "group_mapping.csv"
OTHER_GROUP_CODE = "OTHER"
OTHER_GROUP_NAME = "Other / uncapped"


def normalize_group_ticker(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(4) if text.isdigit() and len(text) < 4 else text


def load_group_mapping(path: str | Path | None = None) -> pd.DataFrame:
    """Load ticker -> supply-chain/theme group mapping.

    The mapping is intentionally a living data file. Missing tickers are treated
    as OTHER and are not capped by group concentration controls.
    """

    resolved = Path(path) if path is not None else GROUP_MAPPING_PATH
    if not resolved.exists():
        return pd.DataFrame(columns=["ticker", "name", "sector", "group_code", "group_name"])

    df = pd.read_csv(resolved, dtype={"ticker": str}, encoding="utf-8-sig")
    required = ["ticker", "name", "sector", "group_code", "group_name"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"group mapping missing columns: {', '.join(missing)}")

    df = df[required].copy()
    df["ticker"] = df["ticker"].map(normalize_group_ticker)
    df["group_code"] = df["group_code"].fillna(OTHER_GROUP_CODE).astype(str).str.strip()
    df["group_name"] = df["group_name"].fillna(OTHER_GROUP_NAME).astype(str).str.strip()
    df.loc[df["group_code"].eq(""), "group_code"] = OTHER_GROUP_CODE
    df.loc[df["group_name"].eq(""), "group_name"] = OTHER_GROUP_NAME
    return df.drop_duplicates(subset=["ticker"], keep="first")


def annotate_group_columns(df: pd.DataFrame, mapping: pd.DataFrame | None = None) -> pd.DataFrame:
    """Attach group_code/group_name columns to a ticker DataFrame."""

    out = df.copy()
    if "ticker" not in out.columns:
        out["group_code"] = OTHER_GROUP_CODE
        out["group_name"] = OTHER_GROUP_NAME
        return out

    if mapping is None:
        mapping = load_group_mapping()

    if mapping.empty:
        out["group_code"] = out.get("group_code", OTHER_GROUP_CODE)
        out["group_name"] = out.get("group_name", OTHER_GROUP_NAME)
        out["group_code"] = out["group_code"].fillna(OTHER_GROUP_CODE)
        out["group_name"] = out["group_name"].fillna(OTHER_GROUP_NAME)
        return out

    left = out.copy()
    left["_group_ticker"] = left["ticker"].map(normalize_group_ticker)
    mapped = left.merge(
        mapping[["ticker", "group_code", "group_name"]],
        left_on="_group_ticker",
        right_on="ticker",
        how="left",
        suffixes=("", "_group_map"),
    )
    if "ticker_group_map" in mapped.columns:
        mapped = mapped.drop(columns=["ticker_group_map"])
    mapped = mapped.drop(columns=["_group_ticker"])
    if "group_code_group_map" in mapped.columns:
        mapped["group_code"] = mapped.get("group_code").combine_first(mapped["group_code_group_map"])
        mapped = mapped.drop(columns=["group_code_group_map"])
    if "group_name_group_map" in mapped.columns:
        mapped["group_name"] = mapped.get("group_name").combine_first(mapped["group_name_group_map"])
        mapped = mapped.drop(columns=["group_name_group_map"])
    mapped["group_code"] = mapped["group_code"].fillna(OTHER_GROUP_CODE)
    mapped["group_name"] = mapped["group_name"].fillna(OTHER_GROUP_NAME)
    return mapped


def is_capped_group(group_code: object) -> bool:
    code = str(group_code).strip()
    return bool(code) and code != OTHER_GROUP_CODE
