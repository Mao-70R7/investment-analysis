@echo off
setlocal EnableExtensions EnableDelayedExpansion

chcp 65001 >nul

set "REPORT_ROOT=%~dp0"
if "%REPORT_ROOT:~-1%"=="\" set "REPORT_ROOT=%REPORT_ROOT:~0,-1%"
cd /d "%REPORT_ROOT%"

set "LOCAL_CONFIG=%REPORT_ROOT%\advisor_update.local.bat"
if exist "%LOCAL_CONFIG%" (
    call "%LOCAL_CONFIG%"
) else (
    echo [WARN] Local config not found: %LOCAL_CONFIG%
    echo [WARN] Copy advisor_update.local.example.bat to advisor_update.local.bat and update machine-specific paths if this is a new PC.
)

set "PYTHON_EXE=%ADVISOR_PYTHON_EXE%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%GFFUNDS_PYTHON_EXE%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=python"

set "NODE_EXE=%ADVISOR_NODE_EXE%"
if "%NODE_EXE%"=="" set "NODE_EXE=%GFFUNDS_NODE_EXE%"
if "%NODE_EXE%"=="" set "NODE_EXE=node"

set "MONITOR_ROOT=%GFFUNDS_MONITOR_ROOT%"
if "%MONITOR_ROOT%"=="" set "MONITOR_ROOT=D:\SyncthingShareToMpc\全市场投顾监控"

set "PROD_SITE_DIR=%GFFUNDS_DEPLOY_SITE_DIR%"
if "%PROD_SITE_DIR%"=="" set "PROD_SITE_DIR=%MONITOR_ROOT%\site"

set "DEVICE_ID=%GFFUNDS_DEVICE_ID%"
if "%DEVICE_ID%"=="" set "DEVICE_ID=b27b7c93"

set "ALGORITHM_VERSION=%GFFUNDS_ALGORITHM_VERSION%"
if "%ALGORITHM_VERSION%"=="" set "ALGORITHM_VERSION=standard_rebalance_asset_dual_nav_v10_all_channels_20260528"

set "PERFORMANCE_ARGS=--workers 8"
if not "%GFFUNDS_PERFORMANCE_ARGS%"=="" set "PERFORMANCE_ARGS=%GFFUNDS_PERFORMANCE_ARGS%"

set "RUN_FULL_COLLECT=%GFFUNDS_RUN_FULL_COLLECT%"
if "%RUN_FULL_COLLECT%"=="" set "RUN_FULL_COLLECT=0"

set "RUN_RECONSTRUCT=%GFFUNDS_RECONSTRUCT_STANDARD_NAV%"
if "%RUN_RECONSTRUCT%"=="" set "RUN_RECONSTRUCT=0"

set "COLLECT_ARGS=--apps gffunds --workers 8 --gffunds-skip-fund-nav --gffunds-skip-protocol-pdf"
if not "%GFFUNDS_COLLECT_ARGS%"=="" set "COLLECT_ARGS=%GFFUNDS_COLLECT_ARGS%"

if /I "%~1"=="/?" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage

echo ============================================================
echo [INFO] GFFunds advisor update + report refresh
echo [INFO] Report root : %REPORT_ROOT%
echo [INFO] Monitor root: %MONITOR_ROOT%
echo [INFO] Prod site   : %PROD_SITE_DIR%
echo [INFO] Python      : %PYTHON_EXE%
echo [INFO] Node        : %NODE_EXE%
echo [INFO] NAV args    : %PERFORMANCE_ARGS%
echo [INFO] Full collect: %RUN_FULL_COLLECT%
echo [INFO] Reconstruct : %RUN_RECONSTRUCT%
echo [INFO] Collect args: %COLLECT_ARGS%
echo ============================================================
echo.

if not exist "%MONITOR_ROOT%\scripts\update_gffunds_performance_curves.py" (
    echo [ERROR] Missing performance updater: %MONITOR_ROOT%\scripts\update_gffunds_performance_curves.py
    exit /b 2
)

if not exist "%MONITOR_ROOT%\scripts\collect_official_apps_public.py" (
    echo [ERROR] Missing collector: %MONITOR_ROOT%\scripts\collect_official_apps_public.py
    exit /b 2
)

call :run_performance_curves
if "%ERRORLEVEL%"=="0" goto maybe_full_collect

echo.
echo [WARN] GFFunds performance curve update failed.
echo [WARN] If the upstream endpoint requires app login, complete login in the phone window and retry.
call :open_gffunds_app_for_login
echo.
call :prompt_login_ready
call :run_performance_curves
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] GFFunds performance curve update failed after login retry.
    exit /b 3
)

:maybe_full_collect
if /I not "%RUN_FULL_COLLECT%"=="1" goto postprocess

call :run_collect
if "%ERRORLEVEL%"=="0" goto postprocess

echo.
echo [WARN] GFFunds full public collection failed.
echo [WARN] If the upstream endpoint requires app login, complete login in the phone window and retry.
call :open_gffunds_app_for_login
echo.
call :prompt_login_ready
call :run_collect
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] GFFunds full collection failed after login retry.
    exit /b 4
)

:postprocess
echo.
echo [INFO] Load all normalized channel data into analysis DB...
cd /d "%MONITOR_ROOT%"
"%PYTHON_EXE%" ".\scripts\load_analysis_zh_current_sqlite.py" --keep-existing-db
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

if /I not "%RUN_RECONSTRUCT%"=="1" goto skip_reconstruct

echo.
echo [INFO] Reconstruct all strategy NAV...
"%PYTHON_EXE%" ".\scripts\reconstruct_strategy_nav.py" --algorithm-version "%ALGORITHM_VERSION%" --output-dir ".\outputs\strategy_nav_reconstruction_after_gffunds_latest"
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

:skip_reconstruct

echo.
echo [INFO] Refresh all standard performance rows...
"%PYTHON_EXE%" ".\scripts\govern_performance_data.py" --standard-algorithm-version "%ALGORITHM_VERSION%" --skip-vacuum --output-dir ".\outputs\performance_governance_after_gffunds_latest"
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

echo.
echo [INFO] Export production basic data...
"%PYTHON_EXE%" ".\scripts\export_basic_data_pages.py" --algorithm-version "%ALGORITHM_VERSION%" --site-dir "%PROD_SITE_DIR%\basic_data"
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

echo.
echo [INFO] Sync production data files into current report...
robocopy "%PROD_SITE_DIR%\basic_data\data" "%REPORT_ROOT%\basic_data\data" /MIR /NFL /NDL /NJH /NJS /NP
set "ROBO_EXIT=%ERRORLEVEL%"
if %ROBO_EXIT% GEQ 8 exit /b %ROBO_EXIT%

echo.
echo [INFO] Rebuild current report data packs and page assets...
cd /d "%REPORT_ROOT%"
"%NODE_EXE%" "%REPORT_ROOT%\analysis_outputs\apply_field_renames_and_build_insights.js"
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

echo.
echo [DONE] GFFunds advisor update and report refresh completed successfully.
exit /b 0

:run_performance_curves
cd /d "%MONITOR_ROOT%"
echo [INFO] Update GFFunds official performance curves...
"%PYTHON_EXE%" ".\scripts\update_gffunds_performance_curves.py" %PERFORMANCE_ARGS%
exit /b %ERRORLEVEL%

:run_collect
cd /d "%MONITOR_ROOT%"
echo [INFO] Collect GFFunds public advisor data...
"%PYTHON_EXE%" ".\scripts\collect_official_apps_public.py" %COLLECT_ARGS%
exit /b %ERRORLEVEL%

:open_gffunds_app_for_login
set "ADB_EXE=%ADVISOR_ADB_EXE%"
if "%ADB_EXE%"=="" set "ADB_EXE=%GFFUNDS_ADB_EXE%"
if "%ADB_EXE%"=="" set "ADB_EXE=adb"
if exist "%MONITOR_ROOT%\tools\platform-tools\adb.exe" if "%ADVISOR_ADB_EXE%%GFFUNDS_ADB_EXE%"=="" set "ADB_EXE=%MONITOR_ROOT%\tools\platform-tools\adb.exe"
"%ADB_EXE%" -s "%DEVICE_ID%" get-state >nul 2>nul
if not "%ERRORLEVEL%"=="0" (
    echo [WARN] ADB device is not available: %DEVICE_ID%
    echo [WARN] Connect the phone, unlock it, open GFFunds app manually, then rerun this BAT if needed.
    exit /b 0
)
for %%P in (com.gffunds.android com.gffunds.mobile com.gffund.android) do (
    "%ADB_EXE%" -s "%DEVICE_ID%" shell monkey -p %%P -c android.intent.category.LAUNCHER 1 >nul 2>nul
    if "!ERRORLEVEL!"=="0" (
        echo [INFO] Opened app package: %%P
        exit /b 0
    )
)
echo [WARN] Could not auto-detect GFFunds app package. Please open it manually and complete login.
exit /b 0

:prompt_login_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('广发基金投顾数据采集失败，可能需要重新登录。请在手机或投屏窗口完成广发基金 App 登录后，点击确定继续重试。','广发基金登录态处理','OK','Warning') } catch { exit 1 }"
if "%ERRORLEVEL%"=="0" exit /b 0
echo [INFO] Press any key after GFFunds app login is complete. The script will retry once.
pause >nul
exit /b 0

:usage
echo Usage:
echo   run_gffunds_update_and_refresh_report.bat
echo.
echo Environment overrides:
echo   GFFUNDS_MONITOR_ROOT
echo   GFFUNDS_DEPLOY_SITE_DIR
echo   GFFUNDS_DEVICE_ID
echo   GFFUNDS_ALGORITHM_VERSION
echo   ADVISOR_PYTHON_EXE
echo   ADVISOR_NODE_EXE
echo   ADVISOR_ADB_EXE
echo   GFFUNDS_PERFORMANCE_ARGS
echo   GFFUNDS_RUN_FULL_COLLECT=1
echo   GFFUNDS_RECONSTRUCT_STANDARD_NAV=1
echo   GFFUNDS_COLLECT_ARGS
echo.
echo Default mode updates official performance curves only, then refreshes the report.
echo Set GFFUNDS_RUN_FULL_COLLECT=1 to additionally collect holdings and rebalance details.
exit /b 0
