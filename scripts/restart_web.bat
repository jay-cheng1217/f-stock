@echo off
rem 提權 web 重啟(可重用):殺 8001 進程 -> 可選 DB 熱修 -> 重啟 app.py
rem 註冊: schtasks /create /f /tn "TW_Stock_Web_Restart" /tr "F:\stock\scripts\restart_web.bat" /sc once /st 23:58 /rl HIGHEST
chcp 65001 >nul
cd /d F:\stock
echo [%date% %time%] restart_web start >> logs\web_restart.log

rem 1) 殺掉聽 8001 的進程
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%p >> logs\web_restart.log 2>&1
)
timeout /t 3 /nobreak >nul

rem 2) 若存在 DB 熱修腳本則執行(冪等,無檔跳過)
if exist scripts\hotfix_db_0702.py (
    python scripts\hotfix_db_0702.py >> logs\web_restart.log 2>&1
)

rem 3) 重啟 web(背景無視窗)
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath python -ArgumentList 'app.py','--host','0.0.0.0','--port','8001' -WorkingDirectory 'F:\stock'" >> logs\web_restart.log 2>&1
echo [%date% %time%] restart_web done >> logs\web_restart.log
