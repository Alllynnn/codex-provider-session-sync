param(
    [string]$TaskName = "CodexProviderSessionSync",
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "../..")
$DaemonScript = Join-Path $RepoRoot "src/provider_sync_daemon.py"
$DaemonExe = Join-Path $RepoRoot "dist/ProviderSyncDaemon.exe"
$CodexHome = Join-Path $env:USERPROFILE ".codex"
$BackupDir = Join-Path $env:USERPROFILE "Desktop/codex-session-sync-backup-v2"
$LogFile = Join-Path $CodexHome "log/provider-sync-daemon.log"

if ((-not (Test-Path -LiteralPath $DaemonExe)) -and (-not (Test-Path -LiteralPath $DaemonScript))) {
    throw "Daemon script not found: $DaemonScript"
}

$Python = (Get-Command "pythonw.exe" -ErrorAction SilentlyContinue)
if ($null -eq $Python) {
    $Python = Get-Command "python.exe" -ErrorAction Stop
}

$DaemonArgs = @(
    "--codex-home", "`"$CodexHome`"",
    "--backup-dir", "`"$BackupDir`"",
    "--log-file", "`"$LogFile`"",
    "--interval-seconds", "$IntervalSeconds",
    "--provider", "openai",
    "--provider", "openrouter",
    "--provider", "custom"
) -join " "

if (Test-Path -LiteralPath $DaemonExe) {
    $RunTarget = $DaemonExe
    $ArgumentList = $DaemonArgs
    $CommandLine = "`"$RunTarget`" $ArgumentList"
} else {
    $RunTarget = $Python.Source
    $ArgumentList = "`"$DaemonScript`" $DaemonArgs"
    $CommandLine = "`"$RunTarget`" $ArgumentList"
}

try {
    $Action = New-ScheduledTaskAction -Execute $RunTarget -Argument $ArgumentList -WorkingDirectory $RepoRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 7) `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Periodically sync Codex Desktop sessions across model providers." `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Installed and started scheduled task: $TaskName"
} catch {
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    New-Item -Path $RunKey -Force | Out-Null
    Set-ItemProperty -Path $RunKey -Name $TaskName -Value $CommandLine
    Start-Process -FilePath $RunTarget -ArgumentList $ArgumentList -WorkingDirectory $RepoRoot -WindowStyle Hidden
    Write-Host "Scheduled task failed, installed HKCU Run startup instead: $TaskName"
}

Write-Host "Command: $CommandLine"
Write-Host "Log file: $LogFile"
