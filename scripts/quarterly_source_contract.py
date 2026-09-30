"""Shared quarterly L1 contracts; no network or writes occur at import time."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile

from bs4 import BeautifulSoup
import pandas as pd

CONTRACT_VERSION = 1
METRICS = {
    "eps": ["EPS_Basic"],
    "financial": ["Revenue_M", "Gross_Margin_Pct", "Operating_Margin_Pct",
                  "Pretax_Margin_Pct", "Net_Margin_Pct"],
    "bs": ["Current_Assets", "Total_Assets", "Current_Liabilities",
           "Total_Liabilities", "Share_Capital", "Parent_Equity",
           "Total_Equity", "Book_Value_Per_Share", "Debt_Ratio", "Current_Ratio"],
}
ENDPOINT = {"financial": "t163sb06", "eps": "t163sb04", "bs": "t163sb05"}


def validate_html_period(html, kind, year, season, market):
    """Only source headings prove response period; numeric data cannot do so."""
    if kind not in ENDPOINT or market not in {"TWSE", "OTC"} or season not in range(1, 5):
        raise ValueError("unsupported quarterly request")
    titles = [re.sub(r"\s+", "", h.get_text())
              for h in BeautifulSoup(html or "", "lxml").find_all("h2")]
    prefix = "上市公司" if market == "TWSE" else "上櫃公司"
    quarter = "第" + "一二三四"[season - 1] + "季"
    expected = prefix + (str(year - 1911) + "年度" if kind == "financial" else "") + quarter
    if len(titles) != 1 or titles[0] not in {expected, expected + "資料"}:
        raise ValueError(f"{kind} response heading does not match {year}Q{season} {market}: {titles}")
    return {"title": titles[0], "request_year": year, "request_roc_year": year - 1911,
            "request_quarter": season, "market": market,
            "response_year_explicit": kind == "financial"}


def _number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return math.nan


def validate_latest_anchor(payload, frame, kind, year, season, market):
    """A same-run explicit-year official source corroborates yearless EPS/BS HTML."""
    if not isinstance(payload, list) or not payload or not all(isinstance(r, dict) for r in payload):
        raise ValueError("official period anchor is empty or has wrong schema")
    anchors = {}
    for row in payload:
        ry = row.get("年度", row.get("Year"))
        rq = row.get("季別", row.get("Season"))
        if _number(ry) != year - 1911 or _number(rq) != season:
            raise ValueError("official anchor year/quarter mismatch")
        ticker = str(row.get("公司代號", row.get("SecuritiesCompanyCode", ""))).strip()
        if not re.fullmatch(r"\d{4,6}", ticker) or ticker in anchors:
            raise ValueError("official anchor ticker schema/uniqueness failure")
        anchors[ticker] = row
    fields = {"EPS_Basic": "基本每股盈餘（元）"} if kind == "eps" else {
        "Total_Assets": "資產總計", "Total_Liabilities": "負債總計",
        "Total_Equity": "權益總計", "Book_Value_Per_Share": "每股參考淨值"}
    rows = frame.assign(Ticker=frame.Ticker.astype(str)).set_index("Ticker")
    common = sorted(set(rows.index) & set(anchors))[:5]
    if len(common) < 5:
        raise ValueError("official period anchor has fewer than five common companies")
    for ticker in common:
        for local, official in fields.items():
            a, b = _number(rows.loc[ticker, local]), _number(anchors[ticker].get(official))
            if not (math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, abs_tol=1e-6, rel_tol=1e-10)):
                raise ValueError(f"official anchor value mismatch: {ticker}/{local}")
    return {"market": market, "year": year, "quarter": season,
            "sample_tickers": common, "compared_fields": list(fields),
            "source_rows": len(payload)}


def verify_market_source(html, frame, kind, year, season, market, session, *, latest):
    proof = validate_html_period(html, kind, year, season, market)
    proof.update({"kind": kind, "html": html,
                  "url": f"https://mopsov.twse.com.tw/mops/web/ajax_{ENDPOINT[kind]}",
                  "year_evidence": "response_heading" if kind == "financial" else "request_only"})
    if kind in {"eps", "bs"} and latest:
        number = "06" if kind == "eps" else "07"
        url = (f"https://openapi.twse.com.tw/v1/opendata/t187ap{number}_L_ci" if market == "TWSE"
               else f"https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap{number}_O_ci")
        response = session.get(url, timeout=60)
        response.raise_for_status()
        payload = response.json()
        proof["anchor"] = validate_latest_anchor(payload, frame, kind, year, season, market)
        proof.update({"anchor_url": url, "anchor_json": response.text,
                      "year_evidence": "request_plus_same_run_official_period_value_anchor"})
    return proof


def validate_quarter_frame(frame, kind, year, season):
    required = {"Ticker", "Name", "Market", "Year", "Season", *METRICS[kind]}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError(f"{kind} quarterly empty/schema failure: {sorted(required - set(frame.columns))}")
    tickers = frame.Ticker.astype(str)
    if not tickers.str.fullmatch(r"\d{4,6}").all() or tickers.duplicated().any():
        raise ValueError("quarterly ticker schema/uniqueness failure")
    if not pd.to_numeric(frame.Year, errors="coerce").eq(year).all() or not pd.to_numeric(frame.Season, errors="coerce").eq(season).all():
        raise ValueError("quarterly row period mismatch")
    if set(frame.Market) != {"TWSE", "OTC"}:
        raise ValueError("quarterly dual-market contract failure")
    for metric in METRICS[kind]:
        numeric = pd.to_numeric(frame[metric], errors="coerce")
        invalid = frame[metric].notna() & numeric.isna()
        if invalid.any() or numeric.abs().eq(float("inf")).any():
            raise ValueError(f"quarterly invalid numeric field: {metric}")
    primary = {"eps": "EPS_Basic", "financial": "Operating_Margin_Pct", "bs": "Total_Assets"}[kind]
    for market in ("TWSE", "OTC"):
        if pd.to_numeric(frame.loc[frame.Market.eq(market), primary], errors="coerce").notna().sum() == 0:
            raise ValueError(f"quarterly {market} has no observed {primary}")
    if kind == "bs":
        accounts = frame[["Total_Assets", "Total_Liabilities", "Total_Equity"]].apply(pd.to_numeric, errors="coerce")
        known = accounts.notna().all(axis=1)
        residual = accounts.Total_Assets - accounts.Total_Liabilities - accounts.Total_Equity
        if residual.loc[known].abs().gt(2).any():
            raise ValueError("balance-sheet accounting identity failure (tolerance 2 source units)")


def _atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def write_verified_quarter(result, filepath, *, evidence):
    """Validate fresh official data, preserve old filed keys, atomically replace CSV."""
    path = Path(filepath)
    match = re.fullmatch(r"(financial|eps|bs)_(\d{4})Q([1-4])\.csv", path.name)
    if not match:
        raise ValueError("unsupported quarterly filename")
    kind, year, season = match[1], int(match[2]), int(match[3])
    validate_quarter_frame(result, kind, year, season)
    if set(evidence) != {"TWSE", "OTC"}:
        raise ValueError("quarterly source evidence missing a market")
    for market, proof in evidence.items():
        if (proof.get("kind"), proof.get("request_year"), proof.get("request_quarter"), proof.get("market")) != (kind, year, season, market):
            raise ValueError("quarterly evidence period/schema mismatch")
    before = pd.read_csv(path, dtype={"Ticker": str}) if path.exists() else pd.DataFrame(columns=result.columns)
    retained = before.iloc[0:0]
    if not before.empty:
        # Official data must be able to repair corrupt old numeric/schema cells.
        # Validate old identity first; only omitted rows need their old values.
        if not {"Ticker", "Year", "Season"}.issubset(before.columns):
            raise ValueError("existing quarterly identity columns missing")
        if before.Ticker.duplicated().any() or not before.Ticker.str.fullmatch(r"\d{4,6}").all():
            raise ValueError("existing quarterly ticker schema/uniqueness failure")
        if not pd.to_numeric(before.Year, errors="coerce").eq(year).all() or not pd.to_numeric(before.Season, errors="coerce").eq(season).all():
            raise ValueError("existing quarterly row period mismatch")
        retained = before.loc[~before.Ticker.isin(result.Ticker.astype(str))]
        if not retained.empty and set(before.columns) != set(result.columns):
            raise ValueError("retained omitted filings have unverifiable schema; existing file preserved")
    merged = (pd.concat([result, retained], ignore_index=True) if not retained.empty else result.copy())
    merged = merged.sort_values("Ticker").reset_index(drop=True)
    validate_quarter_frame(merged, kind, year, season)
    raw_dir = path.parent.parent / "logs" / "quarterly_source_evidence"
    saved = {}
    for market, proof in evidence.items():
        entry = {k: v for k, v in proof.items() if k not in {"html", "anchor_json"}}
        for key, suffix in (("html", ".html"), ("anchor_json", ".json")):
            if key in proof:
                payload = proof[key].encode("utf-8")
                digest = hashlib.sha256(payload).hexdigest()
                target = raw_dir / (digest + suffix)
                _atomic_bytes(target, payload)
                entry[key + "_sha256"] = digest
                entry[key + "_path"] = str(target)
        saved[market] = entry
    payload = merged.to_csv(index=False).encode("utf-8-sig")
    receipt = {"contract_version": CONTRACT_VERSION, "knowledge_time_utc": datetime.now(timezone.utc).isoformat(),
               "kind": kind, "year": year, "quarter": season, "official_rows": len(result),
               "retained_previously_filed_tickers": retained.Ticker.tolist(), "merged_rows": len(merged),
               "before_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
               "after_sha256": hashlib.sha256(payload).hexdigest(), "sources": saved}
    _atomic_bytes(path, payload)
    _atomic_bytes(raw_dir / (path.stem + "_latest.json"), json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"))
    return receipt
