# ==============================================================================
# backfill_valuation.py - 回補台股每日本益比/淨值比/殖利率資料
#
# 來源:
#   TWSE: https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d
#   TPEx: https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php
#
# 用法: python scripts/backfill_valuation.py [--start-date 2024-01-01]
# ==============================================================================

import os
import sys
import time
import random
import argparse
from dataclasses import dataclass
from datetime import datetime, date, timedelta

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from tqdm import tqdm

# --- SSL ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 設定
# ==============================================================================
BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.valuation_source_contract import parse_official_valuation, parse_official_market_holidays
from scripts.taiwan_trading_calendar import is_taiwan_trading_day
VALUATION_DIR = os.path.join(BASE_DIR, "估值資料")
os.makedirs(VALUATION_DIR, exist_ok=True)

TWSE_RATE_LIMIT = 3.0  # seconds
TPEX_RATE_LIMIT = 3.0
MAX_RETRIES = 3
MIN_COMPLETE_ROWS = 1500
# The shared calendar explicitly documents a complete2026annual schedule.
# Adhoc closures in another year do not certify that year's whole calendar.
CANONICAL_FULL_CALENDAR_YEARS = frozenset({2026})
_VERIFIED_MARKET_YEAR_CLOSURES = {}


@dataclass(frozen=True)
class SourceResult:
    status: str
    frame: pd.DataFrame | None = None
    error: str = ""


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
        "Referer": "https://www.twse.com.tw/",
    })
    return s


SESSION = build_session()


# ==============================================================================
# 資料品質斷言：防止 API 欄位偷改導致錯誤資料默默寫入
# ==============================================================================
def _validate_valuation_df(df, source_label=""):
    """檢查估值 DataFrame 的數值範圍是否合理。

    若偵測到明顯異常（如殖利率平均 > 30%），拋出 ValueError 中斷寫入。
    """
    n = len(df)
    if n < 10:
        return  # 太少筆不做檢查

    # 殖利率：正常台股極少超過 30%，平均超過 30 幾乎確定是欄位錯位
    dy = pd.to_numeric(df["Dividend_Yield"], errors="coerce")
    dy_mean = dy.dropna().mean()
    if dy_mean > 30:
        raise ValueError(
            f"[{source_label}] 殖利率平均值 {dy_mean:.1f}% 異常飆高，"
            "TWSE/TPEx API 欄位可能已變更，請檢查爬蟲邏輯！"
        )

    # PE：正常平均值在 10~80 之間，如果平均值剛好等於某個年度（如 114, 113）就是抓到股利年度了
    pe = pd.to_numeric(df["PE_Ratio"], errors="coerce")
    # 用「中位數」偵測誤抓股利年度欄:若整欄是民國年(113-115),中位數必落在 100-120;
    # 真實 PE 中位數 ~20-30。舊版用平均值,店頭過熱期少數千倍 PE 會把平均拉破 100
    # 造成誤殺(2026-04~07 有 13 個交易日上櫃估值因此被拒收)。
    pe_median = pe.dropna().median()
    if 100 < pe_median < 120:
        raise ValueError(
            f"[{source_label}] PE 中位數 {pe_median:.1f}，"
            "疑似抓到股利年度欄位而非本益比，請檢查欄位映射！"
        )

    # PB：正常平均值在 0.5~10 之間，超過 15 很可能是欄位錯位
    pb = pd.to_numeric(df["PB_Ratio"], errors="coerce")
    pb_mean = pb.dropna().mean()
    if pb_mean > 15:
        raise ValueError(
            f"[{source_label}] PB 平均值 {pb_mean:.1f}，"
            "疑似欄位錯位（正常台股 PB 平均值不會超過 15），請檢查爬蟲邏輯！"
        )


# ==============================================================================
# TWSE 本益比/淨值比/殖利率
# ==============================================================================
def fetch_twse_valuation(date_str_8):
    """
    抓取 TWSE 每日本益比/淨值比/殖利率
    date_str_8: YYYYMMDD
    Returns: SourceResult(status=ok/no_data/error)
    """
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
    params = {"date": date_str_8, "response": "json"}

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=30, verify=False)
            if r.status_code != 200:
                time.sleep(TWSE_RATE_LIMIT)
                continue

            df = parse_official_valuation(r.json(), "上市", date_str_8)
            _validate_valuation_df(df, f"TWSE {date_str_8}")
            return SourceResult("ok", df)

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TWSE {date_str_8} 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(TWSE_RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TWSE {date_str_8} 失敗: {e}")
                return SourceResult("error", error=str(e))

    return SourceResult("error", error="TWSE HTTP 重試耗盡")


# ==============================================================================
# TPEx 本益比/淨值比/殖利率
# ==============================================================================
def fetch_tpex_valuation(date_obj):
    """
    抓取 TPEx 每日本益比/淨值比/殖利率
    date_obj: date object
    Returns: SourceResult(status=ok/no_data/error)
    """
    roc_year = date_obj.year - 1911
    tpex_date = f"{roc_year}/{date_obj.strftime('%m/%d')}"

    url = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"
    params = {
        "l": "zh-tw",
        "d": tpex_date,
        "o": "json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=30, verify=False)
            if r.status_code != 200:
                time.sleep(TPEX_RATE_LIMIT)
                continue

            df = parse_official_valuation(r.json(), "上櫃", date_obj.strftime("%Y%m%d"))
            _validate_valuation_df(df, f"TPEx {date_obj}")
            return SourceResult("ok", df)

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TPEx {date_obj} 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(TPEX_RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TPEx {date_obj} 失敗: {e}")
                return SourceResult("error", error=str(e))

    return SourceResult("error", error="TPEx HTTP 重試耗盡")


# ==============================================================================
# 主程式
# ==============================================================================
def generate_trading_days(start_date, end_date):
    """Use the shared calendar and verify any unsupported historical year."""
    for year in range(start_date.year,end_date.year+1):
        if year in CANONICAL_FULL_CALENDAR_YEARS or year in _VERIFIED_MARKET_YEAR_CLOSURES:
            continue
        response=SESSION.get("https://www.twse.com.tw/holidaySchedule/holidaySchedule",
            params={"date":f"{year}0101","response":"json"},timeout=30,verify=False)
        response.raise_for_status()
        _VERIFIED_MARKET_YEAR_CLOSURES[year]=parse_official_market_holidays(response.json(),year)
    days = []
    d = start_date
    while d <= end_date:
        if is_taiwan_trading_day(d) and d not in _VERIFIED_MARKET_YEAR_CLOSURES.get(d.year,set()):
            days.append(d)
        d += timedelta(days=1)
    return days


def valuation_row_count(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except OSError:
        return 0


def select_pending_days(trading_days, valuation_dir=VALUATION_DIR, force=False):
    """找缺檔或殘檔；保留現有檔案到完整替代品通過驗證。"""
    pending = []
    for day in trading_days:
        filepath = os.path.join(valuation_dir, f"valuation_{day:%Y%m%d}.csv")
        rows = valuation_row_count(filepath)
        if force or rows == 0:
            pending.append(day)
        elif rows < MIN_COMPLETE_ROWS:
            print(f"  valuation_{day:%Y%m%d}.csv 殘檔({rows} 列),保留舊檔並重抓")
            pending.append(day)
    return pending


def combine_complete_sources(twse_result, tpex_result):
    """雙市場皆成功且總列數合理時才產生可寫入資料。"""
    results = {"TWSE": twse_result, "TPEx": tpex_result}
    failures = [
        f"{name}={result.status}{f'({result.error})' if result.error else ''}"
        for name, result in results.items()
        if result.status != "ok"
    ]
    if failures:
        raise ValueError("估值來源不完整: " + ", ".join(failures))

    combined = pd.concat(
        [twse_result.frame, tpex_result.frame],
        ignore_index=True,
    )
    combined = combined[["Ticker", "Name", "PE_Ratio", "PB_Ratio", "Dividend_Yield", "Market"]]
    combined["Ticker"] = combined["Ticker"].astype(str).str.strip()
    if combined["Ticker"].duplicated().any():
        raise ValueError("估值雙市場有重複券碼，可能期別或來源市場錯置")
    if len(combined) < MIN_COMPLETE_ROWS:
        raise ValueError(f"估值合併後僅 {len(combined)} 列 < {MIN_COMPLETE_ROWS}")
    if set(combined["Market"].dropna().astype(str)) != {"上市", "上櫃"}:
        raise ValueError("估值合併後未同時包含上市與上櫃市場")
    return combined


def write_valuation_atomic(combined, filepath):
    tmp_path = filepath + ".tmp"
    try:
        combined.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, filepath)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def main():
    parser = argparse.ArgumentParser(description="回補台股每日估值資料 (PE/PB/殖利率)")
    parser.add_argument("--start-date", type=str, default="2024-01-01",
                        help="起始日期 YYYY-MM-DD (預設 2024-01-01)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="結束日期 YYYY-MM-DD (預設今天)")
    parser.add_argument("--force", action="store_true",
                        help="強制覆蓋已存在的檔案（用於修復欄位錯誤）")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()

    print("=" * 60)
    print("回補台股每日估值資料 (PE/PB/殖利率)")
    print(f"  範圍: {start_date} ~ {end_date}")
    print(f"  儲存: {VALUATION_DIR}")
    print("=" * 60)

    trading_days = generate_trading_days(start_date, end_date)

    # 殘檔保留至雙市場完整抓取成功，避免 source outage 讓資料倒退。
    pending_days = select_pending_days(trading_days, force=args.force)

    if args.force:
        print(f"  [FORCE] 強制模式：覆蓋所有 {len(pending_days)} 天的檔案")
    else:
        print(f"  交易日共 {len(trading_days)} 天, 待下載 {len(pending_days)} 天, 已完成 {len(trading_days) - len(pending_days)} 天")

    if not pending_days:
        print("  所有日期皆已下載完畢!")
        return

    success_count = 0
    fail_count = 0
    skip_count = 0
    failed_dates = []

    for d in tqdm(pending_days, desc="估值資料"):
        date_str_8 = d.strftime("%Y%m%d")
        filepath = os.path.join(VALUATION_DIR, f"valuation_{date_str_8}.csv")

        # TWSE
        twse_result = fetch_twse_valuation(date_str_8)
        time.sleep(TWSE_RATE_LIMIT + random.uniform(0, 1))

        # TPEx
        tpex_result = fetch_tpex_valuation(d)
        time.sleep(TPEX_RATE_LIMIT + random.uniform(0, 1))

        if twse_result.status == "no_data" and tpex_result.status == "no_data":
            skip_count += 1
            continue

        try:
            combined = combine_complete_sources(twse_result, tpex_result)
            write_valuation_atomic(combined, filepath)
            success_count += 1
        except ValueError as exc:
            fail_count += 1
            failed_dates.append(date_str_8)
            tqdm.write(f"  {date_str_8}: {exc}; 保留既有檔案")

    # 摘要
    print("\n" + "=" * 60)
    print("回補完成")
    print(f"  成功: {success_count} 天")
    print(f"  略過(非交易日/已存在): {skip_count} 天")
    print(f"  失敗: {fail_count} 天")
    if failed_dates:
        print(f"  失敗日期: {', '.join(failed_dates[:20])}")
    print("=" * 60)
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
