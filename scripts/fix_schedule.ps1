$ErrorActionPreference = "Stop"

function Register-BackupTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(Mandatory = $true)]
        [array]$Trigger,
        [Parameter(Mandatory = $true)]
        $Settings,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $action = New-ScheduledTaskAction -Execute $Command
    $userId = "$env:COMPUTERNAME\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $principal `
        -Description $Description `
        -Force | Out-Null
}

$dailySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 10) `
    -MultipleInstances IgnoreNew `
    -Priority 5

$dailyBackupTriggers = @(
    New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 5:02am
    New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 5:05am
)

Register-BackupTask `
    -TaskName "TW_Stock_Daily_Backup" `
    -Command "F:\stock\scripts\run_daily.bat" `
    -Trigger $dailyBackupTriggers `
    -Settings $dailySettings `
    -Description "Backup triggers for the 05:00 stock update. Locking in smart_update_auto.py prevents duplicate runs."

$myHoldingsRiskCutTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 6:30am

Register-BackupTask `
    -TaskName "TW_Stock_My_Holdings_Risk_Cut_0630" `
    -Command "F:\stock\scripts\run_my_holdings_risk_cut_report.bat" `
    -Trigger $myHoldingsRiskCutTrigger `
    -Settings $dailySettings `
    -Description "REQ-034-v3 my_holdings risk-cut email at 06:30. The script writes artifacts before email and logs real coverage backfill."

$chipkTask = Get-ScheduledTask -TaskName "TW_Stock_ChipK_Update" -ErrorAction SilentlyContinue
if ($chipkTask) {
    Unregister-ScheduledTask -TaskName "TW_Stock_ChipK_Update" -Confirm:$false
}

$weeklySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -MultipleInstances IgnoreNew `
    -Priority 5

$weeklyBackupTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At 10:05am

Register-BackupTask `
    -TaskName "TW_Stock_Weekly_Retrain_Backup" `
    -Command "F:\stock\scripts\run_weekly_retrain.bat" `
    -Trigger $weeklyBackupTrigger `
    -Settings $weeklySettings `
    -Description "Backup trigger for the weekly retrain pipeline. Locking in daily_pipeline.py prevents duplicate runs."

Write-Host ""
Write-Host "Backup tasks created/updated."
Write-Host ""
Get-ScheduledTask -TaskName "TW_Stock_Daily_Backup" | Select-Object TaskName, State | Format-List
Get-ScheduledTask -TaskName "TW_Stock_Daily_Backup" | Select-Object -ExpandProperty Triggers | Format-Table StartBoundary, DaysOfWeek, WeeksInterval, Enabled -AutoSize
Get-ScheduledTask -TaskName "TW_Stock_Daily_Backup" | Select-Object -ExpandProperty Settings | Format-List ExecutionTimeLimit, StartWhenAvailable, WakeToRun, DisallowStartIfOnBatteries, StopIfGoingOnBatteries, MultipleInstances, Priority

Write-Host ""
Get-ScheduledTask -TaskName "TW_Stock_Weekly_Retrain_Backup" | Select-Object TaskName, State | Format-List
Get-ScheduledTask -TaskName "TW_Stock_Weekly_Retrain_Backup" | Select-Object -ExpandProperty Triggers | Format-Table StartBoundary, DaysOfWeek, WeeksInterval, Enabled -AutoSize
Get-ScheduledTask -TaskName "TW_Stock_Weekly_Retrain_Backup" | Select-Object -ExpandProperty Settings | Format-List ExecutionTimeLimit, StartWhenAvailable, WakeToRun, DisallowStartIfOnBatteries, StopIfGoingOnBatteries, MultipleInstances, Priority

Write-Host ""
Get-ScheduledTask -TaskName "TW_Stock_My_Holdings_Risk_Cut_0630" | Select-Object TaskName, State | Format-List
Get-ScheduledTask -TaskName "TW_Stock_My_Holdings_Risk_Cut_0630" | Select-Object -ExpandProperty Triggers | Format-Table StartBoundary, DaysOfWeek, WeeksInterval, Enabled -AutoSize
Get-ScheduledTask -TaskName "TW_Stock_My_Holdings_Risk_Cut_0630" | Select-Object -ExpandProperty Settings | Format-List ExecutionTimeLimit, StartWhenAvailable, WakeToRun, DisallowStartIfOnBatteries, StopIfGoingOnBatteries, MultipleInstances, Priority

Write-Host ""
Write-Host "TW_Stock_ChipK_Update retired; no desktop ChipK schedule is created."
