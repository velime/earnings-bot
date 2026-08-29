param([string]$TaskName = 'EarningsBot')
$ErrorActionPreference = 'Stop'
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed."
} catch {
    Write-Host "Task '$TaskName' not found."
}
