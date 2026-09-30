param(
    [int]$Port = 8765,
    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BridgeDir = Join-Path $RepoRoot "tmp\chipk_admin_bridge"
$TokenPath = Join-Path $BridgeDir "token.txt"
New-Item -ItemType Directory -Force -Path $BridgeDir | Out-Null

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
Set-Content -LiteralPath $TokenPath -Value $token -Encoding UTF8

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host ("ChipK admin bridge starting. Admin={0} Host={1} Port={2}" -f $isAdmin, $HostName, $Port)
Write-Host ("Token file: {0}" -f $TokenPath)
Write-Host "Keep this window open while Codex controls ChipK."

Set-Location $RepoRoot
python (Join-Path $PSScriptRoot "chipk_admin_bridge.py") --host $HostName --port $Port --token-file $TokenPath
