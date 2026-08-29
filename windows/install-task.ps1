<#
    Register the bot as a Windows Scheduled Task that auto-restarts.

      .\install-task.ps1              -> start at logon (no admin needed)
      .\install-task.ps1 -AtStartup   -> also start at boot (needs admin)

    Logs: windows\logs\earnings-bot.log   (Task history also records restarts)
#>
param(
    [switch]$AtStartup,
    [string]$TaskName = 'EarningsBot'
)

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$script = Join-Path $Here 'run-service.bat'

if (-not (Test-Path $script)) { throw "run-service.bat not found next to this script" }

$action = New-ScheduledTaskAction -Execute $script -WorkingDirectory $Here

$triggers = @(New-ScheduledTaskTrigger -AtLogOn)
if ($AtStartup) { $triggers += New-ScheduledTaskTrigger -AtStartup }

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Description 'MEXC stock-earnings Telegram alert bot' -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Task '$TaskName' registered and started."
Write-Host "  status : Get-ScheduledTask $TaskName | Get-ScheduledTaskInfo"
Write-Host "  logs   : Get-Content -Wait `"$Here\logs\earnings-bot.log`""
Write-Host "  remove : .\uninstall-task.ps1"
