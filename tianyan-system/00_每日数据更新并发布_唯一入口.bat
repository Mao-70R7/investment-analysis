@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

set "WORKSPACE_ROOT=%~dp0"
if "%WORKSPACE_ROOT:~-1%"=="\" set "WORKSPACE_ROOT=%WORKSPACE_ROOT:~0,-1%"
set "CODE_ROOT=%WORKSPACE_ROOT%"
set "FRAMEWORK_REL=节点脚本\00_调度框架\启动.ps1"
if not exist "%CODE_ROOT%\%FRAMEWORK_REL%" if exist "%WORKSPACE_ROOT%\程序代码\%FRAMEWORK_REL%" (
    set "CODE_ROOT=%WORKSPACE_ROOT%\程序代码"
)

set "LAUNCHER=%CODE_ROOT%\%FRAMEWORK_REL%"
if not exist "%LAUNCHER%" (
    echo [失败] 找不到节点调度入口：%LAUNCHER%
    echo [提示] 请先确认程序代码已完整同步。
    exit /b 2
)

set "MODE=daily"
set "MODE_ARGS="
set "RESUME_FROM_NODE="
set "RESUME_TO_NODE="
set "NODE_ID="
set "NODE_RUN_ID="
set "NODE_STANDALONE="
if "%~1"=="" goto run
if /I "%~1"=="initialize" goto mode_initialize
if /I "%~1"=="check" goto mode_check
if /I "%~1"=="resume" goto mode_resume
if /I "%~1"=="node" goto mode_node
goto usage

:mode_initialize
set "MODE=initialize"
goto run

:mode_check
set "MODE=check"
goto run

:mode_resume
set "MODE=resume"
set "MODE_ARGS=%~2"
if /I "%~3"=="--from-node" (
    if "%~4"=="" (
        echo [失败] --from-node 缺少节点ID。
        exit /b 2
    )
    set "RESUME_FROM_NODE=%~4"
)
if /I "%~5"=="--to-node" (
    if "%~6"=="" (
        echo [失败] --to-node 缺少节点ID。
        exit /b 2
    )
    set "RESUME_TO_NODE=%~6"
)
goto run

:mode_node
set "MODE=node"
set "NODE_ID=%~2"
if "%NODE_ID%"=="" goto missing_node_id
shift
shift

:parse_node_argument
if "%~1"=="" goto run
if /I "%~1"=="--run-id" (
    if "%~2"=="" (
        echo [失败] --run-id 缺少 run_id。
        exit /b 2
    )
    set "NODE_RUN_ID=%~2"
    shift
    shift
    goto parse_node_argument
)
if /I "%~1"=="--standalone" (
    set "NODE_STANDALONE=1"
    shift
    goto parse_node_argument
)
echo [失败] node 模式不支持参数：%~1
exit /b 2

:usage
echo [失败] 不支持的运行模式：%~1
echo 用法：
echo   %~nx0
echo   %~nx0 initialize
echo   %~nx0 check
echo   %~nx0 resume ^<run_id^>
echo   %~nx0 resume ^<run_id^> --from-node ^<node_id^>
echo   %~nx0 resume ^<run_id^> --from-node ^<node_id^> --to-node ^<node_id^>
echo   %~nx0 node ^<node_id^> --run-id ^<run_id^>
echo   %~nx0 node ^<node_id^> --standalone
exit /b 2

:run
echo ==============================================================================
echo 天眼系统节点化调度入口
echo 工作区：%WORKSPACE_ROOT%
echo 程序目录：%CODE_ROOT%
echo 运行模式：%MODE%
echo 开始时间：%date% %time%
echo ==============================================================================

if /I "%MODE%"=="node" goto run_node
if not "%MODE_ARGS%"=="" goto run_with_args
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%"
goto finish

:run_with_args
if defined RESUME_FROM_NODE goto run_resume_from
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%"
goto finish

:run_resume_from
if defined RESUME_TO_NODE goto run_resume_bounded
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%" -ResumeFromNode "%RESUME_FROM_NODE%"
goto finish

:run_resume_bounded
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%" -ResumeFromNode "%RESUME_FROM_NODE%" -ResumeToNode "%RESUME_TO_NODE%"
goto finish

:run_node
if "%NODE_ID%"=="" goto missing_node_id
if defined NODE_RUN_ID if defined NODE_STANDALONE goto run_node_resume_standalone
if defined NODE_STANDALONE goto run_node_standalone
if defined NODE_RUN_ID goto run_node_resume
goto missing_node_context

:run_node_resume_standalone
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -NodeRunId "%NODE_RUN_ID%" -Standalone
goto finish

:run_node_standalone
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -Standalone
goto finish

:run_node_resume
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -NodeRunId "%NODE_RUN_ID%"
goto finish

:missing_node_id
echo [失败] node 模式缺少节点ID。
exit /b 2

:missing_node_context
echo [失败] node 模式必须提供 --run-id ^<run_id^> 或 --standalone。
exit /b 2

:finish
set "EXIT_CODE=%ERRORLEVEL%"

echo ==============================================================================
echo 结束时间：%date% %time%
echo 退出码：%EXIT_CODE%
echo ==============================================================================
exit /b %EXIT_CODE%
