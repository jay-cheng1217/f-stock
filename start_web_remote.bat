@echo off
chcp 65001 >nul
title Stock Web Server (Remote)
cd /d F:\stock

set "WEB_PORT=8001"
set "PYTHON_EXE=C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE="
if not defined PYTHON_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
    )
)

echo.
echo   ========================================
echo   Stock Web Server (Remote Access)
echo   ========================================
echo.

:: Start web server in background
start "StockWeb" /min "%PYTHON_EXE%" app.py --host 0.0.0.0 --port %WEB_PORT%

:: Wait for server to be ready
timeout /t 3 /noq >nul

echo   Local:  http://127.0.0.1:%WEB_PORT%
echo.
echo   Starting Cloudflare Tunnel...
echo   (Public URL will appear below)
echo   ----------------------------------------
echo.

:: Start cloudflare tunnel — the public URL will be printed to console
cloudflared tunnel --url http://localhost:%WEB_PORT%

:: If tunnel exits, also kill web server
taskkill /fi "WINDOWTITLE eq StockWeb" /f >nul 2>&1
pause
