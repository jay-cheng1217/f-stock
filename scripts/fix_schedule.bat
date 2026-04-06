@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_schedule.ps1"
exit /b %errorlevel%
