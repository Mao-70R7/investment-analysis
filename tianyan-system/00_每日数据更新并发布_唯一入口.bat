@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "KEEP_WINDOW_OPEN=1"
if /I "%ADVISOR_KEEP_WINDOW_OPEN%"=="0" set "KEEP_WINDOW_OPEN=0"

set "WORKSPACE_ROOT=%~dp0"
if "%WORKSPACE_ROOT:~-1%"=="\" set "WORKSPACE_ROOT=%WORKSPACE_ROOT:~0,-1%"
set "LAUNCHER="
call :find_launcher
if not defined LAUNCHER (
    echo [ERROR] daily_update_launcher.ps1 was not found under this workspace.
    set "EXIT_CODE=2"
    goto finish
)

set "MODE=interactive"
set "MODE_ARGS="
set "RESUME_FROM_NODE="
set "RESUME_TO_NODE="
set "NODE_ID="
set "NODE_RUN_ID="
set "NODE_STANDALONE="
set "DRY_RUN_SWITCH="

if "%~1"=="" goto run
if /I "%~1"=="-DryRun" goto mode_dry_run
if /I "%~1"=="continue" goto mode_continue
if /I "%~1"=="restart" goto mode_daily
if /I "%~1"=="daily" goto mode_daily
if /I "%~1"=="initialize" goto mode_initialize
if /I "%~1"=="check" goto mode_check
if /I "%~1"=="resume" goto mode_resume
if /I "%~1"=="node" goto mode_node
goto usage

:mode_dry_run
set "MODE=daily"
set "DRY_RUN_SWITCH=-DryRun"
goto run

:mode_continue
set "MODE=resumeLatest"
goto run

:mode_daily
set "MODE=daily"
goto run

:mode_initialize
set "MODE=initialize"
goto run

:mode_check
set "MODE=check"
goto run

:mode_resume
if "%~2"=="" (
    echo [ERROR] resume requires a run_id.
    set "EXIT_CODE=2"
    goto finish
)
set "MODE=resume"
set "MODE_ARGS=%~2"
if /I "%~3"=="--from-node" (
    if "%~4"=="" (
        echo [ERROR] --from-node requires a node_id.
        set "EXIT_CODE=2"
        goto finish
    )
    set "RESUME_FROM_NODE=%~4"
)
if /I "%~5"=="--to-node" (
    if "%~6"=="" (
        echo [ERROR] --to-node requires a node_id.
        set "EXIT_CODE=2"
        goto finish
    )
    set "RESUME_TO_NODE=%~6"
)
goto run

:mode_node
if "%~2"=="" (
    echo [ERROR] node requires a node_id.
    set "EXIT_CODE=2"
    goto finish
)
set "MODE=node"
set "NODE_ID=%~2"
shift
shift

:parse_node_argument
if "%~1"=="" goto run
if /I "%~1"=="--run-id" (
    if "%~2"=="" (
        echo [ERROR] --run-id requires a run_id.
        set "EXIT_CODE=2"
        goto finish
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
echo [ERROR] unsupported node argument: %~1
set "EXIT_CODE=2"
goto finish

:usage
echo [ERROR] unsupported mode: %~1
echo Usage:
echo   %~nx0
echo   %~nx0 continue
echo   %~nx0 restart
echo   %~nx0 -DryRun
echo   %~nx0 initialize
echo   %~nx0 check
echo   %~nx0 resume ^<run_id^>
echo   %~nx0 resume ^<run_id^> --from-node ^<node_id^>
echo   %~nx0 resume ^<run_id^> --from-node ^<node_id^> --to-node ^<node_id^>
echo   %~nx0 node ^<node_id^> --run-id ^<run_id^>
echo   %~nx0 node ^<node_id^> --standalone
set "EXIT_CODE=2"
goto finish

:run
echo ==============================================================================
echo Tianyan daily update launcher
echo Workspace: "%WORKSPACE_ROOT%"
echo Mode: %MODE%
echo Started: %date% %time%
echo ==============================================================================
call :invoke_launcher
set "EXIT_CODE=%ERRORLEVEL%"

:finish
if not defined EXIT_CODE set "EXIT_CODE=2"
echo ==============================================================================
echo Finished: %date% %time%
echo Exit code: %EXIT_CODE%
echo ==============================================================================
if "%KEEP_WINDOW_OPEN%"=="1" (
    echo The task has ended. Review the final result and log path above.
    echo Press any key to close. Automation may set ADVISOR_KEEP_WINDOW_OPEN=0.
    pause >nul
)
exit /b %EXIT_CODE%

:invoke_launcher
if /I "%MODE%"=="node" goto invoke_node
if not "%MODE_ARGS%"=="" goto invoke_with_args
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_with_args
if defined RESUME_FROM_NODE goto invoke_resume_from
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%" %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_resume_from
if defined RESUME_TO_NODE goto invoke_resume_bounded
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%" -ResumeFromNode "%RESUME_FROM_NODE%" %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_resume_bounded
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode "%MODE%" -ModeArguments "%MODE_ARGS%" -ResumeFromNode "%RESUME_FROM_NODE%" -ResumeToNode "%RESUME_TO_NODE%" %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_node
if defined NODE_RUN_ID if defined NODE_STANDALONE goto invoke_node_resume_standalone
if defined NODE_STANDALONE goto invoke_node_standalone
if defined NODE_RUN_ID goto invoke_node_resume
echo [ERROR] node requires --run-id or --standalone.
exit /b 2

:invoke_node_resume_standalone
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -NodeRunId "%NODE_RUN_ID%" -Standalone %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_node_standalone
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -Standalone %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:invoke_node_resume
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -WorkspaceRoot "%WORKSPACE_ROOT%" -Mode node -NodeId "%NODE_ID%" -NodeRunId "%NODE_RUN_ID%" %DRY_RUN_SWITCH%
exit /b %ERRORLEVEL%

:find_launcher
for /d %%A in ("%WORKSPACE_ROOT%\*") do (
    if exist "%%~fA\daily_update_launcher.ps1" set "LAUNCHER=%%~fA\daily_update_launcher.ps1"
    for /d %%B in ("%%~fA\*") do (
        if exist "%%~fB\daily_update_launcher.ps1" set "LAUNCHER=%%~fB\daily_update_launcher.ps1"
        for /d %%C in ("%%~fB\00_*") do (
            if exist "%%~fC\daily_update_launcher.ps1" set "LAUNCHER=%%~fC\daily_update_launcher.ps1"
        )
    )
)
exit /b 0
