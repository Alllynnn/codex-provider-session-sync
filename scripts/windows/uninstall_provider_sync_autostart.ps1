param(
    [string]$TaskName = "CodexProviderSessionSync"
)

$ErrorActionPreference = "Stop"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Uninstalled scheduled task: $TaskName"
}

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $RunKey -Name $TaskName -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $RunKey -Name $TaskName
    Write-Host "Removed HKCU Run startup: $TaskName"
}

$Processes = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*provider_sync_daemon.py*" -or
    $_.Name -eq "ProviderSyncDaemon.exe" -or
    $_.CommandLine -like "*ProviderSyncDaemon.exe*"
}
foreach ($Process in $Processes) {
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
}
