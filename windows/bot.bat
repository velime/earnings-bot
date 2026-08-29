@echo off
rem Generic launcher.  Usage:  bot.bat <cmd> [args]
rem   bot.bat once | run | pairs | upcoming | preview CSCO | override JD 05:45 | seed
rem Prefers the built .exe, falls back to the source venv.
setlocal
cd /d "%~dp0"

set "EXE=%~dp0dist\earnings-bot\earnings-bot.exe"
if exist "%EXE%" (
    "%EXE%" %*
    exit /b %errorlevel%
)

set "VENV=%~dp0.venv\Scripts\python.exe"
if exist "%VENV%" (
    set "PYTHONPATH=%~dp0.."
    "%VENV%" -m bot.main %*
    exit /b %errorlevel%
)

echo [!] No build found in this folder.
echo     Run:  powershell -ExecutionPolicy Bypass -File build.ps1
exit /b 1
