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
echo [程序更新] 默认只更新程序代码；如新版本声明数据库迁移，将先校验成功备份并事务执行。
"%PYTHON_EXE%" -X utf8 "%RUNTIME_CLI%" --workspace-root "%WORKSPACE_ROOT%" update-code %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
