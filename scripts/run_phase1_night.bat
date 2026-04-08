@echo off
chcp 65001 >nul
REM Phase 1: Taiwan stock data update (scheduled at 23:00)
REM Updates daily K, institutional, margin, revenue, valuation, news, DuckDB ingest
REM Does NOT touch US market data or predictions

cd /d F:\stock
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\smart_update_auto.py --phase 1 %*
