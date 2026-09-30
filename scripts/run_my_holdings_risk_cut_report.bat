@echo off
chcp 65001 >nul
REM REQ-034-v3 my_holdings daily risk-cut reminder.
REM Scheduled by Windows Task Scheduler at 06:30 on weekdays.
REM The Python script is read-only for my_holdings.db and writes local artifacts
REM before attempting email delivery.

cd /d F:\stock
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\send_my_holdings_risk_cut_report.py %*
