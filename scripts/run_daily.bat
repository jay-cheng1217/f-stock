@echo off
chcp 65001 >nul
REM Smart morning update, prediction, portfolio sync, verify, and email
REM Scheduled by Windows Task Scheduler at 05:00 daily
REM The Python script skips weekends unless called with --force

cd /d F:\stock
REM 2026-06-01 production entry filter: TQuant reference gate is primary.
set "TQUANT_REFERENCE_GATE_ENABLED=1"
set "TQUANT_REFERENCE_GATE_MIN_CONSENSUS=strong"
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\smart_update_auto.py %*
