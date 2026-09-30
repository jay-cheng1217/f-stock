@echo off
chcp 65001 >nul
REM Champion daily manual position risk-cut reminder. The Python script skips
REM non-trading days and days where Phase-2 has not refreshed the paper book.

cd /d F:\stock
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\send_position_risk_cut_report.py %*
