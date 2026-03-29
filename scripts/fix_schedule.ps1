# Fix TW_Stock_Daily via schtasks (avoids credential issue)
schtasks /change /tn "TW_Stock_Daily" /ST 05:00
# Enable StartWhenAvailable via XML export/import
$xml = [xml](Export-ScheduledTask -TaskName 'TW_Stock_Daily')
$ns = $xml.Task.NamespaceURI
$settings = $xml.Task.Settings
if (-not $settings.StartWhenAvailable) {
    $elem = $xml.CreateElement('StartWhenAvailable', $ns)
    $elem.InnerText = 'true'
    $settings.AppendChild($elem) | Out-Null
} else {
    $settings.StartWhenAvailable = 'true'
}
if (-not $settings.WakeToRun) {
    $elem = $xml.CreateElement('WakeToRun', $ns)
    $elem.InnerText = 'true'
    $settings.AppendChild($elem) | Out-Null
} else {
    $settings.WakeToRun = 'true'
}
Register-ScheduledTask -TaskName 'TW_Stock_Daily' -Xml $xml.OuterXml -Force
Write-Host "TW_Stock_Daily updated via XML"

# Verify
$s = (Get-ScheduledTask -TaskName 'TW_Stock_Daily').Settings
Write-Host "  StartWhenAvailable: $($s.StartWhenAvailable)"
Write-Host "  WakeToRun: $($s.WakeToRun)"
