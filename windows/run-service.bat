@echo off
rem Launched by the Scheduled Task. No console prompts.
rem Rolls the log at launch when it grows past ~5 MB (keeps one .1 backup).
cd /d "%~dp0"
if not exist logs mkdir logs

set "LOG=%~dp0logs\earnings-bot.log"
if exist "%LOG%" for %%A in ("%LOG%") do if %%~zA GTR 5242880 (
    if exist "%LOG%.1" del "%LOG%.1"
    move /y "%LOG%" "%LOG%.1" >nul
)

call "%~dp0bot.bat" run >> "%LOG%" 2>&1
