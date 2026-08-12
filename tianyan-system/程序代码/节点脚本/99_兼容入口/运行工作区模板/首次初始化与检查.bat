@echo off
setlocal EnableExtensions
set "LAUNCHER=%~dp0runtime_launcher.ps1"
if not exist "%LAUNCHER%" (
    echo [ERROR] Missing runtime launcher: "%LAUNCHER%"
    exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Mode initialize
set "EXIT_CODE=%ERRORLEVEL%"
echo Exit code: %EXIT_CODE%
exit /b %EXIT_CODE%
