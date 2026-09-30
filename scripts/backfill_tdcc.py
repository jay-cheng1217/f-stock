# ==============================================================================
# backfill_tdcc.py - 回補集保股權分散表 (TDCC) 歷史資料
#
# 來源:
#   TDCC API: POST https://www.tdcc.com.tw/server/web/StockQuery/queryStockCustStat
#
# TDCC 每週五更新。逐週五抓取資料。
#
# 用法: python scripts/backfill_tdcc.py [--start-date 2024-01-05]
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
BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
os.makedirs(TDCC_DIR, exist_ok=True)

RATE_LIMIT = 5.0  # seconds between requests
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
        "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
    })
    return s


SESSION = build_session()


# ==============================================================================
# 抓取 TDCC 資料
# ==============================================================================
def fetch_tdcc_data(date_str_8):
    """
    從 TDCC API 抓取某一週的集保分散資料
    date_str_8: YYYYMMDD (必須是週五)
    Returns: DataFrame or None
    """
    url = "https://www.tdcc.com.tw/server/web/StockQuery/queryStockCustStat"
    payload = {
        "SqlMethod": "StockNo",
        "StockNo": "",
        "StartDate": date_str_8,
        "EndDate": date_str_8,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
        "Origin": "https://www.tdcc.com.tw",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.post(url, json=payload, headers=headers, timeout=120, verify=False)
            if r.status_code != 200:
                tqdm.write(f"  TDCC {date_str_8} HTTP {r.status_code}, 重試中...")
                time.sleep(RATE_LIMIT * 2)
                continue

            data = r.json()

            if not data:
                return None

            # API 回傳格式: list of dicts or similar
            # 嘗試解析不同格式
            records = []

            if isinstance(data, list):
                # 直接是 list of records
                for item in data:
                    record = _parse_tdcc_item(item, date_str_8)
                    if record:
                        records.append(record)
            elif isinstance(data, dict):
                # 可能有 data 或 result 欄位
                items = data.get("data") or data.get("result") or data.get("items") or []
                if isinstance(items, list):
                    for item in items:
                        record = _parse_tdcc_item(item, date_str_8)
                        if record:
                            records.append(record)
                else:
                    tqdm.write(f"  TDCC {date_str_8} 回傳格式未知: {list(data.keys())[:5]}")
                    return None

            if records:
                df = pd.DataFrame(records)
                return df

            return None

        except requests.exceptions.JSONDecodeError:
            # 嘗試用 CSV 格式解析
            try:
                from io import StringIO
                df = pd.read_csv(StringIO(r.text))
                if not df.empty:
                    return _normalize_tdcc_df(df, date_str_8)
            except Exception:
                pass
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TDCC {date_str_8} JSON 解析失敗, 重試...")
                time.sleep(RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TDCC {date_str_8} 解析失敗")
                return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  TDCC {date_str_8} 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(RATE_LIMIT * 2)
            else:
                tqdm.write(f"  TDCC {date_str_8} 失敗: {e}")
                return None

    return None


def _parse_tdcc_item(item, date_str_8):
    """解析單筆 TDCC 資料"""
    if not isinstance(item, dict):
        return None

    # TDCC API 常見欄位名
    ticker = (item.get("StkNo") or item.get("StockNo") or
              item.get("證券代號") or item.get("stkno") or "")
    level = (item.get("HoldingSharesLevel") or item.get("Level") or
             item.get("持股分級") or item.get("holdingSharesLevel") or "")
    people = (item.get("People") or item.get("Numpeople") or
              item.get("人數") or item.get("people") or item.get("numPeople") or 0)
    shares = (item.get("HoldingShares") or item.get("Shares") or
              item.get("股數") or item.get("holdingShares") or item.get("shares") or 0)
    pct = (item.get("Percent") or item.get("Pct") or
           item.get("占集保庫存數比例%") or item.get("percent") or
           item.get("holdingSharesPer") or item.get("HoldingSharesPer") or 0)

    ticker = str(ticker).strip()
    if not ticker:
        return None

    try:
        level = int(str(level).strip())
    except (ValueError, TypeError):
        return None

    try:
        people = int(str(people).replace(",", "").strip())
    except (ValueError, TypeError):
        people = 0

    try:
        shares = int(str(shares).replace(",", "").strip())
    except (ValueError, TypeError):
        shares = 0

    try:
        pct = float(str(pct).replace(",", "").strip())
    except (ValueError, TypeError):
        pct = 0.0

    return {
        "資料日期": date_str_8,
        "證券代號": ticker,
        "持股分級": level,
        "人數": people,
        "股數": shares,
        "占集保庫存數比例%": pct,
    }


def _normalize_tdcc_df(df, date_str_8):
    """將各種格式的 DataFrame 正規化為標準格式"""
    if df.empty:
        return None

    # 嘗試對應欄位
    col_map = {}
    for col in df.columns:
        col_clean = str(col).strip()
        if "代號" in col_clean or "StkNo" in col_clean.lower() or "stockno" in col_clean.lower():
            col_map["證券代號"] = col
        elif "分級" in col_clean or "level" in col_clean.lower():
            col_map["持股分級"] = col
        elif "人數" in col_clean or "people" in col_clean.lower():
            col_map["人數"] = col
        elif "股數" in col_clean and "比例" not in col_clean:
            col_map["股數"] = col
        elif "比例" in col_clean or "percent" in col_clean.lower() or "per" in col_clean.lower():
            col_map["占集保庫存數比例%"] = col

    if len(col_map) < 4:
        return None

    result = pd.DataFrame()
    result["資料日期"] = date_str_8
    for target, source in col_map.items():
        result[target] = df[source]

    # 確保必要欄位存在
    for col in ["資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%"]:
        if col not in result.columns:
            result[col] = 0

    result["資料日期"] = date_str_8
    return result[["資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%"]]


# ==============================================================================
# TDCC OpenData (最新一週快速下載)
# ==============================================================================
def _standardize_tdcc_columns(df):
    """Normalize daily TDCC csv columns to a stable schema by column name."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["Date", "Ticker", "Level", "Holders", "Shares", "Pct"])

    alias_map = {
        "Date": ["資料日期", "鞈??交?", "Date"],
        "Ticker": ["證券代號", "霅隞??", "Ticker"],
        "Level": ["持股分級", "???", "Level", "HoldingSharesLevel"],
        "Holders": ["人數", "鈭箸", "People", "Numpeople"],
        "Shares": ["股數", "?⊥", "HoldingShares", "Shares"],
        "Pct": ["占集保庫存數比例%", "??靽澈摮瘥?%", "Percent", "Pct", "HoldingSharesPer"],
    }

    clean_cols = {str(col).strip().replace("\ufeff", ""): col for col in df.columns}
    rename_map = {}
    for target, aliases in alias_map.items():
        source = next((clean_cols[a] for a in aliases if a in clean_cols), None)
        if source is not None:
            rename_map[source] = target

    required = {"Date", "Ticker", "Level", "Holders", "Shares", "Pct"}
    if required - set(rename_map.values()):
        return pd.DataFrame(columns=["Date", "Ticker", "Level", "Holders", "Shares", "Pct"])

    out = df.rename(columns=rename_map)[["Date", "Ticker", "Level", "Holders", "Shares", "Pct"]].copy()
    out["Date"] = (
        out["Date"].astype(str).str.replace("/", "", regex=False).str.replace("-", "", regex=False).str.strip()
    )
    out["Ticker"] = out["Ticker"].astype(str).str.strip()

    for col in ["Level", "Holders", "Shares", "Pct"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["Date", "Ticker", "Level"])
    out = out[out["Date"].astype(str).str.fullmatch(r"\d{8}")]
    out = out[out["Ticker"].astype(str).str.len() > 0]
    out["Level"] = out["Level"].astype(int)
    out = out[(out["Level"] >= 1) & (out["Level"] <= 17)]
    out["Holders"] = out["Holders"].fillna(0).astype(int)
    out["Shares"] = out["Shares"].fillna(0).astype(int)
    out["Pct"] = out["Pct"].fillna(0.0).astype(float)
    return out.reset_index(drop=True)


def fetch_tdcc_opendata():
    """從 TDCC OpenData 下載最新一週資料"""
    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    try:
        r = SESSION.get(url, timeout=60, verify=False)
        r.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        if df.empty:
            return None, None

        date_col = df.columns[0]
        latest_date = str(df[date_col].iloc[0]).replace("/", "").replace("-", "")

        # 正規化欄位名
        std_cols = ["資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%"]
        if len(df.columns) >= 6:
            df.columns = std_cols[:len(df.columns)]
            df["資料日期"] = latest_date
            return df[std_cols], latest_date

        return None, None
    except Exception as e:
        tqdm.write(f"  OpenData 下載失敗: {e}")
        return None, None


# ==============================================================================
# 計算摘要
# ==============================================================================
def rebuild_summary():
    """重建 tdcc_summary.csv"""
    print("\n重建集保摘要 (tdcc_summary.csv)...")

    tdcc_files = sorted([
        f for f in os.listdir(TDCC_DIR)
        if f.startswith("tdcc_") and f.endswith(".csv") and f != "tdcc_summary.csv"
    ])
    if not tdcc_files:
        print("  無集保原始資料")
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
            df[count_col] = pd.to_numeric(df[count_col], errors="coerce").fillna(0)
            df[level_col] = pd.to_numeric(df[level_col], errors="coerce")
            df = df.dropna(subset=[level_col])
            df[level_col] = df[level_col].astype(int)

            # 持股分級 (TDCC 標準 17 級):
            #   1-12: 散戶 (< 400張)
            #   15-17: 大戶 (> 1000張, 含自行保管/合計)
            # Retail: Level 1-12, Whale: Level 15-17
            df_actual = df[df[level_col] <= 15].copy()
            grouped = df_actual.groupby([date_col, ticker_col])

            for (dt, ticker), g in grouped:
                g = g.sort_values(level_col)
                levels = g[level_col].tolist()
                pcts = g[pct_col].tolist()

                # 口徑(2026-07-16 PM 核定,單一真相=twstock._summarize_tdcc):
                # 散戶 = Level 1-9 (<100張)、大戶 = Level 15 (>=1000張,千張大戶)
                retail_pct = sum(p for lv, p in zip(levels, pcts) if 1 <= lv <= 9)
                whale_pct = sum(p for lv, p in zip(levels, pcts) if lv == 15)

                total_holders = int(g[count_col].sum())

                all_summaries.append({
                    "Date": str(dt),
                    "Ticker": str(ticker).strip(),
                    "Retail_Pct": round(retail_pct, 2),
                    "Whale_Pct": round(whale_pct, 2),
                    "Total_Holders": total_holders,
                })
        except Exception as e:
            tqdm.write(f"  處理 {fn} 失敗: {e}")
            continue

    if all_summaries:
        df_summary = pd.DataFrame(all_summaries)
        df_summary = df_summary.drop_duplicates(subset=["Date", "Ticker"], keep="last")
        df_summary = df_summary.sort_values(["Ticker", "Date"])
        df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"  集保摘要: {len(df_summary):,} 筆 -> {summary_path}")
    else:
        print("  無有效資料可彙整")


# ==============================================================================
# 主程式
# ==============================================================================
def _standardize_tdcc_columns_v2(df):
    """Compatibility wrapper for the normalized daily TDCC schema."""
    return _standardize_tdcc_columns(df)


def fetch_tdcc_opendata_v2():
    """Fetch TDCC OpenData and normalize columns by header name."""
    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    try:
        r = SESSION.get(url, timeout=60, verify=False)
        r.raise_for_status()
        from io import StringIO

        raw_df = pd.read_csv(StringIO(r.text))
        df = _standardize_tdcc_columns_v2(raw_df)
        if df.empty:
            return None, None

        latest_date = str(df["Date"].iloc[0])
        out = df.rename(
            columns={
                "Date": "\u8cc7\u6599\u65e5\u671f",
                "Ticker": "\u8b49\u5238\u4ee3\u865f",
                "Level": "\u6301\u80a1\u5206\u7d1a",
                "Holders": "\u4eba\u6578",
                "Shares": "\u80a1\u6578",
                "Pct": "\u5360\u96c6\u4fdd\u5eab\u5b58\u6578\u6bd4\u4f8b%",
            }
        )
        return out, latest_date
    except Exception as e:
        tqdm.write(f"  OpenData 失敗: {e}")
        return None, None


def rebuild_summary_v2():
    """Rebuild tdcc_summary.csv from daily TDCC files."""
    print("\n重建集保摘要 (tdcc_summary.csv)...")

    tdcc_files = sorted([
        f for f in os.listdir(TDCC_DIR)
        if f.startswith("tdcc_") and f.endswith(".csv") and f != "tdcc_summary.csv"
    ])
    if not tdcc_files:
        print("  找不到 TDCC 日檔")
        return

    summary_path = os.path.join(TDCC_DIR, "tdcc_summary.csv")
    all_summaries = []
    skipped_files = 0

    for fn in tqdm(tdcc_files, desc="彙整TDCC"):
        filepath = os.path.join(TDCC_DIR, fn)
        try:
            raw_df = pd.read_csv(filepath, encoding="utf-8-sig", dtype=str)
            df = _standardize_tdcc_columns_v2(raw_df)
            if df.empty:
                skipped_files += 1
                continue

            detail_df = df[df["Level"] <= 15].copy()
            grouped = detail_df.groupby(["Date", "Ticker"], sort=False)

            for (dt, ticker), g in grouped:
                # PM-approved single source of truth (2026-07-16), aligned with
                # twstock._summarize_tdcc and every downstream TDCC feature:
                # retail = levels 1-9 (<100 lots), whale = level 15 only
                # (1,000+ lots). Levels 10-14 are neither bucket.
                retail_pct = float(g.loc[g["Level"].between(1, 9), "Pct"].sum())
                whale_pct = float(g.loc[g["Level"].eq(15), "Pct"].sum())
                total_holders = int(g.loc[g["Level"].between(1, 15), "Holders"].sum())

                all_summaries.append({
                    "Date": str(dt),
                    "Ticker": str(ticker).strip(),
                    "Retail_Pct": round(retail_pct, 2),
                    "Whale_Pct": round(whale_pct, 2),
                    "Total_Holders": total_holders,
                })
        except Exception as e:
            skipped_files += 1
            tqdm.write(f"  跳過 {fn}: {e}")
            continue

    if all_summaries:
        df_summary = pd.DataFrame(all_summaries)
        df_summary["Date"] = df_summary["Date"].astype(str).str.strip()
        df_summary["Ticker"] = df_summary["Ticker"].astype(str).str.strip()
        df_summary = df_summary[df_summary["Date"].str.fullmatch(r"\d{8}")]
        df_summary = df_summary[df_summary["Ticker"].str.len() > 0]
        df_summary = df_summary.drop_duplicates(subset=["Date", "Ticker"], keep="last")
        df_summary = df_summary.sort_values(["Ticker", "Date"]).reset_index(drop=True)
        df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"  集保摘要: {len(df_summary):,} 筆 -> {summary_path}")
        if skipped_files:
            print(f"  略過 {skipped_files} 個檔案（欄位異常或讀取失敗）")
    else:
        print("  沒有可彙整的 TDCC 資料")


rebuild_summary = rebuild_summary_v2
fetch_tdcc_opendata = fetch_tdcc_opendata_v2


def generate_fridays(start_date, end_date):
    """產生週五日期列表"""
    fridays = []
    d = start_date
    # 調整到第一個週五
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)

    while d <= end_date:
        fridays.append(d)
        d += timedelta(days=7)

    return fridays


def main():
    parser = argparse.ArgumentParser(description="回補集保股權分散表 (TDCC) 歷史資料")
    parser.add_argument("--start-date", type=str, default="2024-01-05",
                        help="起始日期 YYYY-MM-DD (預設 2024-01-05，必須是週五)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="結束日期 YYYY-MM-DD (預設今天)")
    parser.add_argument("--skip-summary", action="store_true",
                        help="跳過重建摘要")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()

    print("=" * 60)
    print("回補集保股權分散表 (TDCC)")
    print(f"  範圍: {start_date} ~ {end_date}")
    print(f"  儲存: {TDCC_DIR}")
    print("=" * 60)

    fridays = generate_fridays(start_date, end_date)

    # 過濾已下載的日期
    pending_fridays = []
    for d in fridays:
        filepath = os.path.join(TDCC_DIR, f"tdcc_{d.strftime('%Y%m%d')}.csv")
        if not os.path.exists(filepath):
            pending_fridays.append(d)

    print(f"  週五共 {len(fridays)} 天, 待下載 {len(pending_fridays)} 天, 已完成 {len(fridays) - len(pending_fridays)} 天")

    if not pending_fridays:
        print("  所有日期皆已下載完畢!")
        if not args.skip_summary:
            rebuild_summary()
        return

    # 先嘗試 OpenData 取得最新資料
    print("\n嘗試 OpenData 下載最新資料...")
    df_od, od_date = fetch_tdcc_opendata()
    if df_od is not None and od_date:
        od_path = os.path.join(TDCC_DIR, f"tdcc_{od_date}.csv")
        if not os.path.exists(od_path):
            df_od.to_csv(od_path, index=False, encoding="utf-8-sig")
            print(f"  OpenData 已下載: tdcc_{od_date}.csv ({len(df_od):,} 筆)")
            # 從待下載中移除
            try:
                od_date_obj = datetime.strptime(od_date, "%Y%m%d").date()
                pending_fridays = [d for d in pending_fridays if d != od_date_obj]
            except Exception:
                pass
        else:
            print(f"  OpenData 日期 {od_date} 已存在")

    success_count = 0
    fail_count = 0
    skip_count = 0
    failed_dates = []

    for d in tqdm(pending_fridays, desc="TDCC"):
        date_str_8 = d.strftime("%Y%m%d")
        filepath = os.path.join(TDCC_DIR, f"tdcc_{date_str_8}.csv")

        if os.path.exists(filepath):
            skip_count += 1
            continue

        df = fetch_tdcc_data(date_str_8)

        if df is not None and not df.empty:
            # 確保欄位順序
            expected_cols = ["資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%"]
            for col in expected_cols:
                if col not in df.columns:
                    df[col] = 0
            df = df[expected_cols]

            tmp_path = filepath + ".tmp"
            df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            os.replace(tmp_path, filepath)
            success_count += 1
        else:
            fail_count += 1
            failed_dates.append(date_str_8)

        time.sleep(RATE_LIMIT + random.uniform(0, 2))

    # 重建摘要
    if not args.skip_summary:
        rebuild_summary()

    # 摘要
    print("\n" + "=" * 60)
    print("回補完成")
    print(f"  成功: {success_count} 天")
    print(f"  略過(已存在): {skip_count} 天")
    print(f"  失敗: {fail_count} 天")
    if failed_dates:
        print(f"  失敗日期: {', '.join(failed_dates[:20])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
