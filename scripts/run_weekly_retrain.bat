@echo off
setlocal
chcp 65001 >nul
REM Weekly full retrain pipeline
REM Scheduled by Windows Task Scheduler at 10:00 every Saturday

cd /d F:\stock
if not exist logs mkdir logs

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOG_DATE=%%I

"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\daily_pipeline.py --retrain --force >> "logs\retrain_%LOG_DATE%.log" 2>&1
exit /b %errorlevel%
