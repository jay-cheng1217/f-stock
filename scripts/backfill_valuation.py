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
import time
import random
import argparse
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
BASE_DIR = r"F:\stock"
VALUATION_DIR = os.path.join(BASE_DIR, "估值資料")
os.makedirs(VALUATION_DIR, exist_ok=True)

TWSE_RATE_LIMIT = 3.0  # seconds
TPEX_RATE_LIMIT = 3.0
MAX_RETRIES = 3


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
    pe_mean = pe.dropna().mean()
    if 100 < pe_mean < 120:
        raise ValueError(
            f"[{source_label}] PE 平均值 {pe_mean:.1f}，"
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
    Returns: DataFrame or None
    """
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
    params = {"date": date_str_8, "response": "json"}

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=30, verify=False)
            if r.status_code != 200:
                time.sleep(TWSE_RATE_LIMIT)
                continue

            data = r.json()

            if data.get("stat") != "OK" or not data.get("data"):
                return None  # 非交易日或無資料

            fields = data.get("fields", [])
            rows = data["data"]

            # fields: [證券代號, 證券名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比, 財報年/季]
            records = []
            for row in rows:
                if len(row) < 7:
                    continue
                ticker = str(row[0]).strip()
                name = str(row[1]).strip()
                dividend_yield = row[3]
                pe_ratio = row[5]
                pb_ratio = row[6]

                # 清理數值
                try:
                    dividend_yield = float(str(dividend_yield).replace(",", "").strip()) if str(dividend_yield).strip() not in ("", "-") else None
                except (ValueError, TypeError):
                    dividend_yield = None
                try:
                    pe_ratio = float(str(pe_ratio).replace(",", "").strip()) if str(pe_ratio).strip() not in ("", "-") else None
                except (ValueError, TypeError):
                    pe_ratio = None
                try:
                    pb_ratio = float(str(pb_ratio).replace(",", "").strip()) if str(pb_ratio).strip() not in ("", "-") else None
                except (ValueError, TypeError):
                    pb_ratio = None

                records.append({
                    "Ticker": ticker,
                    "Name": name,
                    "PE_Ratio": pe_ratio,
                    "PB_Ratio": pb_ratio,
                    "Dividend_Yield": dividend_yield,
                    "Market": "上市",
                })

            if records:
                df = pd.DataFrame(records)
                # === 資料品質斷言：防止 API 欄位錯位時默默存入錯誤資料 ===
                _validate_valuation_df(df, f"TWSE {date_str_8}")
                return df
            return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TWSE {date_str_8} 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(TWSE_RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TWSE {date_str_8} 失敗: {e}")
                return None

    return None


# ==============================================================================
# TPEx 本益比/淨值比/殖利率
# ==============================================================================
def fetch_tpex_valuation(date_obj):
    """
    抓取 TPEx 每日本益比/淨值比/殖利率
    date_obj: date object
    Returns: DataFrame or None
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

            data = r.json()

            # TPEx API: tables[0]['data'] (新版) 或 aaData (舊版)
            tables = data.get("tables")
            if tables and isinstance(tables, list) and len(tables) > 0:
                aa_data = tables[0].get("data", [])
            else:
                aa_data = data.get("aaData", [])
            if not aa_data:
                return None  # 非交易日或無資料

            records = []
            for row in aa_data:
                if len(row) < 7:
                    continue
                ticker = str(row[0]).strip()
                name = str(row[1]).strip()

                # TPEx columns:
                # [0]=代號, [1]=名稱, [2]=本益比, [3]=每股股利,
                # [4]=股利年度, [5]=殖利率(%), [6]=股價淨值比, [7]=財報年/季
                pe_ratio = row[2]
                dividend_yield = row[5]
                pb_ratio = row[6]

                try:
                    pe_ratio = float(str(pe_ratio).replace(",", "").strip()) if str(pe_ratio).strip() not in ("", "-", "N/A") else None
                except (ValueError, TypeError):
                    pe_ratio = None
                try:
                    pb_ratio = float(str(pb_ratio).replace(",", "").strip()) if str(pb_ratio).strip() not in ("", "-", "N/A") else None
                except (ValueError, TypeError):
                    pb_ratio = None
                try:
                    dividend_yield = float(str(dividend_yield).replace(",", "").strip()) if str(dividend_yield).strip() not in ("", "-", "N/A") else None
                except (ValueError, TypeError):
                    dividend_yield = None

                records.append({
                    "Ticker": ticker,
                    "Name": name,
                    "PE_Ratio": pe_ratio,
                    "PB_Ratio": pb_ratio,
                    "Dividend_Yield": dividend_yield,
                    "Market": "上櫃",
                })

            if records:
                df = pd.DataFrame(records)
                _validate_valuation_df(df, f"TPEx {date_obj}")
                return df
            return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TPEx {date_obj} 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(TPEX_RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TPEx {date_obj} 失敗: {e}")
                return None

    return None


# ==============================================================================
# 主程式
# ==============================================================================
def generate_trading_days(start_date, end_date):
    """產生交易日列表（排除週末）"""
    days = []
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:  # 0=Mon, 4=Fri
            days.append(d)
        d += timedelta(days=1)
    return days


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

    # 過濾已下載的日期
    pending_days = []
    for d in trading_days:
        filepath = os.path.join(VALUATION_DIR, f"valuation_{d.strftime('%Y%m%d')}.csv")
        if args.force or not os.path.exists(filepath):
            pending_days.append(d)

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

        # 再次檢查（以防並行時已存在，force 模式跳過檢查）
        if not args.force and os.path.exists(filepath):
            skip_count += 1
            continue

        all_data = []

        # TWSE
        df_twse = fetch_twse_valuation(date_str_8)
        if df_twse is not None and not df_twse.empty:
            all_data.append(df_twse)
        time.sleep(TWSE_RATE_LIMIT + random.uniform(0, 1))

        # TPEx
        df_tpex = fetch_tpex_valuation(d)
        if df_tpex is not None and not df_tpex.empty:
            all_data.append(df_tpex)
        time.sleep(TPEX_RATE_LIMIT + random.uniform(0, 1))

        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            combined = combined[["Ticker", "Name", "PE_Ratio", "PB_Ratio", "Dividend_Yield", "Market"]]

            # 寫入暫存檔再 rename (避免寫入中斷產生不完整檔案)
            tmp_path = filepath + ".tmp"
            combined.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            os.replace(tmp_path, filepath)

            success_count += 1
        else:
            # 非交易日（假日/國定假日），不算失敗
            skip_count += 1

    # 摘要
    print("\n" + "=" * 60)
    print("回補完成")
    print(f"  成功: {success_count} 天")
    print(f"  略過(非交易日/已存在): {skip_count} 天")
    print(f"  失敗: {fail_count} 天")
    if failed_dates:
        print(f"  失敗日期: {', '.join(failed_dates[:20])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
