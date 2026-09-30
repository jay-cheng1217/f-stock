@echo off
schtasks /create /f /tn "TW_Stock_ChipK_Relaunch" /tr "\"C:\Program Files (x86)\CMoney\CMoney²z°]Ä_\AppViewer.exe\"" /sc once /st 23:59 /rl HIGHEST
echo exitcode=%errorlevel% > F:\stock\logs\chipk_task_register.txt
