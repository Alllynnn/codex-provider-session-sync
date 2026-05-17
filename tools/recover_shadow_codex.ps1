param(
    [string]$ThreadId = "5cd4a512-ec0f-556e-949e-ba6bf2ee2b81",
    [string]$RolloutName = "rollout-2026-05-12T22-40-18-5cd4a512-ec0f-556e-949e-ba6bf2ee2b81.jsonl"
)

$ErrorActionPreference = "Continue"

$codexHome = Join-Path $env:USERPROFILE ".codex"
$relativeRollout = "Users\Administrator\.codex\sessions\2026\05\12\$RolloutName"
$currentRollout = Join-Path $codexHome "sessions\2026\05\12\$RolloutName"
$desktop = [Environment]::GetFolderPath("Desktop")
$outDir = Join-Path $desktop "codex-shadow-recovery-$ThreadId"
$reportPath = Join-Path $outDir "shadow-recovery-report.txt"

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Write-Report {
    param([string]$Message)
    $Message | Tee-Object -FilePath $reportPath -Append
}

function Get-JsonlSummary {
    param([string]$Path)

    $summary = [ordered]@{
        Path = $Path
        Exists = Test-Path -LiteralPath $Path
        Size = $null
        Lines = $null
        FirstTimestamp = $null
        LastTimestamp = $null
        HasMidnightTurn = $false
        HasRecoveredAnswer = $false
    }

    if (-not $summary.Exists) {
        return [pscustomobject]$summary
    }

    $item = Get-Item -LiteralPath $Path
    $summary.Size = $item.Length

    $lineCount = 0
    $firstTs = $null
    $lastTs = $null
    $hasMidnightTurn = $false
    $hasRecoveredAnswer = $false

    Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
        $lineCount += 1
        if ($_ -like "*019e222c-a4a4-71f3-9c7d-3a4d8d54a0c0*") {
            $hasMidnightTurn = $true
        }
        if ($_ -like "*测完了，用的是同一个模型*") {
            $hasRecoveredAnswer = $true
        }
        try {
            $obj = $_ | ConvertFrom-Json -ErrorAction Stop
            if ($obj.timestamp) {
                if (-not $firstTs) { $firstTs = $obj.timestamp }
                $lastTs = $obj.timestamp
            }
        } catch {
        }
    }

    $summary.Lines = $lineCount
    $summary.FirstTimestamp = $firstTs
    $summary.LastTimestamp = $lastTs
    $summary.HasMidnightTurn = $hasMidnightTurn
    $summary.HasRecoveredAnswer = $hasRecoveredAnswer
    return [pscustomobject]$summary
}

Remove-Item -LiteralPath $reportPath -Force -ErrorAction SilentlyContinue
Write-Report "Codex shadow recovery report"
Write-Report "Generated: $(Get-Date -Format o)"
Write-Report "Thread: $ThreadId"
Write-Report "Current rollout: $currentRollout"
Write-Report "Output dir: $outDir"
Write-Report ""

Write-Report "## Current File"
Get-JsonlSummary -Path $currentRollout | Format-List | Out-String | Tee-Object -FilePath $reportPath -Append

Write-Report "## Restore Points"
try {
    Get-ComputerRestorePoint |
        Sort-Object CreationTime -Descending |
        Select-Object -First 20 SequenceNumber, Description, CreationTime, RestorePointType, EventType |
        Format-Table -AutoSize |
        Out-String |
        Tee-Object -FilePath $reportPath -Append
} catch {
    Write-Report "Get-ComputerRestorePoint failed: $($_.Exception.Message)"
}

Write-Report "## Windows Backup Versions"
try {
    wbadmin get versions 2>&1 | Tee-Object -FilePath $reportPath -Append
} catch {
    Write-Report "wbadmin get versions failed: $($_.Exception.Message)"
}

Write-Report "## Shadow Copies"
$shadows = @()
try {
    $shadows = Get-CimInstance -ClassName Win32_ShadowCopy |
        Sort-Object InstallDate -Descending
} catch {
    Write-Report "Get-CimInstance Win32_ShadowCopy failed: $($_.Exception.Message)"
}

if (-not $shadows -or $shadows.Count -eq 0) {
    Write-Report "No shadow copies returned by Win32_ShadowCopy."
    Write-Report "Raw vssadmin output:"
    try {
        vssadmin list shadows 2>&1 | Tee-Object -FilePath $reportPath -Append
    } catch {
        Write-Report "vssadmin list shadows failed: $($_.Exception.Message)"
    }
    exit 0
}

$copies = @()
$index = 0
foreach ($shadow in $shadows) {
    $index += 1
    Write-Report ""
    Write-Report "### Shadow #$index"
    Write-Report "ID: $($shadow.ID)"
    Write-Report "InstallDate: $($shadow.InstallDate)"
    Write-Report "VolumeName: $($shadow.VolumeName)"
    Write-Report "DeviceObject: $($shadow.DeviceObject)"
    Write-Report "State: $($shadow.State)"
    Write-Report "ClientAccessible: $($shadow.ClientAccessible)"

    $source = "$($shadow.DeviceObject)\$relativeRollout"
    Write-Report "Candidate path: $source"

    if (Test-Path -LiteralPath $source) {
        $dest = Join-Path $outDir ("shadow-{0:000}-{1}" -f $index, $RolloutName)
        try {
            Copy-Item -LiteralPath $source -Destination $dest -Force
            Write-Report "Copied to: $dest"
            $summary = Get-JsonlSummary -Path $dest
            $copies += $summary
            $summary | Format-List | Out-String | Tee-Object -FilePath $reportPath -Append
        } catch {
            Write-Report "Copy failed: $($_.Exception.Message)"
        }
    } else {
        Write-Report "Rollout file not found in this shadow."
    }
}

Write-Report ""
Write-Report "## Copied Candidates"
if ($copies.Count -eq 0) {
    Write-Report "No historical rollout copies were found in shadow copies."
} else {
    $copies |
        Sort-Object Size -Descending |
        Format-Table Size, Lines, LastTimestamp, HasMidnightTurn, HasRecoveredAnswer, Path -AutoSize |
        Out-String |
        Tee-Object -FilePath $reportPath -Append
}

Write-Report ""
Write-Report "Done."
