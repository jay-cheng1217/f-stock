$task = Get-ScheduledTask -TaskName 'TW_Stock_Daily'
$task.Settings.StartWhenAvailable = $true
$task.Settings.WakeToRun = $true
Set-ScheduledTask -InputObject $task
Write-Host "TW_Stock_Daily updated: StartWhenAvailable=True, WakeToRun=True"

$task2 = Get-ScheduledTask -TaskName 'TW_Stock_Weekly_Retrain'
$task2.Settings.StartWhenAvailable = $true
$task2.Settings.WakeToRun = $true
Set-ScheduledTask -InputObject $task2
Write-Host "TW_Stock_Weekly_Retrain updated: StartWhenAvailable=True, WakeToRun=True"
