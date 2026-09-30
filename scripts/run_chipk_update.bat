@echo off
chcp 65001 >nul
REM Standalone ChipK desktop snapshot archive + model-pick diagnosis.
REM Scheduled by Windows Task Scheduler at 20:25 on Taiwan trading weekdays.

cd /d F:\stock
if not exist logs mkdir logs
REM 2026-07-02: 啟用 stale 自癒(自動重開籌碼K桌面版再重抓)
set CHIPK_AUTO_RELAUNCH=1
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\run_chipk_update.py %* >> logs\chipk_update.log 2>&1
