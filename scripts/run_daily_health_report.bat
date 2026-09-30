@echo off
chcp 65001 >nul
REM Champion daily health report. The Python script skips non-trading days
REM and days where the Champion paper book has not been updated.

cd /d F:\stock
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\send_daily_health_report.py %*
