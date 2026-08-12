@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "WORKSPACE_ROOT=%~dp0"
set "INSTALLER=%WORKSPACE_ROOT%程序代码\scripts\install_runtime_environment.ps1"
cd /d "%WORKSPACE_ROOT%"

if not exist "%INSTALLER%" (
    echo [失败] 环境安装脚本不存在: %INSTALLER%
    pause
    exit /b 2
)
echo [环境] 将检查 Python 3.12、Git、Node.js，并在工作区内创建独立 Python 环境。
echo [环境] 缺失的基础程序优先通过 winget 自动安装。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -WorkspaceRoot "%WORKSPACE_ROOT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
