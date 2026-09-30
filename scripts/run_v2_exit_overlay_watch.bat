@echo off
chcp 65001 >nul
REM Research-only V2 exit overlay watch. Does not update production trading state.

cd /d F:\stock
set "PYTHON_EXE=C:\Users\cheng\AppData\Local\Python\pythoncore-3.14-64\python.exe"
"%PYTHON_EXE%" scripts\v2_exit_overlay_watch.py %*
if errorlevel 1 exit /b %errorlevel%

echo.
echo Running V2 overlay decision...
"%PYTHON_EXE%" -m scripts.v2_overlay_decision >> logs\v2_overlay_decision.log 2>&1
if errorlevel 1 echo [WARN] decision script returned non-zero
