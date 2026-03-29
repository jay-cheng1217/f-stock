@echo off
schtasks /change /tn "TW_Stock_Daily" /ST 05:00 /RI 0 /Z
echo Done: TW_Stock_Daily updated
pause
