# -*- coding: utf-8 -*-
"""一次性:重抓全部 TPEX 法人快取(修正投信/自營欄位錯位)並重併日K。

背景:2026-07-02 發現 _fetch_tpex_fund 欄位映射錯誤(投信=外資自營淨≈0、
自營=外資合計+投信),上櫃股 Trust/Dealer 歷史全錯。Foreign 一直正確。
流程:備份舊快取 → 逐日重抓(用修正後的 _fetch_tpex_fund)→ step4 重併日K。
"""
import os, sys, glob, shutil, time
sys.path.insert(0, r"F:\stock")
import twstock as tw

FUND_DIR = tw.FUND_CACHE_DIR
BACKUP_DIR = os.path.join(os.path.dirname(FUND_DIR), "法人快取_backup_tpex_bug")

def main():
    files = sorted(glob.glob(os.path.join(FUND_DIR, "fund_tpex_*.csv")))
    dates = [os.path.basename(f)[len("fund_tpex_"):-4] for f in files]
    print(f"共 {len(dates)} 個 TPEX 快取待重建 ({dates[0]} ~ {dates[-1]})")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    moved = 0
    for f in files:
        dst = os.path.join(BACKUP_DIR, os.path.basename(f))
        if not os.path.exists(dst):
            shutil.move(f, dst); moved += 1
    print(f"已備份 {moved} 檔 → {BACKUP_DIR}")

    ok = fail = nodata = 0
    for i, d8 in enumerate(dates, 1):
        ds = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        status = tw._fetch_tpex_fund(ds)
        if status == "ok": ok += 1
        elif status == "no_data": nodata += 1
        elif status != "exists": fail += 1
        if i % 100 == 0:
            print(f"  {i}/{len(dates)}  ok={ok} no_data={nodata} fail={fail}", flush=True)
        time.sleep(0.4)  # 溫柔對待 TPEX
    print(f"重抓完成: ok={ok} no_data={nodata} fail={fail}")

    print("重併法人 → 日K (step4)...")
    tw.step4_merge_fund_into_daily_k()
    print("ALL DONE")

if __name__ == "__main__":
    main()
