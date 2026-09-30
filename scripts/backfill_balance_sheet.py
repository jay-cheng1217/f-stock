# ==============================================================================
# backfill_balance_sheet.py - 回補 MOPS 資產負債表 (t163sb05)
#
# 提取: 流動資產、資產總計、流動負債、負債總計、股東權益、每股淨值
# 衍生: 負債比、流動比率、ROE、ROA (需搭配季報損益)
#
# 用法: python scripts/backfill_balance_sheet.py [--start-year 2020]
# ==============================================================================

import os
import re
import sys
import io
import time
import random
import argparse
from datetime import date, timedelta

import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup
from tqdm import tqdm

# Force UTF-8 output on Windows
if sys.platform == "win32":
    def _ensure_utf8_stream(stream):
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

# --- SSL ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
try:
    from scripts.quarterly_source_contract import verify_market_source, write_verified_quarter
except ModuleNotFoundError:
    from quarterly_source_contract import verify_market_source, write_verified_quarter

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS_DIR = os.path.join(BASE_DIR, "資產負債")
os.makedirs(BS_DIR, exist_ok=True)

RATE_LIMIT = 5.0
MAX_RETRIES = 3
COMPLETENESS_RATIO = 0.8
FILING_GRACE_DAYS = 3

QUARTER_DEADLINES = (
    ((3, 31), lambda filing_year: (filing_year - 1, 4)),
    ((5, 15), lambda filing_year: (filing_year, 1)),
    ((8, 14), lambda filing_year: (filing_year, 2)),
    ((11, 14), lambda filing_year: (filing_year, 3)),
)

MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb05"
MOPS_REFERER = "https://mopsov.twse.com.tw/mops/web/t163sb05"


def build_session():
    s = requests.Session()
    retries = Retry(
        total=5, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": MOPS_REFERER,
    })
    return s


SESSION = build_session()


def fetch_balance_sheet(year_roc, season, typek):
    """從 MOPS 批次抓取資產負債表"""
    payload = {
        "encodeURIComponent": "1",
        "step": "2",
        "firstin": "1",
        "off": "1",
        "TYPEK": typek,
        "year": str(year_roc),
        "season": str(season),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": MOPS_REFERER,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.post(MOPS_URL, data=payload, headers=headers, timeout=90, verify=False)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"    重試 ({attempt+1}): {e}")
                time.sleep(RATE_LIMIT * 2)
            else:
                tqdm.write(f"    失敗: {e}")
                return None
    return None


def _clean_num(val):
    """清理數值字串"""
    if not val or val in ("--", "-", "N/A", ""):
        return np.nan
    cleaned = val.replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return np.nan


_BS_HEADER_ALIASES = {
    "Current_Assets": ("流動資產", "流動資產合計"),
    "Total_Assets": ("資產總計", "資產總額"),
    "Current_Liabilities": ("流動負債", "流動負債合計"),
    "Total_Liabilities": ("負債總計", "負債總額"),
    "Share_Capital": ("股本",),
    "Parent_Equity": (
        "歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益",
        "歸屬於母公司業主權益合計",
    ),
    "Total_Equity": ("權益總計", "權益總額"),
    "Book_Value_Per_Share": ("每股參考淨值", "每股淨值"),
}


def _expanded_bs_rows(table):
    """Expand real HTML cell spans before resolving each table's named columns."""
    spans = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        row = {column: value for column, (value, remaining) in spans.items()}
        spans = {column: (value, remaining - 1) for column, (value, remaining) in spans.items() if remaining > 1}
        column = 0
        for cell in cells:
            while column in row:
                column += 1
            value = cell.get_text(" ", strip=True)
            width = int(cell.get("colspan", 1))
            height = int(cell.get("rowspan", 1))
            if width < 1 or height < 1:
                raise ValueError("Invalid balance-sheet HTML span")
            for offset in range(width):
                pos = column + offset
                if pos in row:
                    raise ValueError("Overlapping balance-sheet HTML spans")
                row[pos] = value
                if height > 1:
                    spans[pos] = (value, height - 1)
            column += width
        yield [row.get(pos, "") for pos in range(max(row, default=-1) + 1)]


def _bs_column_positions(headers):
    normalized = [re.sub(r"\s+", "", text) for text in headers]
    positions = {}
    for field, aliases in _BS_HEADER_ALIASES.items():
        matches = [index for index, name in enumerate(normalized) if name in aliases]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous balance-sheet header: {field}")
        positions[field] = matches[0] if matches else None
    required = ("Total_Assets", "Total_Liabilities", "Total_Equity", "Book_Value_Per_Share")
    if any(positions[field] is None for field in required):
        raise ValueError("Unrecognized balance-sheet total/BVPS headers")
    return positions


def parse_balance_sheet(html_text):
    """Read each industry's actual named fields; absent categories remain NaN."""
    if not html_text:
        return pd.DataFrame()

    soup = BeautifulSoup(html_text, "lxml")
    tables = soup.find_all("table", class_="hasBorder")
    if not tables:
        return pd.DataFrame()

    all_rows = []
    for table in tables:
        headers = None
        positions = None
        for tds in _expanded_bs_rows(table):
            if not tds:
                continue
            first = re.sub(r"\s+", "", tds[0])
            if first == "公司代號":
                headers = tds
                positions = None
                continue
            ticker = first.replace(",", "")
            if not re.fullmatch(r"\d{4,6}", ticker):
                continue
            if headers is None or len(tds) != len(headers):
                raise ValueError(f"Missing/misaligned balance-sheet header for {ticker}")
            if positions is None:
                positions = _bs_column_positions(headers)
            row = {"Ticker": ticker, "Name": tds[1].strip()}
            for field, position in positions.items():
                row[field] = _clean_num(tds[position]) if position is not None else np.nan
            all_rows.append(row)

    if all_rows:
        result = pd.DataFrame(all_rows)
        if result.Ticker.duplicated().any():
            raise ValueError("Duplicate balance-sheet ticker")
        return result
    return pd.DataFrame()


def get_latest_available_quarter(today=None):
    today = today or date.today()
    candidates = []
    for filing_year in (today.year - 1, today.year):
        for (month, day), resolve in QUARTER_DEADLINES:
            due = date(filing_year, month, day) + timedelta(days=FILING_GRACE_DAYS)
            if due <= today:
                candidates.append(resolve(filing_year))
    if not candidates:
        raise RuntimeError(f"no filed balance-sheet quarter is available for {today}")
    return max(candidates)


def generate_quarters(start_year, start_q, end_year, end_q):
    quarters = []
    for y in range(start_year, end_year + 1):
        for q in range(1, 5):
            if y == start_year and q < start_q:
                continue
            if y == end_year and q > end_q:
                break
            quarters.append((y, q))
    return quarters


def _row_count(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except OSError:
        return 0


def _previous_reference_rows(quarters, index, directory=BS_DIR):
    for y, q in reversed(quarters[:index]):
        rows = _row_count(os.path.join(directory, f"bs_{y}Q{q}.csv"))
        if rows:
            return rows
    return 0


def select_pending_quarters(quarters, directory=BS_DIR):
    pending = []
    for index, (y, q) in enumerate(quarters):
        path = os.path.join(directory, f"bs_{y}Q{q}.csv")
        rows = _row_count(path)
        reference_rows = _previous_reference_rows(quarters, index, directory)
        if (y, q) == max(quarters) or rows == 0 or (reference_rows and rows < reference_rows * COMPLETENESS_RATIO):
            pending.append((y, q))
    return pending


def combine_complete_markets(market_frames, reference_rows=0):
    required = {"TWSE", "OTC"}
    available = {
        market for market, frame in market_frames.items()
        if frame is not None and not frame.empty
    }
    missing = required - available
    if missing:
        raise ValueError(f"missing balance-sheet source: {', '.join(sorted(missing))}")

    result = pd.concat([market_frames[market] for market in ("TWSE", "OTC")], ignore_index=True)
    if reference_rows and len(result) < reference_rows * COMPLETENESS_RATIO:
        raise ValueError(
            f"incomplete balance-sheet replacement: {len(result)} rows < "
            f"previous {reference_rows} rows x {COMPLETENESS_RATIO:.0%}"
        )
    return result


def write_quarter_atomic(result, filepath, *, evidence):
    return write_verified_quarter(result, filepath, evidence=evidence)


def main():
    parser = argparse.ArgumentParser(description="回補 MOPS 資產負債表")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--start-quarter", type=int, default=1)
    args = parser.parse_args()

    end_year, end_q = get_latest_available_quarter()

    print("=" * 60)
    print("回補 MOPS 資產負債表 (t163sb05)")
    print(f"  範圍: {args.start_year}Q{args.start_quarter} ~ {end_year}Q{end_q}")
    print(f"  儲存: {BS_DIR}")
    print("=" * 60)

    quarters = generate_quarters(args.start_year, args.start_quarter, end_year, end_q)
    pending = select_pending_quarters(quarters)

    print(f"  季度共 {len(quarters)} 個, 待下載 {len(pending)} 個")

    if not pending:
        print("  全部完成!")
        return 0

    success = 0
    fail = 0

    for y, q in tqdm(pending, desc="資產負債表"):
        filepath = os.path.join(BS_DIR, f"bs_{y}Q{q}.csv")
        roc_year = y - 1911
        market_frames = {}
        evidence = {}

        for typek in ["sii", "otc"]:
            market = "TWSE" if typek == "sii" else "OTC"
            try:
                html = fetch_balance_sheet(roc_year, q, typek)
                df = parse_balance_sheet(html)
                if not df.empty:
                    evidence[market] = verify_market_source(
                        html, df, "bs", y, q, market, SESSION,
                        latest=(y, q) == (end_year, end_q))
                    df["Market"] = market
                    market_frames[market] = df
                    tqdm.write(f"    {y}Q{q} ({market}): {len(df)} 筆")
            except Exception as exc:
                tqdm.write(f"    {y}Q{q} ({market}) source verification failed: {exc}")
            time.sleep(RATE_LIMIT + random.uniform(0, 3))

        quarter_index = quarters.index((y, q))
        reference_rows = _previous_reference_rows(quarters, quarter_index)
        try:
            result = combine_complete_markets(market_frames, reference_rows)
            result["Year"] = y
            result["Season"] = q

            # 計算衍生比率
            result["Debt_Ratio"] = (
                result["Total_Liabilities"] / result["Total_Assets"].replace(0, np.nan) * 100
            ).round(2)
            result["Current_Ratio"] = (
                result["Current_Assets"] / result["Current_Liabilities"].replace(0, np.nan) * 100
            ).round(2)

            write_quarter_atomic(result, filepath, evidence=evidence)
            success += 1
        except ValueError as exc:
            tqdm.write(f"  {y}Q{q}: {exc}; existing file preserved")
            fail += 1

    print(f"\n完成: 成功 {success}, 失敗 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
