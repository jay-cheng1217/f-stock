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

echo.
echo   Stock Web Server
echo   http://127.0.0.1:%WEB_PORT%
echo   Press Ctrl+C to stop
if defined PYTHON_EXE echo   Python: %PYTHON_EXE%
echo.

if not defined PYTHON_EXE (
    echo Python executable not found.
    pause
    exit /b 1
)

"%PYTHON_EXE%" app.py --port %WEB_PORT%
pause
