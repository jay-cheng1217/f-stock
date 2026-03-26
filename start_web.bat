@echo off
chcp 65001 >nul
title Stock Web Server
cd /d F:\stock

set "WEB_PORT=8001"
set "PYTHON_EXE=C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE="
if not defined PYTHON_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
    )
)

:: Get LAN IP for mobile access
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0"') do (
    set "LAN_IP=%%a"
)
set "LAN_IP=%LAN_IP: =%"

echo.
echo   Stock Web Server
echo   Local:  http://127.0.0.1:%WEB_PORT%
if defined LAN_IP echo   LAN:    http://%LAN_IP%:%WEB_PORT%  (mobile)
echo   Press Ctrl+C to stop
if defined PYTHON_EXE echo   Python: %PYTHON_EXE%
echo.

if not defined PYTHON_EXE (
    echo Python executable not found.
    pause
    exit /b 1
)

"%PYTHON_EXE%" app.py --host 0.0.0.0 --port %WEB_PORT%
pause
