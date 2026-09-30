@echo off
chcp 65001 >nul
REM Phase 2: US market + predictions + email (scheduled at 05:00)
REM Fetches VIX/S&P500/PHLX after US close, retrains T+1, runs predictions, sends email

cd /d F:\stock
REM 2026-06-01 production entry filter: TQuant reference gate is primary.
set "TQUANT_REFERENCE_GATE_ENABLED=1"
set "TQUANT_REFERENCE_GATE_MIN_CONSENSUS=strong"
"C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\smart_update_auto.py --phase 2 %*
