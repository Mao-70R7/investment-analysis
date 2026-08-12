@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "WORKSPACE_ROOT=%~dp0"
set "PYTHON_EXE=%WORKSPACE_ROOT%运行环境\python\Scripts\python.exe"
set "RUNTIME_CLI=%WORKSPACE_ROOT%程序代码\scripts\runtime_workspace_cli.py"
cd /d "%WORKSPACE_ROOT%"

if not exist "%PYTHON_EXE%" exit /b 2
set "PATH=%WORKSPACE_ROOT%运行环境\python\Scripts;%PATH%"
echo [程序回退] 将恢复上一个已验证程序提交；数据库版本不兼容时会拒绝回退。
"%PYTHON_EXE%" -X utf8 "%RUNTIME_CLI%" --workspace-root "%WORKSPACE_ROOT%" rollback-code %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
