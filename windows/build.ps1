<#
    Windows build for earnings-bot.

    Two modes:
      .\build.ps1            -> venv + standalone .exe  (PyInstaller, no Python on target)
      .\build.ps1 -NoExe     -> venv only, run from source
      .\build.ps1 -Clean     -> wipe .venv / build / dist first

    Everything the bot needs at runtime lives in this  windows\  folder:
      .env  data\  logs\  seed_times.json   (and dist\earnings-bot\ for the exe)
#>
param(
    [switch]$NoExe,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$Root = Split-Path $Here -Parent
Set-Location $Here

if ($Clean) {
    'dist', 'build', '.venv' | ForEach-Object {
        if (Test-Path "$Here\$_") { Remove-Item -Recurse -Force "$Here\$_" }
    }
}

Write-Host "==> venv + dependencies"
$py = "$Here\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv "$Here\.venv" }
& $py -m pip install --upgrade pip --quiet
& $py -m pip install --quiet -r "$Root\requirements.txt"

Write-Host "==> runtime files in $Here"
if (-not (Test-Path "$Here\.env")) {
    Copy-Item "$Here\.env.example" "$Here\.env"
    Write-Host "    created windows\.env  - fill in TELEGRAM_TOKEN / TELEGRAM_CHAT_ID"
}
Copy-Item "$Root\seed_times.json" "$Here\seed_times.json" -Force
New-Item -ItemType Directory -Force "$Here\data", "$Here\logs" | Out-Null

if ($NoExe) {
    Write-Host ""
    Write-Host "Source build ready."
    Write-Host "  .\bot.bat once           # one sync + send pass"
    Write-Host "  .\bot.bat seed           # load seed_times.json"
    Write-Host "  .\run.bat                # run the daemon (foreground)"
    exit 0
}

Write-Host "==> PyInstaller"
& $py -m pip install --quiet pyinstaller
& $py -m PyInstaller --noconfirm --clean "$Here\earnings-bot.spec"

Write-Host ""
Write-Host "Build complete:  $Here\dist\earnings-bot\earnings-bot.exe"
Write-Host "  .\bot.bat once"
Write-Host "  .\run.bat"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\install-task.ps1   # autostart"
