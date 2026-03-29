# Export current task XML, modify settings, re-create
$xml = [xml](Export-ScheduledTask -TaskName 'TW_Stock_Daily')
$ns = $xml.Task.NamespaceURI
$settings = $xml.Task.Settings

# Set StartWhenAvailable
$node = $settings.SelectSingleNode('*[local-name()="StartWhenAvailable"]')
if ($node) { $node.InnerText = 'true' }
else {
    $elem = $xml.CreateElement('StartWhenAvailable', $ns)
    $elem.InnerText = 'true'
    $settings.AppendChild($elem) | Out-Null
}

# Set WakeToRun
$node = $settings.SelectSingleNode('*[local-name()="WakeToRun"]')
if ($node) { $node.InnerText = 'true' }
else {
    $elem = $xml.CreateElement('WakeToRun', $ns)
    $elem.InnerText = 'true'
    $settings.AppendChild($elem) | Out-Null
}

# Save XML and re-create via schtasks (will prompt for password)
$xmlPath = "$env:TEMP\tw_stock_daily.xml"
$xml.Save($xmlPath)
Write-Host "Saved modified XML to $xmlPath"
Write-Host "Re-creating task with schtasks /create /F ..."
schtasks /create /tn "TW_Stock_Daily" /XML $xmlPath /F /RU cheng

# Verify
$s = (Get-ScheduledTask -TaskName 'TW_Stock_Daily').Settings
Write-Host "  StartWhenAvailable: $($s.StartWhenAvailable)"
Write-Host "  WakeToRun: $($s.WakeToRun)"
