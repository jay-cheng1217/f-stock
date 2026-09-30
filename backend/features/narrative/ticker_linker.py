from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

import duckdb
import pandas as pd

from backend.config import DUCKDB_PATH


TICKER_WITH_SUFFIX_RE = re.compile(r"(?<!\d)(?P<ticker>\d{4})\s*(?:[-.]?\s*(?:TW|TWO|TT|台股))(?!\d)", re.I)
PAREN_TICKER_RE = re.compile(r"[（(](?P<ticker>\d{4})[）)]")
AMBIGUOUS_COMPANY_ALIASES = {
    "三星",
    "大量",
    "新興",
    "聯發",
    "時報",
    "力士",
}


@dataclass(frozen=True)
class TickerLink:
    ticker: str
    match_method: str
    confidence: float
    matched_text: str


def _clean_alias(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _read_aliases_from_duckdb(path: str = DUCKDB_PATH) -> dict[str, set[str]]:
    if not os.path.exists(path):
        return {}
    con = duckdb.connect(path, read_only=True)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        frames: list[pd.DataFrame] = []
        if "stock_list" in tables:
            frames.append(con.execute("SELECT Ticker, Name FROM stock_list WHERE Name IS NOT NULL").fetchdf())
        if "financials" in tables:
            frames.append(
                con.execute(
                    "SELECT DISTINCT CAST(Ticker AS VARCHAR) AS Ticker, Name FROM financials WHERE Name IS NOT NULL"
                ).fetchdf()
            )
    finally:
        con.close()

    aliases: dict[str, set[str]] = {}
    for frame in frames:
        for _, row in frame.iterrows():
            ticker = str(row.get("Ticker") or "").strip()
            name = _clean_alias(str(row.get("Name") or ""))
            if not ticker or not ticker.isdigit() or not name:
                continue
            aliases.setdefault(ticker, set()).add(name)
            aliases[ticker].add(name.replace("股份有限公司", "").replace("有限公司", ""))
    return aliases


def load_aliases_from_conn(con: duckdb.DuckDBPyConnection) -> dict[str, set[str]]:
    tables = {
        row[0]
        for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    frames: list[pd.DataFrame] = []
    if "stock_list" in tables:
        frames.append(con.execute("SELECT Ticker, Name FROM stock_list WHERE Name IS NOT NULL").fetchdf())
    if "financials" in tables:
        frames.append(
            con.execute(
                "SELECT DISTINCT CAST(Ticker AS VARCHAR) AS Ticker, Name FROM financials WHERE Name IS NOT NULL"
            ).fetchdf()
        )

    aliases: dict[str, set[str]] = {}
    for frame in frames:
        for _, row in frame.iterrows():
            ticker = str(row.get("Ticker") or "").strip()
            name = _clean_alias(str(row.get("Name") or ""))
            if not ticker or not ticker.isdigit() or not name:
                continue
            aliases.setdefault(ticker, set()).add(name)
            aliases[ticker].add(name.replace("股份有限公司", "").replace("有限公司", ""))
    return aliases


@lru_cache(maxsize=1)
def load_default_aliases() -> dict[str, set[str]]:
    return _read_aliases_from_duckdb()


class TickerLinker:
    """Deterministic title/snippet linker: explicit ticker, then company dictionary."""

    def __init__(self, aliases: dict[str, Iterable[str]] | None = None):
        raw_aliases = aliases if aliases is not None else load_default_aliases()
        self.aliases: dict[str, set[str]] = {
            str(ticker): {_clean_alias(alias) for alias in values if _clean_alias(alias)}
            for ticker, values in raw_aliases.items()
        }
        self.known_tickers = set(self.aliases)
        self._alias_pairs = sorted(
            (
                (alias, ticker)
                for ticker, values in self.aliases.items()
                for alias in values
                if len(alias) >= 2
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )

    def link(self, title: str, snippet: str = "", ticker_hint: str | None = None) -> list[TickerLink]:
        text = _clean_alias(f"{title or ''} {snippet or ''}")
        out: dict[str, TickerLink] = {}

        hint = str(ticker_hint or "").strip()
        if hint and (not self.known_tickers or hint in self.known_tickers):
            out[hint] = TickerLink(hint, "ticker_regex", 1.0, hint)

        for match in TICKER_WITH_SUFFIX_RE.finditer(title or ""):
            ticker = match.group("ticker")
            if not self.known_tickers or ticker in self.known_tickers:
                out[ticker] = TickerLink(ticker, "ticker_regex", 0.98, match.group(0))

        for match in PAREN_TICKER_RE.finditer(title or ""):
            ticker = match.group("ticker")
            if ticker in out:
                continue
            if not self.known_tickers or ticker in self.known_tickers:
                out[ticker] = TickerLink(ticker, "ticker_regex", 0.95, match.group(0))

        matched_aliases: list[str] = []
        for alias, ticker in self._alias_pairs:
            if ticker in out:
                continue
            if alias in AMBIGUOUS_COMPANY_ALIASES:
                continue
            if any(alias in chosen for chosen in matched_aliases):
                continue
            if alias and alias in text:
                out[ticker] = TickerLink(ticker, "company_dict", 0.9, alias)
                matched_aliases.append(alias)

        return sorted(out.values(), key=lambda link: (-link.confidence, link.ticker))
