param(
    [Parameter(Mandatory = $true)]
    [string]$StartDate,

    [Parameter(Mandatory = $true)]
    [string]$EndDate,

    [string]$OutputPrefix = "predictions_overlay_ctx_q1",
    [ValidateSet("auto", "duckdb", "csv")]
    [string]$SnapshotSource = "csv",
    [int]$ChunkBusinessDays = 5,
    [int]$InnerChunkSizeDays = 5,
    [int]$MaxStocks = 0,
    [int]$MinSourceBytes = 1024,
    [int]$MinOutputBytes = 1024,
    [double]$SleepSeconds = 2.0,
    [string]$ManifestPath = "",
    [switch]$Force,
    [switch]$ContextOnly,
    [switch]$BuildUnifiedSignals,
    [string]$UnifiedOutputPrefix = "unified_signals_overlay_q1",
    [int]$UnifiedMinOutputBytes = 1024,
    [switch]$ForceUnified,
    [switch]$DisableT1,
    [switch]$UnifiedPenaltyOverlay,
    [ValidateSet("v1", "tuned")]
    [string]$PenaltyOverlayVersion = "v1",
    [switch]$FailFast
)

$ErrorActionPreference = "Stop"

function Get-BusinessDates {
    param(
        [datetime]$From,
        [datetime]$To
    )

    $dates = New-Object System.Collections.Generic.List[string]
    $cursor = $From.Date
    while ($cursor -le $To.Date) {
        if ($cursor.DayOfWeek -ne [System.DayOfWeek]::Saturday -and
            $cursor.DayOfWeek -ne [System.DayOfWeek]::Sunday) {
            $dates.Add($cursor.ToString("yyyy-MM-dd"))
        }
        $cursor = $cursor.AddDays(1)
    }
    return $dates
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "backfill_alpha_predictions.py"
$start = [datetime]::ParseExact($StartDate, "yyyy-MM-dd", $null)
$end = [datetime]::ParseExact($EndDate, "yyyy-MM-dd", $null)
$dates = @(Get-BusinessDates -From $start -To $end)

if ($dates.Count -eq 0) {
    throw "No business dates in requested range $StartDate to $EndDate"
}
if ($ChunkBusinessDays -le 0) {
    $ChunkBusinessDays = $dates.Count
}
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $repoRoot "ml\reports\backfill_alpha_predictions_${StartDate}_${EndDate}.jsonl"
}

$chunkNumber = 0
for ($idx = 0; $idx -lt $dates.Count; $idx += $ChunkBusinessDays) {
    $chunkNumber += 1
    $endIdx = [Math]::Min($idx + $ChunkBusinessDays - 1, $dates.Count - 1)
    $chunkDates = @($dates[$idx..$endIdx])
    $chunkStart = $chunkDates[0]
    $chunkEnd = $chunkDates[$chunkDates.Count - 1]

    Write-Host "[alpha-backfill-batch] chunk $chunkNumber $chunkStart to $chunkEnd"

    $argsList = @(
        $scriptPath,
        "--start-date", $chunkStart,
        "--end-date", $chunkEnd,
        "--output-prefix", $OutputPrefix,
        "--snapshot-source", $SnapshotSource,
        "--chunk-size-days", $InnerChunkSizeDays,
        "--min-source-bytes", $MinSourceBytes,
        "--min-output-bytes", $MinOutputBytes,
        "--manifest-path", $ManifestPath
    )

    if ($chunkNumber -gt 1) {
        $argsList += "--append-manifest"
    }
    if ($MaxStocks -gt 0) {
        $argsList += @("--max-stocks", $MaxStocks)
    }
    if ($Force) {
        $argsList += "--force"
    }
    if ($ContextOnly) {
        $argsList += "--context-only"
    }
    if ($FailFast) {
        $argsList += "--fail-fast"
    }
    if ($BuildUnifiedSignals) {
        $argsList += @(
            "--build-unified-signals",
            "--unified-output-prefix", $UnifiedOutputPrefix,
            "--unified-min-output-bytes", $UnifiedMinOutputBytes,
            "--penalty-overlay-version", $PenaltyOverlayVersion
        )
        if ($ForceUnified) {
            $argsList += "--force-unified"
        }
        if ($DisableT1) {
            $argsList += "--disable-t1"
        }
        if ($UnifiedPenaltyOverlay) {
            $argsList += "--unified-penalty-overlay"
        }
    }

    Push-Location $repoRoot
    try {
        & python @argsList
        if ($LASTEXITCODE -ne 0) {
            throw "backfill_alpha_predictions.py failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    if ($SleepSeconds -gt 0 -and $endIdx -lt ($dates.Count - 1)) {
        Start-Sleep -Seconds $SleepSeconds
    }
}

Write-Host "[alpha-backfill-batch] completed $($dates.Count) business dates"
