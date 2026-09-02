@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%uninstall_ai_strategy_codex_proxy_startup.ps1"
pause
