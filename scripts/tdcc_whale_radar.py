"""Validated local TDCC -> weekly radar lineage; no fetching or strategy changes."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SNAP = Path("ml/data/tdcc_whale_snapshot.csv")
DELTAS = Path("ml/data/tdcc_whale_deltas.csv")
STATUS = Path("ml/data/tdcc_whale_radar_status.json")
RAW_COLUMNS = {"資料日期": "date", "證券代號": "ticker", "持股分級": "level",
               "人數": "holders", "股數": "shares", "占集保庫存數比例%": "pct"}
MIN_PAIR_COVERAGE = 0.8  # Data completeness floor, never a trading threshold.


class TDCCRadarDataError(ValueError):
    """Incomplete, incorrectly dated or unverifiable data."""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date8(value) -> str:
    parsed = pd.to_datetime(str(value).replace("-", ""), format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        raise TDCCRadarDataError(f"Invalid TDCC date: {value}")
    return parsed.strftime("%Y%m%d")


def validate_raw_week(path: Path) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [str(c).lstrip("\ufeff").strip() for c in raw.columns]
    if not set(RAW_COLUMNS).issubset(raw.columns):
        raise TDCCRadarDataError(f"Raw schema incomplete: {path.name}")
    raw = raw.rename(columns=RAW_COLUMNS)[list(RAW_COLUMNS.values())]
    if raw.empty:
        raise TDCCRadarDataError(f"Raw week empty: {path.name}")
    dates = raw["date"].map(date8).unique().tolist()
    if len(dates) != 1 or dates[0] != path.stem.removeprefix("tdcc_"):
        raise TDCCRadarDataError(f"Raw filename/content date mismatch: {path.name}: {dates}")
    raw["ticker"] = raw["ticker"].str.strip()
    if raw["ticker"].isna().any() or raw["ticker"].eq("").any():
        raise TDCCRadarDataError(f"Missing security code: {path.name}")
    for col in ("level", "holders", "shares", "pct"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
        if not np.isfinite(raw[col]).all() or (raw[col] < 0).any():
            raise TDCCRadarDataError(f"Invalid numeric field {col}: {path.name}")
    if not raw["level"].between(1, 17).all() or raw["level"].mod(1).ne(0).any() or raw["pct"].gt(100).any():
        raise TDCCRadarDataError(f"Invalid level/percentage: {path.name}")
    if raw.duplicated(["ticker", "level"]).any():
        raise TDCCRadarDataError(f"Duplicate security/level: {path.name}")
    counts = raw[raw["level"].between(1, 15)].groupby("ticker")["level"].nunique()
    if len(counts) != raw["ticker"].nunique() or counts.ne(15).any():
        raise TDCCRadarDataError(f"Partial security levels (requires 1-15): {path.name}")
    four = raw[raw["ticker"].str.fullmatch(r"\d{4}")].copy()
    if four.empty:
        raise TDCCRadarDataError(f"No four-digit securities: {path.name}")
    return four, {"path": str(path.resolve()), "sha256": sha(path), "date": dates[0],
                  "raw_rows": len(raw), "raw_tickers": int(raw["ticker"].nunique()),
                  "four_digit_rows": len(four), "four_digit_tickers": int(four["ticker"].nunique()),
                  "required_levels_per_ticker": 15}


def aggregate(raw: pd.DataFrame) -> pd.DataFrame:
    p = raw.pivot(index="ticker", columns="level", values="pct")
    result = pd.DataFrame({"date": date8(raw["date"].iloc[0]), "whale1000": p[15],
                           "retail100": p[list(range(1, 10))].sum(axis=1)}).round(2).reset_index()
    if (result["whale1000"] + result["retail100"] > 100.05).any():
        raise TDCCRadarDataError("Whale + retail exceeds 100% (rounding tolerance 0.05pp)")
    tsmc = result[result["ticker"] == "2330"]
    if not tsmc.empty and float(tsmc["whale1000"].iloc[0]) <= 50:
        raise TDCCRadarDataError("2330 thousand-lot holdings must exceed 50%; check level semantics")
    return result


def load_local_week(path: Path) -> pd.DataFrame:
    return aggregate(validate_raw_week(path)[0])


def select_week_pair(input_dir: Path, as_of: str | None = None) -> tuple[Path, Path]:
    cap = date8(as_of) if as_of else None
    paths = sorted(p for p in input_dir.glob("tdcc_2???????.csv") if cap is None or p.stem[5:] <= cap)
    if len(paths) < 2:
        raise TDCCRadarDataError("Two raw weekly snapshots required; no weekly result produced")
    previous, current = paths[-2:]
    if pd.Period(date8(current.stem[5:]), freq="W-FRI").ordinal - pd.Period(date8(previous.stem[5:]), freq="W-FRI").ordinal != 1:
        raise TDCCRadarDataError(f"Missing adjacent TDCC week: {previous.name} -> {current.name}")
    return previous, current


def calculate_pair(previous: Path, current: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    prev_raw, prev_source = validate_raw_week(previous)
    cur_raw, cur_source = validate_raw_week(current)
    if pd.Period(cur_source["date"], freq="W-FRI").ordinal - pd.Period(prev_source["date"], freq="W-FRI").ordinal != 1:
        raise TDCCRadarDataError("Weekly delta cannot span a missing week")
    cur, prev = aggregate(cur_raw), aggregate(prev_raw)
    m = cur.merge(prev[["ticker", "whale1000", "retail100"]], on="ticker", suffixes=("", "_prev"))
    coverage = len(m) / max(len(prev), len(cur))
    if coverage < MIN_PAIR_COVERAGE or cur_source["raw_rows"] < prev_source["raw_rows"] * MIN_PAIR_COVERAGE:
        raise TDCCRadarDataError(f"Partial weekly market coverage: {coverage:.3%}")
    m["prev_date"] = prev_source["date"]
    m["whale_delta"] = (m["whale1000"] - m["whale1000_prev"]).round(2)
    m["retail_delta"] = (m["retail100"] - m["retail100_prev"]).round(2)
    return cur, m, {"previous": prev_source, "current": cur_source, "paired_tickers": len(m),
                    "pair_coverage": coverage, "current_only_tickers": sorted(set(cur.ticker)-set(prev.ticker)),
                    "previous_only_tickers": sorted(set(prev.ticker)-set(cur.ticker))}


def atomic_write(value, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    if isinstance(value, pd.DataFrame):
        value.to_csv(temp, index=False, encoding="utf-8-sig")
    else:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def refresh(*, input_dir: Path, output_root: Path, sector_path: Path, as_of: str | None = None,
            min_delta: float = 3.0) -> dict:
    try:
        previous, current = select_week_pair(input_dir, as_of)
        cur, m, sources = calculate_pair(previous, current)
        hit = m[(m["whale_delta"] >= min_delta) & (m["retail_delta"] < 0)].copy()
        sec = pd.read_csv(sector_path, dtype=str)
        names = dict(zip(sec["Ticker"].str.zfill(4), zip(sec["Name"], sec["Sector"])))
        hit["name"] = [names.get(t, ("", ""))[0] for t in hit["ticker"]]
        hit["sector"] = [names.get(t, ("", ""))[1] for t in hit["ticker"]]
        hit = hit[hit["name"] != ""].sort_values("whale_delta", ascending=False)
        day = sources["current"]["date"]
        ranked = Path(f"logs/tdcc_whale_delta_{day}.csv")
        atomic_write(cur, output_root / SNAP)
        atomic_write(m, output_root / DELTAS)
        atomic_write(hit[["ticker", "name", "sector", "whale1000", "whale_delta", "retail100", "retail_delta"]], output_root / ranked)
        outputs = {p.as_posix(): sha(output_root / p) for p in (SNAP, DELTAS, ranked)}
        report = {"status": "OK", "as_of_date": day, "previous_date": sources["previous"]["date"],
                  "generated_at": datetime.now().astimezone().isoformat(), "sources": sources,
                  "definition": {"whale_levels": [15], "retail_levels": list(range(1, 10)),
                                 "min_whale_delta_pp": min_delta, "retail_delta_less_than": 0},
                  "named_radar_rows": len(hit), "outputs": outputs}
        atomic_write(report, output_root / STATUS)  # Commit marker last.
        return report
    except Exception as exc:
        atomic_write({"status": "FAILED", "generated_at": datetime.now().astimezone().isoformat(),
                      "error": f"{type(exc).__name__}: {exc}"}, output_root / STATUS)
        raise


def load_radar_artifact(base_dir: Path, *, not_after: str | None = None) -> tuple[pd.DataFrame, dict]:
    path = base_dir / STATUS
    if not path.exists():
        raise TDCCRadarDataError("TDCC radar lineage missing; legacy snapshot is unverified")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "OK":
        raise TDCCRadarDataError(f"TDCC radar status {report.get('status')}: {report.get('error', '')}")
    if not_after and report["as_of_date"] > date8(not_after):
        raise TDCCRadarDataError("TDCC radar newer than requested as-of")
    sources = report["sources"]
    if (date8(sources["current"]["date"]) != date8(report["as_of_date"])
            or date8(sources["previous"]["date"]) != date8(report["previous_date"])):
        raise TDCCRadarDataError("TDCC radar manifest source dates inconsistent")
    for key in ("previous", "current"):
        source = Path(sources[key]["path"])
        if source.stem.removeprefix("tdcc_") != date8(sources[key]["date"]):
            raise TDCCRadarDataError(f"TDCC radar {key} source filename/date mismatch")
        if not source.exists() or sha(source) != sources[key]["sha256"]:
            raise TDCCRadarDataError(f"TDCC radar {key} raw hash mismatch")
    _, latest = select_week_pair(Path(sources["current"]["path"]).parent, not_after)
    if latest.name != Path(sources["current"]["path"]).name:
        raise TDCCRadarDataError("TDCC radar lags latest available raw week")
    for relative, expected in report["outputs"].items():
        output = base_dir / relative
        if not output.exists() or sha(output) != expected:
            raise TDCCRadarDataError(f"TDCC radar derived hash mismatch: {relative}")
    m = pd.read_csv(base_dir / DELTAS, dtype={"ticker": str, "date": str, "prev_date": str})
    if m.empty or set(m["date"]) != {report["as_of_date"]} or set(m["prev_date"]) != {report["previous_date"]}:
        raise TDCCRadarDataError("TDCC radar derived dates incomplete or inconsistent")
    return m, report
