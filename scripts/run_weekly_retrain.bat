@echo off
setlocal
chcp 65001 >nul
REM Weekly V2-fast retrain pipeline
REM Scheduled by Windows Task Scheduler at 10:00 every Saturday

cd /d F:\stock
if not exist logs mkdir logs

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOG_DATE=%%I

"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\run_weekly_retrain_guard.py >> "logs\retrain_%LOG_DATE%.log" 2>&1
exit /b %errorlevel%
