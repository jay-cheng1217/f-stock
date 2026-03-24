@echo off
REM 台股每週模型重訓練
REM 建議排程: 每週六 10:00 執行
REM Windows Task Scheduler:
REM   schtasks /create /tn "TW_Stock_Weekly_Retrain" /tr "F:\stock\scripts\run_weekly_retrain.bat" /sc weekly /d SAT /st 10:00

cd /d F:\stock
python scripts\daily_pipeline.py --retrain --force >> logs\retrain_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
