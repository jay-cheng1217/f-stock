# ==============================================================================
# twstock.py - 台股資料整合更新主程式
#
# 更新順序：
#   Step 1: 日K資料 (yfinance)
#   Step 2: 法人買賣超 (TWSE + TPEx)
#   Step 3: 融資融券 (TWSE + TPEx)
#   Step 4: 合併法人資料 → 日K
#   Step 5: 合併融資券資料 → 日K
#   Step 6: 月營收
#   Step 7: 重算技術指標
#   Step 11: 外資持股比率 (TWSE + TPEx)
# ==============================================================================

import os
import sys
import time
import json
import random
import re
import html
import shutil
import importlib
import argparse
from io import StringIO
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup
from tqdm import tqdm

# --- SSL ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 路徑設定
# ==============================================================================
BASE_DIR = r"F:\stock"
DAILY_K_DIR = os.path.join(BASE_DIR, "日K資料")
FUND_CACHE_DIR = os.path.join(BASE_DIR, "法人快取")
RAW_MARGIN_DIR = os.path.join(BASE_DIR, "原始融資資料")
CLEANED_DATA_DIR = os.path.join(BASE_DIR, "清理後資料")
REVENUE_DIR = os.path.join(BASE_DIR, "月營收")

FINANCIAL_DIR = os.path.join(BASE_DIR, "季報財務")
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
INDEX_DIR = os.path.join(BASE_DIR, "大盤指數")

FOREIGN_OWN_DIR = os.path.join(BASE_DIR, "外資持股")

CLEANED_MARGIN_FILE = os.path.join(CLEANED_DATA_DIR, "cleaned_all_margin_data.csv")
MARGIN_MERGE_STATUS_FILE = os.path.join(CLEANED_DATA_DIR, "margin_merge_status.json")

for d in [DAILY_K_DIR, FUND_CACHE_DIR, RAW_MARGIN_DIR, CLEANED_DATA_DIR, REVENUE_DIR,
          FINANCIAL_DIR, TDCC_DIR, INDEX_DIR, FOREIGN_OWN_DIR]:
    os.makedirs(d, exist_ok=True)

TODAY = date.today()
REQUEST_PAUSE = 0.4


def get_previous_trading_day():
    d = TODAY - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _load_market_lookup():
    """Build a ticker -> market lookup from DuckDB financials metadata."""
    db_path = os.path.join(BASE_DIR, "stock.duckdb")
    if not os.path.exists(db_path):
        return {}
    try:
        import duckdb

        con = duckdb.connect(db_path, read_only=True)
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT
                    Ticker,
                    Market,
                    row_number() OVER (
                        PARTITION BY Ticker
                        ORDER BY Year DESC, Season DESC
                    ) AS rn
                FROM financials
                WHERE Market IN ('TWSE', 'OTC')
            )
            SELECT Ticker, Market
            FROM ranked
            WHERE rn = 1
            """
        ).fetchall()
        con.close()
        return {str(ticker): str(market) for ticker, market in rows if ticker and market}
    except Exception:
        return {}


def _fund_cache_dates_by_market():
    markets = {"TWSE": set(), "OTC": set()}
    for name in os.listdir(FUND_CACHE_DIR):
        if not name.endswith(".csv"):
            continue
        if name.startswith("fund_tpex_"):
            date8 = name[len("fund_tpex_"):-4]
            if len(date8) == 8 and date8.isdigit():
                markets["OTC"].add(f"{date8[:4]}-{date8[4:6]}-{date8[6:]}")
        elif name.startswith("fund_"):
            date8 = name[len("fund_"):-4]
            if len(date8) == 8 and date8.isdigit():
                markets["TWSE"].add(f"{date8[:4]}-{date8[4:6]}-{date8[6:]}")
    return markets


def _fill_missing_fund_values(df_idx, ticker, market_lookup, cache_dates_by_market):
    market = market_lookup.get(ticker)
    if market not in cache_dates_by_market:
        return 0

    covered_dates = cache_dates_by_market[market]
    if not covered_dates:
        return 0

    cols = ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]
    covered_mask = df_idx.index.isin(covered_dates)
    if not covered_mask.any():
        return 0

    filled = 0
    for col in cols:
        missing_mask = covered_mask & df_idx[col].isna()
        if missing_mask.any():
            filled += int(missing_mask.sum())
            df_idx.loc[missing_mask, col] = 0.0
    return filled


def build_session():
    # 優先使用 OS 憑證庫（Windows Certificate Store），避免 verify=False
    try:
        import truststore
        truststore.inject_into_ssl()
        _verify = True
    except Exception:
        import warnings
        warnings.warn(
            "truststore 載入失敗，SSL 驗證已停用（MITM 風險）。"
            "請執行 pip install truststore 修正。",
            stacklevel=2,
        )
        _verify = False

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
    s.verify = _verify
    return s


SESSION = build_session()


# ==============================================================================
# Step 1: 日K資料更新 (yfinance)
# ==============================================================================
def step1_update_daily_k():
    import yfinance as yf

    print("\n" + "=" * 60)
    print("Step 1/7: 更新日K資料 (yfinance)")
    print("=" * 60)

    tickers = sorted([
        os.path.splitext(f)[0]
        for f in os.listdir(DAILY_K_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    ])
    print(f"本地 CSV 共 {len(tickers)} 檔")

    updated = latest = failed = 0
    failed_list = []

    for ticker in tqdm(tickers, desc="日K更新"):
        path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
        try:
            df_old = pd.read_csv(path, dtype={"Date": str}, encoding="utf-8-sig")
        except UnicodeError:
            try:
                df_old = pd.read_csv(path, dtype={"Date": str}, encoding="cp950")
            except Exception:
                failed += 1
                failed_list.append(ticker)
                continue
        except Exception:
            failed += 1
            failed_list.append(ticker)
            continue

        original_columns = df_old.columns.tolist()

        # 找最後日期
        if "Date" not in df_old.columns or df_old.empty:
            failed += 1
            failed_list.append(ticker)
            continue
        dts = pd.to_datetime(df_old["Date"], errors="coerce").dropna()
        if dts.empty:
            failed += 1
            failed_list.append(ticker)
            continue
        last_dt = dts.max().date()

        if last_dt >= TODAY:
            latest += 1
            continue

        start_dt = last_dt + timedelta(days=1)
        end_dt = TODAY + timedelta(days=1)  # yfinance end is exclusive

        MAX_RETRY = 3
        last_err = None
        for attempt in range(MAX_RETRY):
            try:
                df_new = yf.download(
                    f"{ticker}.TW", start=start_dt.strftime("%Y-%m-%d"),
                    end=end_dt.strftime("%Y-%m-%d"), progress=False,
                )
                if df_new.empty:
                    time.sleep(REQUEST_PAUSE)
                    df_new = yf.download(
                        f"{ticker}.TWO", start=start_dt.strftime("%Y-%m-%d"),
                        end=end_dt.strftime("%Y-%m-%d"), progress=False,
                    )
                if df_new.empty:
                    latest += 1
                    last_err = None
                    break

                df_new = df_new.reset_index()
                if isinstance(df_new.columns, pd.MultiIndex):
                    df_new.columns = df_new.columns.get_level_values(0)

                if "Date" in df_new.columns:
                    df_new["Date"] = pd.to_datetime(df_new["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                elif "Datetime" in df_new.columns:
                    df_new = df_new.rename(columns={"Datetime": "Date"})
                    df_new["Date"] = pd.to_datetime(df_new["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                else:
                    latest += 1
                    last_err = None
                    break

                df_combined = pd.concat([df_old, df_new], ignore_index=True)
                df_combined["Date"] = pd.to_datetime(df_combined["Date"], errors="coerce")
                df_combined = df_combined.dropna(subset=["Date"])
                df_combined["Date"] = df_combined["Date"].dt.strftime("%Y-%m-%d")
                df_combined = df_combined.drop_duplicates(subset=["Date"], keep="last")
                df_combined = df_combined.sort_values("Date")

                all_cols = df_combined.columns.tolist()
                new_cols = [c for c in all_cols if c not in original_columns]
                final_cols = [c for c in (original_columns + new_cols) if c in df_combined.columns]
                df_combined = df_combined[final_cols]

                tmp_path = path + ".tmp"
                df_combined.to_csv(tmp_path, index=False, encoding="utf-8-sig")
                os.replace(tmp_path, path)
                updated += 1
                last_err = None
                time.sleep(REQUEST_PAUSE)
                break

            except Exception as e:
                last_err = str(e)
                if attempt < MAX_RETRY - 1:
                    time.sleep(2 ** attempt)  # 1s, 2s backoff

        if last_err is not None:
            failed += 1
            failed_list.append(f"{ticker}({last_err[:30]})")

    print(f"  成功更新: {updated}  已最新: {latest}  失敗: {failed}")
    if failed_list:
        print(f"  失敗清單(前20): {', '.join(failed_list[:20])}")


# ==============================================================================
# Step 2: 法人買賣超 (TWSE + TPEx)
# ==============================================================================
def _find_missing_dates(directory, prefix, suffix, end_date, lookback_days=60,
                        exclude_prefix=None):
    """掃描目錄，找出近 lookback_days 天內缺少檔案的週間日（缺口偵測）"""
    start = end_date - timedelta(days=lookback_days)
    existing = set()
    for f in os.listdir(directory):
        if exclude_prefix and f.startswith(exclude_prefix):
            continue
        if f.startswith(prefix) and f.endswith(suffix):
            try:
                d_str = f[len(prefix):-len(suffix)]
                existing.add(datetime.strptime(d_str, "%Y%m%d").date())
            except Exception:
                pass
    missing = []
    d = start
    while d <= end_date:
        if d.weekday() < 5 and d not in existing:
            missing.append(d)
        d += timedelta(days=1)
    return missing


def _detect_fund_last_date():
    """從法人快取資料夾偵測最後日期"""
    files = sorted([
        f for f in os.listdir(FUND_CACHE_DIR)
        if f.startswith("fund_") and f.endswith(".csv") and not f.startswith("fund_tpex")
    ])
    if not files:
        return date(2020, 1, 1)
    last = files[-1]  # e.g. fund_20251015.csv
    try:
        d = last.replace("fund_", "").replace(".csv", "")
        return datetime.strptime(d, "%Y%m%d").date()
    except Exception:
        return date(2020, 1, 1)


def _fetch_twse_fund(date_str_8):
    """下載 TWSE 法人買賣超"""
    cache_path = os.path.join(FUND_CACHE_DIR, f"fund_{date_str_8}.csv")
    if os.path.exists(cache_path):
        return "exists"
    url = f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str_8}&selectType=ALL"
    try:
        r = SESSION.get(url, timeout=20)
        raw = r.text
        lines = [line for line in raw.split("\n") if "證券代號" in line or "證券名稱" in line]
        if not lines:
            return "no_data"
        header_line = lines[0]
        content = []
        header_found = False
        for line in raw.split("\n"):
            if header_found:
                content.append(line)
            elif line.strip() == header_line.strip():
                header_found = True
                content.append(line)
        raw2 = "\n".join(content)
        df = pd.read_csv(StringIO(raw2))
        df.columns = [c.replace("\ufeff", "").strip() for c in df.columns]
        if "證券代號" not in df.columns:
            return "no_data"
        df = df[df["證券代號"].astype(str).str.isnumeric()]
        df = df.rename(columns={
            "證券代號": "Ticker",
            "外陸資買賣超股數(不含外資自營商)": "Foreign_BuySell",
            "投信買賣超股數": "Trust_BuySell",
            "自營商買賣超股數": "Dealer_BuySell",
        })
        df = df[["Ticker", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]]
        df["Date"] = f"{date_str_8[:4]}-{date_str_8[4:6]}-{date_str_8[6:]}"
        for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        return "ok"
    except Exception:
        return "error"


def _fetch_tpex_fund(date_str):
    """下載 TPEx 法人買賣超"""
    date8 = date_str.replace("-", "")
    cache_path = os.path.join(FUND_CACHE_DIR, f"fund_tpex_{date8}.csv")
    if os.path.exists(cache_path):
        return "exists"
    try:
        roc_year = int(date_str[:4]) - 1911
        tpex_date_str = f"{roc_year}/{date_str[5:7]}/{date_str[8:]}"
    except ValueError:
        return "error"

    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
    params = {"l": "zh-tw", "d": tpex_date_str, "_": str(int(time.time() * 1000))}
    try:
        r = SESSION.get(url, params=params, timeout=20)
        data = r.json()
        df = None
        if isinstance(data, dict) and data.get("aaData"):
            tmp = pd.DataFrame(data["aaData"])
            df = tmp[[0, 4, 7, 8]].copy()
            df.columns = ["Ticker", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]
        elif isinstance(data, dict) and data.get("tables") and data["tables"][0].get("data"):
            tmp = pd.DataFrame(data["tables"][0]["data"])
            sel = tmp[[0, 4, 7, 10, 13]].copy()
            sel.columns = ["Ticker", "Foreign_BuySell", "Trust_BuySell", "Dealer_Prop", "Dealer_Hedge"]
            for c in ["Foreign_BuySell", "Trust_BuySell", "Dealer_Prop", "Dealer_Hedge"]:
                sel[c] = pd.to_numeric(sel[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
            sel["Dealer_BuySell"] = sel["Dealer_Prop"] + sel["Dealer_Hedge"]
            df = sel[["Ticker", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]]
        else:
            return "no_data"

        df["Date"] = date_str
        for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        return "ok"
    except Exception:
        return "error"


def step2_update_fund_data():
    print("\n" + "=" * 60)
    print("Step 2/7: 更新法人買賣超 (TWSE + TPEx)")
    print("=" * 60)

    end = get_previous_trading_day()

    # 18:00 後法人資料通常已公布，嘗試抓今天的
    now = datetime.now()
    if now.hour >= 18 and TODAY.weekday() < 5 and TODAY > end:
        end = TODAY
        print(f"  (18:00後，嘗試包含今日 {TODAY} 法人資料)")

    # 缺口偵測：掃描近 60 天，找遺漏的交易日
    missing = _find_missing_dates(FUND_CACHE_DIR, "fund_", ".csv", end,
                                  lookback_days=60, exclude_prefix="fund_tpex")
    if not missing:
        print("  法人資料已是最新（含缺口檢查）")
        return

    last_date = _detect_fund_last_date()
    gap_count = sum(1 for d in missing if d <= last_date)
    if gap_count > 0:
        print(f"  偵測到 {gap_count} 天缺口 + {len(missing) - gap_count} 天新資料")
    print(f"  下載範圍: {missing[0]} ~ {missing[-1]} ({len(missing)} 天)")

    ok = skip = 0
    for d in tqdm(missing, desc="法人(TWSE+TPEx)"):
        d_str = d.strftime("%Y-%m-%d")
        d8 = d.strftime("%Y%m%d")
        r1 = _fetch_twse_fund(d8)
        time.sleep(0.3)
        r2 = _fetch_tpex_fund(d_str)
        time.sleep(0.3)
        # 近 3 天：僅在 error（網路/例外）時重試；no_data 表示假日或真無資料，不重試
        if d >= TODAY - timedelta(days=3):
            for attempt in range(3):
                needs_retry = (r1 == "error") or (r2 == "error")
                if not needs_retry:
                    break
                time.sleep(2 ** (attempt + 1))
                if r1 == "error":
                    r1 = _fetch_twse_fund(d8)
                if r2 == "error":
                    r2 = _fetch_tpex_fund(d_str)
        if "ok" in (r1, r2):
            ok += 1
        else:
            skip += 1

    print(f"  新增: {ok} 天, 略過/無資料: {skip} 天")


# ==============================================================================
# Step 3: 融資融券 (TWSE + TPEx)
# ==============================================================================
def _detect_margin_last_date():
    files = sorted([
        f for f in os.listdir(RAW_MARGIN_DIR)
        if f.startswith("raw_margin_twse_") and f.endswith(".csv")
    ])
    if not files:
        return date(2020, 1, 1)
    last = files[-1]
    try:
        d = last.replace("raw_margin_twse_", "").replace(".csv", "")
        return datetime.strptime(d, "%Y%m%d").date()
    except Exception:
        return date(2020, 1, 1)


def _download_twse_margin(d, save_path):
    if os.path.exists(save_path):
        return "exists"
    date_str = d.strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=csv&date={date_str}&selectType=ALL"
    try:
        r = SESSION.get(url, timeout=20)
        txt = r.text.strip()
        if r.status_code == 200 and len(txt) > 500:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(txt)
            return "ok"
        return "no_data"
    except Exception:
        return "error"


def _download_tpex_margin(d, save_path):
    if os.path.exists(save_path):
        return "exists"
    roc_year = d.year - 1911
    tpex_date_str = f"{roc_year}/{d.strftime('%m/%d')}"
    url = "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php"
    params = {"l": "zh-tw", "d": tpex_date_str, "_": str(int(time.time() * 1000))}
    try:
        r = SESSION.get(url, params=params, timeout=20)
        txt = r.text.strip()
        if txt and len(txt) > 100:
            with open(save_path, "w", encoding="utf-8-sig") as f:
                f.write(txt)
            return "ok"
        return "no_data"
    except Exception:
        return "error"


def step3_update_margin_data():
    print("\n" + "=" * 60)
    print("Step 3/7: 更新融資融券原始資料")
    print("=" * 60)

    end = get_previous_trading_day()

    # 18:00 後融資券資料通常已公布，嘗試抓今天的
    now = datetime.now()
    if now.hour >= 18 and TODAY.weekday() < 5 and TODAY > end:
        end = TODAY
        print(f"  (18:00後，嘗試包含今日 {TODAY} 融資券資料)")

    # 缺口偵測：掃描近 60 天，找遺漏的交易日
    missing = _find_missing_dates(RAW_MARGIN_DIR, "raw_margin_twse_", ".csv", end,
                                  lookback_days=60)
    if not missing:
        print("  融資券原始檔已是最新（含缺口檢查），檢查總表是否同步...")
        last_date = _detect_margin_last_date()
        _clean_and_merge_margin(last_date, end)
        return

    last_date = _detect_margin_last_date()
    gap_count = sum(1 for d in missing if d <= last_date)
    if gap_count > 0:
        print(f"  偵測到 {gap_count} 天缺口 + {len(missing) - gap_count} 天新資料")
    print(f"  下載範圍: {missing[0]} ~ {missing[-1]} ({len(missing)} 天)")

    ok = 0
    for d in tqdm(missing, desc="融資券下載"):
        ymd = d.strftime("%Y%m%d")
        twse_path = os.path.join(RAW_MARGIN_DIR, f"raw_margin_twse_{ymd}.csv")
        tpex_path = os.path.join(RAW_MARGIN_DIR, f"raw_margin_tpex_{ymd}.json")
        r1 = _download_twse_margin(d, twse_path)
        r2 = _download_tpex_margin(d, tpex_path)
        # 近 3 天：僅在 error（網路/例外）時重試；no_data 表示假日或真無資料，不重試
        if d >= TODAY - timedelta(days=3):
            for attempt in range(3):
                needs_retry = (r1 == "error") or (r2 == "error")
                if not needs_retry:
                    break
                time.sleep(2 ** (attempt + 1))
                if r1 == "error":
                    r1 = _download_twse_margin(d, twse_path)
                if r2 == "error":
                    r2 = _download_tpex_margin(d, tpex_path)
        if "ok" in (r1, r2):
            ok += 1
        time.sleep(0.35)

    print(f"  新增: {ok} 天")

    # 清理 + 合併到總表（從最早缺口開始）
    _clean_and_merge_margin(missing[0], end)


TARGET_COLUMNS = ["Date", "Ticker", "Margin_Balance", "Short_Balance"]


def _clean_twse_margin(file_path, date_str):
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 200:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        start_line = None
        with open(file_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if "代號" in line and "名稱" in line:
                    start_line = i
                    break
        if start_line is None:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        df = pd.read_csv(file_path, encoding="utf-8", skiprows=start_line)
        df.columns = df.columns.str.strip()
        required = ["代號", "今日餘額", "今日餘額.1"]
        if not all(c in df.columns for c in required):
            return pd.DataFrame(columns=TARGET_COLUMNS)
        df["代號"] = df["代號"].astype(str).str.replace('=', '').str.replace('"', '').str.strip()
        df = df[df["代號"].str.match(r"^\d{4,6}$", na=False)]
        df = df[required].copy()
        df.columns = ["Ticker", "Margin_Balance", "Short_Balance"]
        df["Date"] = date_str
        for col in ["Margin_Balance", "Short_Balance"]:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce").fillna(0).astype(int)
        return df[TARGET_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=TARGET_COLUMNS)


def _clean_tpex_margin(file_path, date_str):
    try:
        if not os.path.exists(file_path):
            return pd.DataFrame(columns=TARGET_COLUMNS)
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        if not content.strip():
            return pd.DataFrame(columns=TARGET_COLUMNS)
        data = json.loads(content)
        if "tables" in data and data.get("tables") and data["tables"][0].get("data"):
            df_raw = pd.DataFrame(data["tables"][0]["data"])
            df_sel = df_raw.iloc[:, [0, 6, 14]]
        elif "aaData" in data and data.get("aaData"):
            df_raw = pd.DataFrame(data["aaData"])
            df_sel = df_raw.iloc[:, [0, 6, 12]]
        else:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        df_sel.columns = ["Ticker", "Margin_Balance", "Short_Balance"]
        df_sel["Date"] = date_str
        for col in ["Margin_Balance", "Short_Balance"]:
            df_sel[col] = pd.to_numeric(df_sel[col].astype(str).str.replace(",", ""), errors="coerce").fillna(0).astype(int)
        return df_sel[TARGET_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=TARGET_COLUMNS)


def _clean_and_merge_margin(start, end):
    """清理新的融資券原始資料並合併到總表"""
    print("  清理並合併融資券資料...")

    df_old = None
    old_dates = set()
    if os.path.exists(CLEANED_MARGIN_FILE):
        try:
            df_old = pd.read_csv(CLEANED_MARGIN_FILE, usecols=TARGET_COLUMNS)
            if "Date" in df_old.columns and not df_old.empty:
                old_dates = set(df_old["Date"].astype(str).unique())
        except Exception:
            df_old = None

    dates = pd.date_range(start=start, end=end)
    dates_to_clean = [d.strftime("%Y-%m-%d") for d in dates if d.strftime("%Y-%m-%d") not in old_dates]

    if not dates_to_clean:
        print("  融資券總表已是最新")
        return

    all_new = []
    for iso in dates_to_clean:
        ymd = iso.replace("-", "")
        df_twse = _clean_twse_margin(os.path.join(RAW_MARGIN_DIR, f"raw_margin_twse_{ymd}.csv"), iso)
        df_tpex = _clean_tpex_margin(os.path.join(RAW_MARGIN_DIR, f"raw_margin_tpex_{ymd}.json"), iso)
        all_new.append(df_twse)
        all_new.append(df_tpex)

    base = df_old if df_old is not None else pd.DataFrame(columns=TARGET_COLUMNS)
    df_new = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame(columns=TARGET_COLUMNS)
    out = pd.concat([base, df_new], ignore_index=True)
    if not out.empty:
        out.drop_duplicates(subset=["Date", "Ticker"], keep="last", inplace=True)
        out.sort_values(by=["Ticker", "Date"], inplace=True)
    out.to_csv(CLEANED_MARGIN_FILE, index=False, encoding="utf-8-sig")
    print(f"  融資券總表更新完成，共 {len(out):,} 筆")


# ==============================================================================
# Step 4: 合併法人資料 → 日K
# ==============================================================================
def step4_merge_fund_into_daily_k():
    print("\n" + "=" * 60)
    print("Step 4/7: 合併法人資料到日K")
    print("=" * 60)

    # 讀取所有法人快取
    fund_files = sorted([
        os.path.join(FUND_CACHE_DIR, f)
        for f in os.listdir(FUND_CACHE_DIR)
        if f.endswith(".csv")
    ])
    if not fund_files:
        print("  無法人資料可合併")
        return

    all_fund = []
    for p in tqdm(fund_files, desc="讀取法人快取"):
        try:
            df = pd.read_csv(p, dtype={"Ticker": str, "Date": str}, encoding="utf-8-sig")
            needed = ["Ticker", "Date", "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]
            if all(c in df.columns for c in needed):
                all_fund.append(df[needed])
        except Exception:
            pass

    if not all_fund:
        print("  無有效法人資料")
        return

    fund_data = pd.concat(all_fund, ignore_index=True)
    fund_data = fund_data.sort_values(["Ticker", "Date"]).drop_duplicates(subset=["Ticker", "Date"], keep="last")
    for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
        fund_data[col] = pd.to_numeric(fund_data[col], errors="coerce").fillna(0)
    grouped = fund_data.groupby("Ticker", sort=False)
    market_lookup = _load_market_lookup()
    cache_dates_by_market = _fund_cache_dates_by_market()
    print(f"  法人資料: {len(fund_data):,} 筆, {len(grouped):,} 支股票")

    stock_files = [f for f in os.listdir(DAILY_K_DIR) if f.endswith(".csv")]
    updated = skipped = 0
    zero_filled_cells = 0
    for fn in tqdm(stock_files, desc="合併法人→日K"):
        ticker = os.path.splitext(fn)[0]
        has_fund_rows = ticker in grouped.groups
        if (not has_fund_rows) and (ticker not in market_lookup):
            skipped += 1
            continue
        path = os.path.join(DAILY_K_DIR, fn)
        try:
            df_daily = pd.read_csv(path, dtype={"Date": str}, encoding="utf-8-sig")
            for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
                if col not in df_daily.columns:
                    df_daily[col] = 0.0
            df_idx = df_daily.set_index("Date")
            if has_fund_rows:
                sfd = grouped.get_group(ticker).copy()
                sfd = sfd.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").set_index("Date")
                sfd = sfd[["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]]
                df_idx.update(sfd)
            zero_filled_cells += _fill_missing_fund_values(df_idx, ticker, market_lookup, cache_dates_by_market)
            df_idx.reset_index().to_csv(path, index=False, encoding="utf-8-sig")
            updated += 1
        except Exception:
            skipped += 1

    print(f"  更新: {updated}, 略過: {skipped}, 補零儲存格: {zero_filled_cells}")


# ==============================================================================
# Step 5: 合併融資券 → 日K
# ==============================================================================
def step5_merge_margin_into_daily_k():
    print("\n" + "=" * 60)
    print("Step 5/7: 合併融資券資料到日K")
    print("=" * 60)

    if not os.path.exists(CLEANED_MARGIN_FILE):
        print("  無融資券總表可合併")
        return

    all_margin = pd.read_csv(CLEANED_MARGIN_FILE, dtype={"Ticker": str, "Date": str}, encoding="utf-8-sig")

    # 增量: 只合併上次之後的
    start_date_for_merge = None
    try:
        with open(MARGIN_MERGE_STATUS_FILE, "r", encoding="utf-8") as f:
            status = json.load(f)
            last = status.get("last_merged_date")
            if last:
                last_dt = datetime.strptime(last, "%Y-%m-%d").date()
                start_date_for_merge = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                print(f"  上次合併至: {last}, 本次從 {start_date_for_merge} 起")
    except Exception:
        print("  首次合併")

    if start_date_for_merge:
        data_to_merge = all_margin[all_margin["Date"] >= start_date_for_merge].copy()
    else:
        data_to_merge = all_margin.copy()

    if data_to_merge.empty:
        print("  已是最新，無需合併")
        return

    grp = data_to_merge.groupby("Ticker")
    tickers_to_merge = set(grp.groups.keys())

    stock_files = [f for f in os.listdir(DAILY_K_DIR) if f.endswith(".csv")]
    updated = skipped = 0

    for fn in tqdm(stock_files, desc="合併融資券→日K"):
        ticker = os.path.splitext(fn)[0]
        if ticker not in tickers_to_merge:
            skipped += 1
            continue
        path = os.path.join(DAILY_K_DIR, fn)
        try:
            df_daily = pd.read_csv(path, dtype={"Date": str}, encoding="utf-8-sig")
            for col in ["Margin_Balance", "Short_Balance"]:
                if col not in df_daily.columns:
                    df_daily[col] = 0
            stock_margin = grp.get_group(ticker).copy()
            stock_idx = stock_margin.set_index("Date")[["Margin_Balance", "Short_Balance"]]
            df_idx = df_daily.set_index("Date")
            df_idx.update(stock_idx)
            df_idx.reset_index().to_csv(path, index=False, encoding="utf-8-sig")
            updated += 1
        except Exception:
            skipped += 1

    latest_date = data_to_merge["Date"].max()
    with open(MARGIN_MERGE_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_merged_date": latest_date}, f, ensure_ascii=False)

    print(f"  更新: {updated}, 略過: {skipped}, 合併至: {latest_date}")


# ==============================================================================
# Step 6: 月營收
# ==============================================================================
def _normalize_date_col(df):
    if "Date" not in df.columns:
        return df
    def _norm(x):
        if pd.isna(x):
            return x
        s = str(x).strip()
        m = re.match(r"^\s*(\d{4})[-/](\d{1,2})\s*$", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        return s
    df["Date"] = df["Date"].apply(_norm)
    return df


def _extract_inner_html(full_html):
    soup = BeautifulSoup(full_html, "lxml")
    candidates = soup.select("form[name^=autoRunScript] input[name^=run]")
    for inp in candidates:
        val = (inp.get("value") or "").strip()
        if not val:
            continue
        raw = html.unescape(val)
        m_match = re.search(r"innerHTML\s*=\s*['\"](.+?)['\"];", raw, re.DOTALL)
        if not m_match:
            continue
        inner = m_match.group(1).replace(r"\/", "/").replace(r"\n", "\n").replace(r"\t", "\t")
        inner = inner.replace(r"\'", "'").replace(r'\"', '"')
        inner = html.unescape(inner)
        if "<table" in inner:
            return inner
    return None


def _parse_revenue(html_text, source_ym):
    soup = BeautifulSoup(html_text, "lxml")
    main_tbl = soup.find("table", class_="hasBorder")
    if not main_tbl or "查無資料" in main_tbl.get_text():
        return None
    try:
        company_name = None
        tag = soup.find("td", class_="compName")
        if tag and tag.b and ")" in tag.b.get_text():
            company_name = tag.b.text.split(")")[1].split("公司提供")[0].strip()
        items = {}
        for tr in main_tbl.select("tr"):
            th = tr.find("th")
            tds = tr.find_all("td")
            if th and tds:
                key = th.get_text(strip=True)
                val_str = tds[0].get_text(strip=True).replace(",", "").replace("\xa0", "")
            elif len(tds) >= 2 and not th:
                # KY 股格式：全部用 <td>，第一個是標籤，第二個是數值
                key = tds[0].get_text(strip=True)
                val_str = tds[1].get_text(strip=True).replace(",", "").replace("\xa0", "")
            else:
                continue
            try:
                val = float(val_str) if val_str else None
            except ValueError:
                val = None
            if key in items:
                items[f"累計_{key}"] = val
            else:
                items[key] = val
        if items.get("本月") is not None:
            return {
                "Date": f"{source_ym[0]}-{source_ym[1]:02d}",
                "Name": company_name,
                "Monthly_Revenue": items.get("本月"),
                "Cumulative_Revenue": items.get("本年累計"),
                "YoY_pct_change": items.get("增減百分比"),
                "Cumulative_YoY_pct_change": items.get("累計_增減百分比"),
            }
        return None
    except Exception:
        return None


def _fetch_revenue_month(session, ticker, year, month):
    api_url = "https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t05st10_ifrs",
    }
    roc_year = year - 1911
    for attempt in range(3):
        try:
            time.sleep(random.uniform(3.0, 6.0))
            payload = {
                "encodeURIComponent": "1", "step": "1", "firstin": "1",
                "off": "1", "queryName": "co_id", "inpuType": "co_id",
                "TYPEK": "all", "isnew": "false", "co_id": ticker,
                "year": str(roc_year), "month": f"{month:02d}",
            }
            r1 = session.post(api_url, data=payload, headers=headers, timeout=30)
            r1.raise_for_status()

            soup1 = BeautifulSoup(r1.text, "lxml")
            detail_payload = payload.copy()
            detail_payload.update({"step": "2", "yearmonth": f"{roc_year}{month:02d}"})
            form = soup1.select_one("form#t05st10_ifrs_form")
            if form:
                for inp in form.select("input[name]"):
                    name = inp.get("name")
                    value = (inp.get("value") or "").strip()
                    if name and name not in detail_payload:
                        detail_payload[name] = value

            r2 = session.post(api_url, data=detail_payload, headers=headers, timeout=30)
            r2.raise_for_status()

            parsed = _parse_revenue(r2.text, (year, month))
            if not parsed:
                inner = _extract_inner_html(r2.text)
                if inner:
                    parsed = _parse_revenue(inner, (year, month))
            return parsed

        except requests.exceptions.RequestException:
            if attempt >= 2:
                return None
            time.sleep(5)
    return None


def _process_ticker_revenue(ticker, all_months):
    """處理單支股票的月營收更新 (供並行使用)"""
    final_path = os.path.join(REVENUE_DIR, f"revenue_{ticker}.csv")

    existing = set()
    if os.path.exists(final_path):
        try:
            df_e = pd.read_csv(final_path, dtype={"Date": str}, usecols=["Date"])
            df_e = _normalize_date_col(df_e)
            existing = set(df_e["Date"].dropna().tolist())
        except Exception:
            pass

    missing = [(y, m) for y, m in all_months if f"{y}-{m:02d}" not in existing]
    if not missing:
        return 0, []

    new_rows = []
    failed = []
    session = build_session()
    try:
        for year, month in missing:
            res = _fetch_revenue_month(session, ticker, year, month)
            if res:
                new_rows.append(res)
            else:
                failed.append((ticker, year, month))
    finally:
        session.close()

    if not new_rows:
        return 0, failed

    df_new = pd.DataFrame(new_rows)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        try:
            df_old = pd.read_csv(final_path, dtype={"Date": str})
            df_out = pd.concat([df_old, df_new], ignore_index=True)
        except Exception:
            df_out = df_new
    else:
        df_out = df_new

    df_out = _normalize_date_col(df_out)
    df_out = df_out.drop_duplicates(subset=["Date"], keep="last").sort_values("Date")
    tmp = f"{final_path}.tmp"
    df_out.to_csv(tmp, index=False, encoding="utf-8-sig")
    shutil.move(tmp, final_path)
    return len(new_rows), failed


def step6_update_revenue(retry_only=False):
    print("\n" + "=" * 60)
    print("Step 6/7: 更新月營收" + (" (重試模式)" if retry_only else ""))
    print("=" * 60)

    today_day = datetime.today().day
    end_date = datetime.today().replace(day=1) - relativedelta(days=1)

    # 每月 11~15 號才做完整掃描（營收公布截止日後）
    # 其他日子只檢查最近一個月，大幅減少無意義請求
    if not retry_only and today_day > 15:
        print("  今日非營收公布期 (每月1~15日)，跳過月營收更新")
        return

    if not retry_only and 1 <= today_day <= 15:
        # 只掃最近 2 個月
        start_date = end_date.replace(day=1) - relativedelta(months=1)
        print(f"  營收公布期，只檢查近 2 個月: {start_date.strftime('%Y-%m')} ~ {end_date.strftime('%Y-%m')}")
    else:
        start_date = datetime(2020, 1, 1) if not retry_only else datetime(2025, 9, 1)

    def month_range(s, e):
        cur = s.replace(day=1)
        last = e.replace(day=1)
        while cur <= last:
            yield (cur.year, cur.month)
            cur += relativedelta(months=1)

    all_months = list(month_range(start_date, end_date))

    # 找出需要更新的股票
    all_tickers = sorted([
        os.path.splitext(f)[0]
        for f in os.listdir(DAILY_K_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    ])

    if retry_only:
        # 只選最新月份不到 end_date 的股票
        target_ym = f"{end_date.year}-{end_date.month:02d}"
        tickers = []
        for t in all_tickers:
            fp = os.path.join(REVENUE_DIR, f"revenue_{t}.csv")
            if os.path.exists(fp):
                try:
                    last = pd.read_csv(fp, dtype={"Date": str}, usecols=["Date"])
                    last = _normalize_date_col(last)
                    latest = last["Date"].dropna().max()
                    if latest < target_ym:
                        tickers.append(t)
                except Exception:
                    tickers.append(t)
            else:
                tickers.append(t)
    else:
        tickers = all_tickers

    print(f"  目標股票: {len(tickers)} 支, 月份範圍: {start_date.strftime('%Y-%m')} ~ {end_date.strftime('%Y-%m')}")

    total_new = 0
    failed_tasks = []

    with requests.Session() as session:
        for ticker in tqdm(tickers, desc="月營收更新"):
            final_path = os.path.join(REVENUE_DIR, f"revenue_{ticker}.csv")

            existing = set()
            if os.path.exists(final_path):
                try:
                    df_e = pd.read_csv(final_path, dtype={"Date": str}, usecols=["Date"])
                    df_e = _normalize_date_col(df_e)
                    existing = set(df_e["Date"].dropna().tolist())
                except Exception:
                    pass

            missing = [(y, m) for y, m in all_months if f"{y}-{m:02d}" not in existing]
            if not missing:
                continue

            new_rows = []
            for year, month in missing:
                res = _fetch_revenue_month(session, ticker, year, month)
                if res:
                    new_rows.append(res)
                else:
                    failed_tasks.append((ticker, year, month))

            if not new_rows:
                continue

            df_new = pd.DataFrame(new_rows)
            if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                try:
                    df_old = pd.read_csv(final_path, dtype={"Date": str})
                    df_out = pd.concat([df_old, df_new], ignore_index=True)
                except Exception:
                    df_out = df_new
            else:
                df_out = df_new

            df_out = _normalize_date_col(df_out)
            df_out = df_out.drop_duplicates(subset=["Date"], keep="last").sort_values("Date")
            tmp = f"{final_path}.tmp"
            df_out.to_csv(tmp, index=False, encoding="utf-8-sig")
            shutil.move(tmp, final_path)
            total_new += len(new_rows)

    print(f"  新增: {total_new} 筆, 失敗: {len(failed_tasks)} 筆")


# ==============================================================================
# Step 7: 重算技術指標
# ==============================================================================
def _rma(series, period):
    s = series.copy()
    sma = s.rolling(period, min_periods=period).mean()
    r = pd.Series(index=s.index, dtype="float64")
    first = sma.first_valid_index()
    if first is None:
        return r
    r.loc[first] = sma.loc[first]
    alpha = 1.0 / period
    for i in range(s.index.get_loc(first) + 1, len(s)):
        prev = r.iloc[i - 1]
        val = s.iloc[i]
        r.iloc[i] = prev + alpha * (val - prev)
    return r


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return _rma(tr, period)


def _stoch_kd(high, low, close, k_period=9, k_smooth=3, d_period=3):
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    rsv = (close - lowest) / (highest - lowest)
    rsv = (rsv.replace([np.inf, -np.inf], np.nan) * 100).clip(0, 100)
    k = rsv.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def step7_calc_indicators():
    print("\n" + "=" * 60)
    print("Step 7/7: 重算技術指標")
    print("=" * 60)

    csv_files = [f for f in os.listdir(DAILY_K_DIR) if f.endswith(".csv")]
    processed = skipped = 0

    for fn in tqdm(csv_files, desc="技術指標"):
        path = os.path.join(DAILY_K_DIR, fn)
        try:
            df = pd.read_csv(path)
            needed = ["Date", "Open", "High", "Low", "Close", "Volume"]
            if not all(c in df.columns for c in needed):
                skipped += 1
                continue
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
            df[["Open", "High", "Low", "Close", "Volume"]] = (
                df[["Open", "High", "Low", "Close", "Volume"]]
                .apply(pd.to_numeric, errors="coerce").ffill().bfill()
            )
            if len(df) < 60:
                skipped += 1
                continue

            c = df["Close"]
            h, l, v = df["High"], df["Low"], df["Volume"]

            # 刪除舊指標欄位再重算
            indicator_cols = [
                "MA_5", "MA_20", "MA_60", "RSI_6", "RSI_14",
                "BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0",
                "MACD_12_26_9", "MACDh_12_26_9", "MACDs_12_26_9",
                "K", "D", "ATR_14", "VOL_MA_5", "VOL_MA_20",
            ]
            df.drop(columns=[x for x in indicator_cols if x in df.columns], errors="ignore", inplace=True)

            df["MA_5"] = c.rolling(5).mean()
            df["MA_20"] = c.rolling(20).mean()
            df["MA_60"] = c.rolling(60).mean()
            df["RSI_6"] = _rsi(c, 6)
            df["RSI_14"] = _rsi(c, 14)
            ma20 = c.rolling(20).mean()
            std20 = c.rolling(20).std()
            df["BBM_20_2.0"] = ma20
            df["BBU_20_2.0"] = ma20 + 2 * std20
            df["BBL_20_2.0"] = ma20 - 2 * std20
            ema12 = c.ewm(span=12, adjust=False).mean()
            ema26 = c.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            df["MACD_12_26_9"] = macd
            df["MACDs_12_26_9"] = signal
            df["MACDh_12_26_9"] = macd - signal
            k, d = _stoch_kd(h, l, c)
            df["K"] = k
            df["D"] = d
            df["ATR_14"] = _atr(h, l, c)
            df["VOL_MA_5"] = v.rolling(5).mean()
            df["VOL_MA_20"] = v.rolling(20).mean()

            df.replace([np.inf, -np.inf], np.nan, inplace=True)
            df.to_csv(path, index=False)
            processed += 1

        except Exception:
            skipped += 1

    print(f"  完成: {processed}, 略過: {skipped}")


# ==============================================================================
# Step 8: 季報財務數據 (MOPS 財務比率分析)
# ==============================================================================
def _fetch_mops_batch(year_roc, season, typek):
    """從 MOPS 批次抓取財務比率 (全部公司)"""
    url = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb06"
    payload = {
        "encodeURIComponent": "1", "step": "2", "firstin": "1",
        "off": "1", "TYPEK": typek,
        "year": str(year_roc), "season": str(season),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t163sb06",
    }
    r = SESSION.post(url, data=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return r.text


def _parse_mops_ratio_tables(html_text):
    """解析 MOPS 營益分析 HTML，用 BeautifulSoup 正確提取表頭"""
    soup = BeautifulSoup(html_text, "lxml")
    tables = soup.find_all("table", class_="hasBorder")
    if not tables:
        return pd.DataFrame()

    # 標準欄位名 (MOPS t163sb06 營益分析固定7欄)
    STANDARD_COLS = [
        "Ticker", "Name", "Revenue_M",
        "Gross_Margin_Pct", "Operating_Margin_Pct",
        "Pretax_Margin_Pct", "Net_Margin_Pct",
    ]

    all_rows = []
    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            vals = [td.get_text(strip=True) for td in tds[:7]]
            # 第一欄必須是 4-6 位數字的股票代號
            ticker = vals[0].replace(",", "").strip()
            if re.match(r"^\d{4,6}$", ticker):
                all_rows.append(vals)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=STANDARD_COLS)
    # 數值轉換
    for col in STANDARD_COLS[2:]:
        df[col] = pd.to_numeric(df[col].str.replace(",", ""), errors="coerce")
    return df


def step8_update_financial():
    print("\n" + "=" * 60)
    print("Step 8/10: 更新季報財務數據 (MOPS)")
    print("=" * 60)

    # 決定季度範圍: 2020Q1 ~ 最近可用季度
    # 季報公布時程: Q1→5月, Q2→8月, Q3→11月, Q4→隔年3月底
    latest_year = TODAY.year
    latest_month = TODAY.month
    if latest_month >= 11:
        end_year, end_q = latest_year, 3
    elif latest_month >= 8:
        end_year, end_q = latest_year, 2
    elif latest_month >= 5:
        end_year, end_q = latest_year, 1
    elif latest_month >= 3:
        end_year, end_q = latest_year - 1, 4  # 3月起嘗試抓 Q4
    else:
        end_year, end_q = latest_year - 1, 3

    quarters = []
    for y in range(2020, end_year + 1):
        for q in range(1, 5):
            if y == end_year and q > end_q:
                break
            quarters.append((y, q))

    total_new = 0
    for year, season in tqdm(quarters, desc="季報財務"):
        filepath = os.path.join(FINANCIAL_DIR, f"financial_{year}Q{season}.csv")
        if os.path.exists(filepath):
            continue

        roc_year = year - 1911
        all_data = []
        for typek in ["sii", "otc"]:
            try:
                html_text = _fetch_mops_batch(roc_year, season, typek)
                df = _parse_mops_ratio_tables(html_text)
                if not df.empty:
                    df["Market"] = "TWSE" if typek == "sii" else "OTC"
                    all_data.append(df)
                time.sleep(random.uniform(3, 6))
            except Exception as e:
                tqdm.write(f"  {year}Q{season} ({typek}) 失敗: {e}")

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            result["Year"] = year
            result["Season"] = season
            result.to_csv(filepath, index=False, encoding="utf-8-sig")
            total_new += 1
            tqdm.write(f"  {year}Q{season}: {len(result)} 筆")

    print(f"  新增: {total_new} 季")


# ==============================================================================
# Step 9: 集保股權分散表 (TDCC)
# ==============================================================================
def step9_update_tdcc():
    print("\n" + "=" * 60)
    print("Step 9/10: 更新集保股權分散表 (TDCC)")
    print("=" * 60)

    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    try:
        r = SESSION.get(url, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))

        if df.empty:
            print("  無資料")
            return

        # 第一欄是日期
        date_col = df.columns[0]
        latest_date = str(df[date_col].iloc[0]).replace("/", "").replace("-", "")

        filepath = os.path.join(TDCC_DIR, f"tdcc_{latest_date}.csv")
        if os.path.exists(filepath):
            print(f"  已有最新集保資料 ({latest_date})")
        else:
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
            print(f"  已下載: tdcc_{latest_date}.csv ({len(df):,} 筆)")

    except Exception as e:
        print(f"  下載失敗: {e}")

    # 每次都重算摘要 (確保修正後的邏輯生效)
    _summarize_tdcc()


def _summarize_tdcc():
    """將集保原始資料彙整為每支股票的大戶/散戶比例"""
    tdcc_files = sorted([f for f in os.listdir(TDCC_DIR) if f.startswith("tdcc_") and f.endswith(".csv")])
    if not tdcc_files:
        return

    summary_path = os.path.join(TDCC_DIR, "tdcc_summary.csv")

    all_summaries = []
    for fn in tqdm(tdcc_files, desc="彙整集保"):
        filepath = os.path.join(TDCC_DIR, fn)
        try:
            df = pd.read_csv(filepath, encoding="utf-8-sig")
            if len(df.columns) < 6:
                continue

            date_col = df.columns[0]   # 資料日期
            ticker_col = df.columns[1]  # 證券代號
            level_col = df.columns[2]   # 持股分級
            count_col = df.columns[3]   # 人數
            shares_col = df.columns[4]  # 股數
            pct_col = df.columns[5]     # 佔比%

            df[pct_col] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
            df[level_col] = df[level_col].astype(str).str.strip()

            # 持股分級 (TDCC 標準 17 級):
            #   1-15: 實際持股分級 (1=1-999股 ... 15=1,000,001股以上)
            #   16: 自行保管
            #   17: 合計 (固定100%)
            # 散戶: Level 1-3 (< 10,000股 = < 10張)
            # 大戶: Level 12-15 (>= 400,001股 = >= 400張)
            # 只使用 Level 1-15，排除 16(自行保管) 和 17(合計)
            df_actual = df[df[level_col].astype(int) <= 15].copy()
            grouped = df_actual.groupby([date_col, ticker_col])

            for (dt, ticker), g in grouped:
                g = g.sort_values(level_col)
                pcts = g[pct_col].tolist()

                retail_pct = sum(pcts[:3]) if len(pcts) >= 3 else 0
                whale_pct = sum(pcts[11:15]) if len(pcts) >= 15 else sum(pcts[-4:]) if len(pcts) >= 4 else 0

                all_summaries.append({
                    "Date": str(dt),
                    "Ticker": str(ticker).strip(),
                    "Retail_Pct": round(retail_pct, 2),
                    "Whale_Pct": round(whale_pct, 2),
                    "Total_Holders": g[count_col].sum() if count_col in g.columns else 0,
                })
        except Exception:
            continue

    if all_summaries:
        df_summary = pd.DataFrame(all_summaries)
        df_summary = df_summary.drop_duplicates(subset=["Date", "Ticker"], keep="last")
        df_summary = df_summary.sort_values(["Ticker", "Date"])
        df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"  集保摘要: {len(df_summary):,} 筆 → {summary_path}")


# ==============================================================================
# Step 10: 大盤指數 + 國際指標
# ==============================================================================
def step10_update_indices():
    import yfinance as yf

    print("\n" + "=" * 60)
    print("Step 10/10: 更新大盤指數 + 國際指標")
    print("=" * 60)

    indices = {
        "^TWII": "加權指數",
        "^GSPC": "SP500",
        "^SOX": "費城半導體",
        "^VIX": "VIX恐慌指數",
        "USDTWD=X": "美元台幣",
    }

    for symbol, name in indices.items():
        safe_name = symbol.replace("^", "").replace("=", "")
        filepath = os.path.join(INDEX_DIR, f"index_{safe_name}.csv")

        # 美股指數永遠重抓最後 3 天（覆蓋盤中不完整資料）
        start_dt = date(2020, 1, 1)
        if os.path.exists(filepath):
            try:
                df_old = pd.read_csv(filepath, dtype={"Date": str})
                last = pd.to_datetime(df_old["Date"]).max().date()
                start_dt = last - timedelta(days=3)
            except Exception:
                pass

        try:
            end_dt = TODAY + timedelta(days=1)  # yfinance end 是 exclusive
            df = yf.download(
                symbol, start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"), progress=False,
            )
            if df.empty:
                print(f"  {name}: 無新資料")
                continue

            df = df.reset_index()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

            if os.path.exists(filepath):
                df_old = pd.read_csv(filepath, dtype={"Date": str})
                df = pd.concat([df_old, df], ignore_index=True)
                df = df.drop_duplicates(subset=["Date"], keep="last")
            df = df.sort_values("Date")
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
            print(f"  {name}: 更新至 {df['Date'].max()}")
            time.sleep(0.5)

        except Exception as e:
            print(f"  {name}: 失敗 ({e})")


# ==============================================================================
# Step 11: 外資持股比率 (TWSE + TPEx)
# ==============================================================================
def step11_update_foreign_ownership():
    """下載外資（含陸資）持股比率。

    TWSE: https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS
    TPEx: https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php

    存為 外資持股/YYYYMMDD.csv，欄位: Ticker, Issued_Shares, Foreign_Held, Foreign_Pct
    """
    print("\n" + "=" * 60)
    print("  Step 11: 外資持股比率")
    print("=" * 60)

    # 決定要抓哪些日期（最近 5 個交易日，避免遺漏）
    dates_to_fetch = []
    for i in range(7):
        d = TODAY - timedelta(days=i)
        if d.weekday() < 5:
            dates_to_fetch.append(d)
        if len(dates_to_fetch) >= 5:
            break

    for dt in dates_to_fetch:
        fname = dt.strftime("%Y%m%d") + ".csv"
        fpath = os.path.join(FOREIGN_OWN_DIR, fname)
        if os.path.exists(fpath):
            sz = os.path.getsize(fpath)
            if sz > 1000:
                print(f"  {dt}: 已存在 ({sz:,} bytes)")
                continue

        print(f"  {dt}: 下載中...", end=" ")
        rows = []

        # --- TWSE ---
        twse_rows = _fetch_foreign_twse(dt)
        rows.extend(twse_rows)
        time.sleep(REQUEST_PAUSE)

        # --- TPEx ---
        tpex_rows = _fetch_foreign_tpex(dt)
        rows.extend(tpex_rows)
        time.sleep(REQUEST_PAUSE)

        if not rows:
            print("無資料 (可能非交易日)")
            continue

        df = pd.DataFrame(rows, columns=["Ticker", "Issued_Shares", "Foreign_Held", "Foreign_Pct"])
        df = df.drop_duplicates(subset=["Ticker"], keep="first")
        df = df.sort_values("Ticker")
        df.to_csv(fpath, index=False, encoding="utf-8-sig")
        print(f"OK ({len(df)} 檔)")


def _parse_twse_number(s):
    """清除逗號，轉 float；失敗回 NaN。"""
    if not s or s == "--" or s == "N/A":
        return np.nan
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return np.nan


def _fetch_foreign_twse(dt):
    """從 TWSE 抓外資持股（上市）。"""
    url = (
        "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
        f"?response=json&date={dt.strftime('%Y%m%d')}&selectType=ALLBUT0999"
    )
    rows = []
    try:
        r = SESSION.get(url, timeout=30)
        data = r.json()
        if data.get("stat") != "OK" or "data" not in data:
            return rows
        for row in data["data"]:
            ticker = str(row[0]).strip()
            if not ticker or not ticker[0].isdigit():
                continue
            # [3]=發行股數 [5]=外資持有股數 [7]=外資持股比率
            issued = _parse_twse_number(row[3])
            held = _parse_twse_number(row[5])
            pct = _parse_twse_number(row[7])
            rows.append((ticker, issued, held, pct))
    except Exception as e:
        print(f"[TWSE外資] {e}", end=" ")
    return rows


def _fetch_foreign_tpex(dt):
    """從 TPEx 抓外資持股（上櫃）。"""
    roc_year = dt.year - 1911
    roc_date = f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
    url = (
        "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php"
        f"?l=zh-tw&d={roc_date}&t=D"
    )
    rows = []
    try:
        r = SESSION.get(url, timeout=30)
        data = r.json()
        # TPEx 格式: tables[0]["data"], 非 aaData
        table = []
        tables = data.get("tables", [])
        if tables and isinstance(tables[0], dict):
            table = tables[0].get("data", [])
        if not table:
            # 舊版 API 可能用 aaData
            table = data.get("aaData", [])
        if not table:
            return rows
        for row in table:
            # [0]=排行 [1]=代號 [2]=名稱 [3]=發行股數 [5]=持有股數 [7]=持股比率
            ticker = str(row[1]).strip()
            if not ticker or not ticker[0].isdigit():
                continue
            issued = _parse_twse_number(row[3])
            held = _parse_twse_number(row[5])
            pct = _parse_twse_number(row[7])
            rows.append((ticker, issued, held, pct))
    except Exception as e:
        print(f"[TPEx外資] {e}", end=" ")
    return rows


# ==============================================================================
# 主程式
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="台股資料整合更新")
    parser.add_argument("--step", type=int, choices=range(1, 12),
                        help="只執行指定步驟 (1-11)")
    parser.add_argument("--skip-revenue", action="store_true",
                        help="跳過月營收更新 (較慢)")
    parser.add_argument("--retry-revenue", action="store_true",
                        help="只重試月營收未補齊的股票 (2025-09起)")
    args = parser.parse_args()

    print("=" * 60)
    print("  台股資料整合更新系統")
    print(f"  資料目錄: {BASE_DIR}")
    print(f"  今日日期: {TODAY}")
    print("=" * 60)

    steps = {
        1: ("日K資料", step1_update_daily_k),
        2: ("法人買賣超", step2_update_fund_data),
        3: ("融資融券", step3_update_margin_data),
        4: ("合併法人→日K", step4_merge_fund_into_daily_k),
        5: ("合併融資券→日K", step5_merge_margin_into_daily_k),
        6: ("月營收", step6_update_revenue),
        7: ("技術指標", step7_calc_indicators),
        8: ("季報財務", step8_update_financial),
        9: ("集保分散表", step9_update_tdcc),
        10: ("大盤指數", step10_update_indices),
        11: ("外資持股比率", step11_update_foreign_ownership),
    }

    if args.retry_revenue:
        print("\n重試月營收 (只補 2025-09 起缺少的)")
        step6_update_revenue(retry_only=True)
        return

    if args.step:
        name, func = steps[args.step]
        print(f"\n只執行 Step {args.step}: {name}")
        func()
    else:
        start_time = time.time()
        for i, (name, func) in steps.items():
            if i == 6 and args.skip_revenue:
                print(f"\n跳過 Step 6: 月營收")
                continue
            func()
        elapsed = time.time() - start_time
        print("\n" + "=" * 60)
        print(f"  全部更新完成！耗時 {elapsed / 60:.1f} 分鐘")
        print("=" * 60)

        # 自動重建 ML snapshot cache
        print("\n  重建 ML 推論快照...")
        try:
            from ml.dataset import build_latest_snapshot
            snapshot = build_latest_snapshot(verbose=True)
            snapshot_path = os.path.join(BASE_DIR, "ml", "models", "snapshot_cache.pkl")
            snapshot.to_pickle(snapshot_path)
            # 清除舊的 server-side cache，強制下次載入新的
            for old in [
                os.path.join(BASE_DIR, "ml", "models", "latest_snapshot_cache.pkl"),
                os.path.join(BASE_DIR, "ml", "models", "latest_snapshot_cache_meta.json"),
            ]:
                if os.path.exists(old):
                    os.remove(old)
            print(f"  ML 快照已更新: {len(snapshot)} 檔股票")
        except Exception as e:
            print(f"  ML 快照重建失敗: {e}")


if __name__ == "__main__":
    main()
