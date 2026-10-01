@echo off
setlocal
chcp 65001 >nul
REM REQ-034-v3 my_holdings daily risk-cut reminder.
REM Scheduled by Windows Task Scheduler at 06:30 on weekdays.
REM The Python script is read-only for my_holdings.db and writes local artifacts
REM before attempting email delivery.
REM Task exit code 0 does not prove delivery (missing SMTP settings exits 0);
REM the send log records email_sent=True/False.

cd /d F:\stock
if not exist logs mkdir logs
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOG_DATE=%%I

"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" -X utf8 scripts\send_my_holdings_risk_cut_report.py %* >> "logs\my_holdings_risk_cut_send_%LOG_DATE%.log" 2>&1
exit /b %errorlevel%
