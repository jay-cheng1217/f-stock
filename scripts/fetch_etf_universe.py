"""Fetch Taiwan ETF universe from the official TWSE ISIN listing.

Source:
  https://isin.twse.com.tw/isin/C_public.jsp?strMode=2  (TWSE listed)
  https://isin.twse.com.tw/isin/C_public.jsp?strMode=4  (TPEx listed)

Output:
  ml/data/etf_universe.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.universe import infer_etf_type  # noqa: E402

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUTPUT_PATH = BASE_DIR / "ml" / "data" / "etf_universe.csv"
ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp"
SOURCES = (
    {"mode": 2, "market": "TWSE", "market_label": "上市"},
    {"mode": 4, "market": "TPEx", "market_label": "上櫃"},
)
FIELDNAMES = [
    "ticker",
    "name",
    "market",
    "market_label",
    "listing_date",
    "isin",
    "cfi_code",
    "ticker_suffix",
    "strategy",
    "asset_class",
    "leverage_type",
    "universe_type",
    "source_mode",
    "source_url",
]


def build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://isin.twse.com.tw/",
        }
    )
    return session


def decode_exchange_html(content: bytes) -> str:
    for encoding in ("cp950", "big5", "utf-8", "big5hkscs"):
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("cp950", errors="replace")


def split_code_name(value: str) -> tuple[str, str] | None:
    match = re.match(r"^([0-9A-Z]{4,8})\s+(.+)$", value.strip())
    if not match:
        return None
    return match.group(1).upper(), match.group(2).strip()


def ticker_suffix(ticker: str) -> str:
    match = re.search(r"([A-Z]+)$", ticker)
    return match.group(1) if match else ""


def infer_strategy(ticker: str, name: str) -> str:
    suffix = ticker_suffix(ticker)
    if suffix in {"A", "D"} or "主動" in name:
        return "active"
    return "passive"


def infer_leverage_type(ticker: str, name: str) -> str:
    suffix = ticker_suffix(ticker)
    if suffix == "R" or "反" in name:
        return "inverse"
    if suffix == "L" or "正2" in name or "槓桿" in name:
        return "leveraged"
    return "plain"


def infer_asset_class(ticker: str, name: str) -> str:
    suffix = ticker_suffix(ticker)
    if suffix in {"B", "D"} or "債" in name:
        return "bond"
    if suffix == "U" or "期" in name:
        return "futures"
    if "多資產" in name:
        return "multi_asset"
    return "equity"


def parse_isin_etfs(html: str, *, market: str, market_label: str, mode: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="h4")
    if table is None:
        raise RuntimeError(f"Cannot find ISIN table for strMode={mode}")

    records: list[dict[str, str]] = []
    in_etf_section = False
    source_url = f"{ISIN_URL}?strMode={mode}"

    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) == 1 and cells[0].get("colspan"):
            in_etf_section = cells[0].get_text(strip=True) == "ETF"
            continue
        if not in_etf_section or len(cells) < 6:
            continue

        values = [cell.get_text(" ", strip=True) for cell in cells]
        parsed = split_code_name(values[0])
        if parsed is None:
            continue

        ticker, name = parsed
        listing_date = values[2].replace("/", "-")
        suffix = ticker_suffix(ticker)
        strategy = infer_strategy(ticker, name)
        asset_class = infer_asset_class(ticker, name)
        leverage_type = infer_leverage_type(ticker, name)
        records.append(
            {
                "ticker": ticker,
                "name": name,
                "market": market,
                "market_label": market_label,
                "listing_date": listing_date,
                "isin": values[1],
                "cfi_code": values[5] if len(values) > 5 else "",
                "ticker_suffix": suffix,
                "strategy": strategy,
                "asset_class": asset_class,
                "leverage_type": leverage_type,
                "universe_type": infer_etf_type(
                    ticker,
                    name=name,
                    asset_class=asset_class,
                    strategy=strategy,
                    leverage_type=leverage_type,
                ),
                "source_mode": str(mode),
                "source_url": source_url,
            }
        )

    return records


def fetch_etf_universe(session: requests.Session | None = None) -> list[dict[str, str]]:
    session = session or build_session()
    records: list[dict[str, str]] = []
    for source in SOURCES:
        response = session.get(ISIN_URL, params={"strMode": source["mode"]}, timeout=60, verify=False)
        response.raise_for_status()
        html = decode_exchange_html(response.content)
        records.extend(parse_isin_etfs(html, **source))

    deduped: dict[str, dict[str, str]] = {}
    for row in records:
        deduped.setdefault(row["ticker"], row)
    return sorted(deduped.values(), key=lambda item: (item["ticker"], item["market"]))


def write_universe(rows: list[dict[str, str]], path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch official Taiwan ETF universe.")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    args = parser.parse_args(argv)

    rows = fetch_etf_universe()
    output = Path(args.output)
    write_universe(rows, output)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["universe_type"]] = counts.get(row["universe_type"], 0) + 1
    print(f"[etf-universe] wrote {len(rows)} rows to {output}")
    print("[etf-universe] " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
