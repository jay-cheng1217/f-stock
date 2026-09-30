@echo off
setlocal
chcp 65001 >nul
cd /d F:\stock
python -X utf8 scripts\run_kol_tracker_daily.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo KOL dashboard update failed; see logs\kol_tracker.log.
exit /b %EXIT_CODE%
