"""CMoney ChipK local snapshot reader.

The desktop app stores a small CP950 encoded snapshot under the AppViewer
profile.  This module reads only non-secret market data files.  It never reads
cookies, login databases, WebView sessions, or registry credentials.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

CHIPK_MAIN_FORCE_FILENAME = "2056757959.txt"
CHIPK_DATE_RELATIVE = Path("System") / "date.txt"
CHIPK_SCORE_VERSION = "chipk_main_force_snapshot_v1_20260618"
CHIPK_ORIENTATION = "lower_is_stronger_assumption"
CHIPK_DIAGNOSIS_VERSION = "chipk_main_force_diagnosis_v1_20260618"
CHIPK_BUCKET_LABELS = ["weak", "neutral", "supportive", "strong"]
CHIPK_REQUIRED_COLUMNS = [
    "股票代號",
    "股票名稱",
    "收盤價",
    "成交量",
    "主力動向1日",
    "主力動向5日",
    "主力動向20日",
]


@dataclass(frozen=True)
class ChipKLoadMeta:
    status: str
    asof_date: str | None
    source_path: str | None
    row_count: int
    history_available: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "asof_date": self.asof_date,
            "source_path": self.source_path,
            "row_count": self.row_count,
            "history_available": self.history_available,
            "message": self.message,
            "score_version": CHIPK_SCORE_VERSION,
            "score_orientation": CHIPK_ORIENTATION,
        }


def _appviewer_root() -> Path:
    override = os.environ.get("CMONEY_APPVIEWER_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return Path()
    return Path(appdata) / "AppViewer"


def _safe_profile_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    profiles: list[Path] = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        chart_dir = item / "UBSKChart"
        if (chart_dir / CHIPK_MAIN_FORCE_FILENAME).exists():
            profiles.append(chart_dir)
    return sorted(profiles, key=lambda p: (p / CHIPK_MAIN_FORCE_FILENAME).stat().st_mtime, reverse=True)


def find_latest_chipk_snapshot_path(root: Path | None = None) -> Path | None:
    """Return the newest local ChipK main-force snapshot path, if present."""

    search_root = root or _appviewer_root()
    profiles = _safe_profile_dirs(search_root)
    if not profiles:
        return None
    return profiles[0] / CHIPK_MAIN_FORCE_FILENAME


def _read_asof_date(chart_dir: Path) -> str | None:
    date_path = chart_dir / CHIPK_DATE_RELATIVE
    if not date_path.exists():
        return None
    raw = date_path.read_text(encoding="cp950", errors="ignore").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or None


def _normalize_ticker(value: object) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= 4:
        return text.zfill(4)
    return text


def _score_from_force_columns(df: pd.DataFrame) -> pd.Series:
    """Map CMoney 1..5 main-force ranks into 0..1 strength score.

    The local file exposes ranks, not signed buy/sell quantities.  The current
    interpretation is intentionally explicit and auditable: lower rank is
    treated as stronger main-force support.  Historical backtests must verify
    this before the score is allowed to alter production gates.
    """

    weights = {
        "chipk_main_force_1d": 0.25,
        "chipk_main_force_5d": 0.35,
        "chipk_main_force_20d": 0.40,
    }
    score = pd.Series(0.0, index=df.index, dtype="float64")
    total_weight = 0.0
    for col, weight in weights.items():
        raw = pd.to_numeric(df.get(col), errors="coerce")
        # 1 -> 1.00, 3 -> 0.50, 5 -> 0.00
        component = ((5.0 - raw) / 4.0).clip(lower=0.0, upper=1.0)
        score = score + component.fillna(0.5) * weight
        total_weight += weight
    return score / total_weight if total_weight else pd.Series(np.nan, index=df.index)


def chipk_score_bucket(score: pd.Series | float | int | None) -> pd.Series | str:
    """Convert 0..1 ChipK main-force score to a stable bucket label."""

    if isinstance(score, pd.Series):
        bucketed = pd.cut(
            pd.to_numeric(score, errors="coerce"),
            bins=[-0.01, 0.25, 0.50, 0.70, 1.01],
            labels=CHIPK_BUCKET_LABELS,
        )
        return bucketed.astype("object").where(bucketed.notna(), "missing")
    value = pd.to_numeric(pd.Series([score]), errors="coerce").iloc[0]
    if pd.isna(value):
        return "missing"
    if value <= 0.25:
        return "weak"
    if value <= 0.50:
        return "neutral"
    if value <= 0.70:
        return "supportive"
    return "strong"


def annotate_chipk_main_force_diagnosis(df: pd.DataFrame) -> pd.DataFrame:
    """Classify main-force shape into entry-confirmation/trap diagnostics.

    The snapshot exposes 1..5 ranks rather than signed buy/sell quantities.
    Lower rank is treated as stronger support.  These labels are diagnostics
    only; broker-detail and retail-flow exports are needed before hard gates.
    """

    out = df.copy()
    idx = out.index
    f1 = pd.to_numeric(out.get("chipk_main_force_1d"), errors="coerce")
    f5 = pd.to_numeric(out.get("chipk_main_force_5d"), errors="coerce")
    f20 = pd.to_numeric(out.get("chipk_main_force_20d"), errors="coerce")
    score = pd.to_numeric(out.get("chipk_main_force_score"), errors="coerce")

    strong_1d = f1.le(2)
    strong_5d = f5.le(2)
    strong_20d = f20.le(2)
    weak_1d = f1.ge(4)
    weak_5d = f5.ge(4)
    weak_20d = f20.ge(4)

    pattern = pd.Series("mixed_or_unconfirmed", index=idx, dtype="object")
    pattern = pattern.mask(strong_1d & strong_5d & strong_20d, "sustained_accumulation")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & strong_1d & weak_5d & weak_20d, "short_term_chase_trap")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & weak_1d & weak_5d & weak_20d, "distribution_pressure")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & weak_1d & strong_5d & strong_20d, "constructive_pullback")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & weak_1d & weak_5d & strong_20d, "short_term_fade")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & strong_1d & strong_5d & ~weak_20d, "near_term_accumulation")
    open_slot = pattern.eq("mixed_or_unconfirmed")
    pattern = pattern.mask(open_slot & ~weak_1d & strong_5d & strong_20d, "constructive_support")

    trap = (strong_1d & (weak_5d | weak_20d)) | pattern.eq("short_term_chase_trap")
    distribution = pattern.eq("distribution_pressure")

    action = pd.Series("WATCH", index=idx, dtype="object")
    action = action.mask(score.gt(0.70) & ~trap & ~distribution, "CONFIRM_ENTRY")
    action = action.mask(score.gt(0.50) & score.le(0.70) & ~trap & ~distribution, "SUPPORTIVE_ENTRY")
    action = action.mask(pattern.eq("constructive_pullback"), "WAIT_FOR_TURN")
    action = action.mask(score.le(0.25) | distribution, "AVOID")
    action = action.mask(trap, "DO_NOT_CHASE")

    risk_flags = pd.Series("", index=idx, dtype="object")
    risk_flags = _append_flag(risk_flags, trap, "day_trade_trap_risk")
    risk_flags = _append_flag(risk_flags, distribution, "distribution_pressure")
    risk_flags = _append_flag(risk_flags, weak_1d & weak_5d, "short_term_main_force_fade")
    risk_flags = _append_flag(risk_flags, weak_20d, "weak_20d_main_force")

    out["chipk_diagnosis_version"] = CHIPK_DIAGNOSIS_VERSION
    out["chipk_bucket"] = chipk_score_bucket(score)
    out["chipk_main_force_pattern"] = pattern
    out["chipk_entry_alignment"] = action
    out["chipk_trap_risk"] = trap.fillna(False).astype(bool)
    out["chipk_distribution_risk"] = distribution.fillna(False).astype(bool)
    out["chipk_risk_flags"] = risk_flags
    return out


def _append_flag(base: pd.Series, mask: pd.Series, flag: str) -> pd.Series:
    mask = mask.fillna(False)
    return base.where(~mask, base.where(base.eq(""), base + ";") + flag)


def load_latest_chipk_main_force(path: str | Path | None = None) -> tuple[pd.DataFrame, ChipKLoadMeta]:
    """Load the latest CMoney ChipK main-force snapshot.

    Returns an empty frame with status metadata on any non-fatal failure.
    """

    snapshot_path = Path(path) if path else find_latest_chipk_snapshot_path()
    if snapshot_path is None:
        return pd.DataFrame(), ChipKLoadMeta(
            status="missing",
            asof_date=None,
            source_path=None,
            row_count=0,
            history_available=False,
            message="chipk snapshot file not found",
        )
    try:
        text = snapshot_path.read_text(encoding="cp950", errors="replace")
        rows = list(csv.reader(io.StringIO(text), delimiter="^"))
    except Exception as exc:
        return pd.DataFrame(), ChipKLoadMeta(
            status="error",
            asof_date=None,
            source_path=str(snapshot_path),
            row_count=0,
            history_available=False,
            message=f"{type(exc).__name__}: {exc}",
        )
    if not rows:
        return pd.DataFrame(), ChipKLoadMeta(
            status="empty",
            asof_date=_read_asof_date(snapshot_path.parent),
            source_path=str(snapshot_path),
            row_count=0,
            history_available=False,
            message="chipk snapshot is empty",
        )

    header = [str(item).strip() for item in rows[0]]
    missing = [col for col in CHIPK_REQUIRED_COLUMNS if col not in header]
    if missing:
        return pd.DataFrame(), ChipKLoadMeta(
            status="error",
            asof_date=_read_asof_date(snapshot_path.parent),
            source_path=str(snapshot_path),
            row_count=0,
            history_available=False,
            message="missing columns: " + ",".join(missing),
        )

    records = [row for row in rows[1:] if row and len(row) >= len(header)]
    raw_df = pd.DataFrame(records, columns=header)
    out = pd.DataFrame(
        {
            "ticker": raw_df["股票代號"].map(_normalize_ticker),
            "chipk_name": raw_df["股票名稱"].astype(str).str.strip(),
            "chipk_close": pd.to_numeric(raw_df["收盤價"], errors="coerce"),
            "chipk_volume": pd.to_numeric(raw_df["成交量"], errors="coerce"),
            "chipk_main_force_1d": pd.to_numeric(raw_df["主力動向1日"], errors="coerce"),
            "chipk_main_force_5d": pd.to_numeric(raw_df["主力動向5日"], errors="coerce"),
            "chipk_main_force_20d": pd.to_numeric(raw_df["主力動向20日"], errors="coerce"),
        }
    )
    out = out[out["ticker"].astype(str).str.len().gt(0)].drop_duplicates("ticker", keep="first")
    out["chipk_main_force_score"] = _score_from_force_columns(out).round(6)
    out["chipk_score_version"] = CHIPK_SCORE_VERSION
    out["chipk_score_orientation"] = CHIPK_ORIENTATION
    asof = _read_asof_date(snapshot_path.parent)
    out["chipk_asof_date"] = asof
    out["chipk_history_available"] = False
    out = annotate_chipk_main_force_diagnosis(out)
    meta = ChipKLoadMeta(
        status="ok",
        asof_date=asof,
        source_path=str(snapshot_path),
        row_count=int(len(out)),
        history_available=False,
        message="latest snapshot loaded; historical ChipK series not detected",
    )
    return out.reset_index(drop=True), meta


def redact_chipk_source_path(path: str | None) -> str:
    """Return a non-secret source description for reports/logs."""

    if not path:
        return ""
    p = Path(path)
    return str(Path("...") / "UBSKChart" / p.name)
