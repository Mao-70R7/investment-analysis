@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "CODE_ROOT=%~dp0..\.."
for %%I in ("%CODE_ROOT%") do set "CODE_ROOT=%%~fI"
set "WORKSPACE_ROOT=%CODE_ROOT%"
if exist "%CODE_ROOT%\..\本机配置\runtime.local.json" (
    for %%I in ("%CODE_ROOT%\..") do set "WORKSPACE_ROOT=%%~fI"
)
set "ENTRY=%WORKSPACE_ROOT%\00_每日数据更新并发布_唯一入口.bat"
if not exist "%ENTRY%" (
    echo [失败] 找不到正式入口：%ENTRY%
    exit /b 2
)

set "MODE=%~1"
if "%MODE%"=="" set "MODE=daily"
echo [废弃入口] 当前脚本只保留一轮兼容，请改用：%ENTRY%
if /I "%MODE%"=="daily" (
    call "%ENTRY%"
) else if /I "%MODE%"=="migration" (
    call "%ENTRY%" node migration_package
) else if /I "%MODE%"=="ttfund" (
    call "%ENTRY%" node ttfund_incremental
) else (
    echo [失败] 不支持的兼容模式：%MODE%
    exit /b 2
)
exit /b %ERRORLEVEL%
