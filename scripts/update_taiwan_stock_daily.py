# ==============================================================================
# 台股日K CSV 增量更新（本機 Windows 版）
# - 只更新本機已有的 <ticker>.csv 檔
# - 維持舊欄位順序，新增欄位自動接到後面
# - 兩段式抓取：<代碼>.TW → <代碼>.TWO
# - 原子寫檔，避免中斷壞檔
# ==============================================================================

import os
import time
from datetime import datetime, timedelta, date

import pandas as pd
import yfinance as yf
from tqdm import tqdm

# ========= 1) 參數 =========
# ⚠️ 用 raw string 避免跳脫字元問題（Windows 路徑）
UPDATE_DIR = r"F:\股票分析\日K資料"

TODAY = datetime.now().date()
RECENT_TRADING_DAY_GRACE = 2     # 離最近交易日 <= 2 天視為已最新（週末/假日）
REQUEST_PAUSE = 0.4              # yfinance 呼叫之間的退避秒數

# ========= 2) 工具函式 =========
def list_local_tickers_from_csv(folder: str):
    """由資料夾列出本地已有 <ticker>.csv 清單（僅數字檔名前綴）。"""
    tickers = []
    for fn in os.listdir(folder):
        if fn.lower().endswith(".csv"):
            t = os.path.splitext(fn)[0]
            if t.isdigit():
                tickers.append(t)
    return sorted(tickers)

def parse_last_date(df: pd.DataFrame) -> date | None:
    """從 DataFrame 解析最後日期；若失敗回傳 None。"""
    if "Date" not in df.columns or df.empty:
        return None
    dts = pd.to_datetime(df["Date"], errors="coerce")
    dts = dts.dropna()
    if dts.empty:
        return None
    return dts.max().date()

def need_update(last_dt: date) -> bool:
    """判斷是否需要更新（考慮週末/假日寬限）。"""
    if last_dt is None:
        return True
    return (TODAY - last_dt).days > RECENT_TRADING_DAY_GRACE

def safe_to_csv(df: pd.DataFrame, path: str):
    """原子寫檔：先寫 .tmp，再 os.replace 置換正式檔。"""
    tmp_path = path + ".tmp"
    # 用 utf-8-sig 兼容 Excel（可視需求調整）
    df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, path)

def fetch_yf_increment(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """依序嘗試 .TW → .TWO 拉增量，回傳含標準 Date 欄位的 DataFrame。"""
    def _dl(sym):
        return yf.download(
            sym,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            progress=False,
        )

    df_new = _dl(f"{ticker}.TW")
    if df_new.empty:
        time.sleep(REQUEST_PAUSE)
        df_new = _dl(f"{ticker}.TWO")

    if df_new.empty:
        return df_new

    df_new = df_new.reset_index()

    # 攤平多層欄
    if isinstance(df_new.columns, pd.MultiIndex):
        df_new.columns = df_new.columns.get_level_values(0)

    # 標準化日期欄
    if "Date" in df_new.columns:
        df_new["Date"] = pd.to_datetime(df_new["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        if df_new.index.name and "date" in df_new.index.name.lower():
            df_new = df_new.rename_axis("Date").reset_index()
            df_new["Date"] = pd.to_datetime(df_new["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            return pd.DataFrame()

    return df_new

# ========= 3) 主流程 =========
def main():
    if not os.path.isdir(UPDATE_DIR):
        raise FileNotFoundError(f"找不到資料夾：{UPDATE_DIR}")

    tickers = list_local_tickers_from_csv(UPDATE_DIR)
    print(f"準備檢查本地 CSV 共 {len(tickers)} 檔。資料夾：{UPDATE_DIR}")

    count_updated = 0
    count_latest = 0
    count_failed = 0
    failed_list = []

    for ticker in tqdm(tickers, desc="更新進度"):
        path = os.path.join(UPDATE_DIR, f"{ticker}.csv")

        # 讀舊檔
        try:
            # 若早期檔案是 Big5/CP950，可改 encoding="cp950"
            df_old = pd.read_csv(path, dtype={"Date": str}, encoding="utf-8-sig")
        except UnicodeError:
            # 自動退回用 cp950 再試一次
            try:
                df_old = pd.read_csv(path, dtype={"Date": str}, encoding="cp950")
            except Exception:
                count_failed += 1
                failed_list.append(ticker)
                continue
        except Exception:
            count_failed += 1
            failed_list.append(ticker)
            continue

        original_columns = df_old.columns.tolist()
        last_dt = parse_last_date(df_old)

        if not need_update(last_dt):
            count_latest += 1
            continue

        # last_dt 可能是 None（舊檔沒有有效日期）：此處可選擇完整重抓
        if last_dt is None:
            # 你也可以把這裡改成基準日完整回補，例如：
            # start_dt = date(1990, 1, 1)
            count_failed += 1
            failed_list.append(ticker)
            continue

        start_dt = last_dt + timedelta(days=1)

        try:
            df_new = fetch_yf_increment(ticker, start_dt, TODAY)
            if df_new.empty:
                # 多半是休市或暫無資料，歸為已最新
                count_latest += 1
                continue

            df_combined = pd.concat([df_old, df_new], ignore_index=True)

            # 標準化日期 → 去重 → 排序
            df_combined["Date"] = pd.to_datetime(df_combined["Date"], errors="coerce")
            df_combined = df_combined.dropna(subset=["Date"])
            df_combined["Date"] = df_combined["Date"].dt.strftime("%Y-%m-%d")
            df_combined = df_combined.drop_duplicates(subset=["Date"], keep="last")
            df_combined = df_combined.sort_values("Date", ascending=True)

            # 保留舊欄位順序 + 追加新欄位
            all_cols = df_combined.columns.tolist()
            new_cols = [c for c in all_cols if c not in original_columns]
            final_cols = [c for c in (original_columns + new_cols) if c in df_combined.columns]
            df_combined = df_combined[final_cols]

            safe_to_csv(df_combined, path)
            count_updated += 1
            time.sleep(REQUEST_PAUSE)

        except Exception:
            count_failed += 1
            failed_list.append(ticker)
            continue

    # ========= 總結 =========
    print("\n" + "=" * 56)
    print("每日資料更新完成！")
    print(f"資料夾：{UPDATE_DIR}")
    print("-" * 56)
    print(f"✅ 成功更新：{count_updated}")
    print(f"👍 已是最新：{count_latest}")
    print(f"❌ 失敗檔案：{count_failed}")
    if failed_list:
        print("失敗清單（最多顯示 50）：")
        print(", ".join(failed_list[:50]) + (" ..." if len(failed_list) > 50 else ""))
    print("=" * 56)

if __name__ == "__main__":
    main()
