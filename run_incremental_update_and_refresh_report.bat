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
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=python"

set "NODE_EXE=%ADVISOR_NODE_EXE%"
if "%NODE_EXE%"=="" set "NODE_EXE=node"

set "MONITOR_ROOT=%ADVISOR_MONITOR_ROOT%"
if "%MONITOR_ROOT%"=="" set "MONITOR_ROOT=%TTFUND_MONITOR_ROOT%"
if "%MONITOR_ROOT%"=="" set "MONITOR_ROOT=%GFFUNDS_MONITOR_ROOT%"
if "%MONITOR_ROOT%"=="" set "MONITOR_ROOT=D:\SyncthingShareToMpc\全市场投顾监控"

set "DEVICE_ID=%ADVISOR_DEVICE_ID%"
if "%DEVICE_ID%"=="" set "DEVICE_ID=%TTFUND_DEVICE_ID%"
if "%DEVICE_ID%"=="" set "DEVICE_ID=%GFFUNDS_DEVICE_ID%"
if "%DEVICE_ID%"=="" set "DEVICE_ID=b27b7c93"

set "HISTORY_MODE=%ADVISOR_HISTORY_MODE%"
if "%HISTORY_MODE%"=="" set "HISTORY_MODE=%TTFUND_HISTORY_MODE%"
if "%HISTORY_MODE%"=="" set "HISTORY_MODE=latest_only"

set "PROD_SITE_DIR=%ADVISOR_DEPLOY_SITE_DIR%"
if "%PROD_SITE_DIR%"=="" set "PROD_SITE_DIR=%TTFUND_DEPLOY_SITE_DIR%"
if "%PROD_SITE_DIR%"=="" set "PROD_SITE_DIR=%GFFUNDS_DEPLOY_SITE_DIR%"
if "%PROD_SITE_DIR%"=="" set "PROD_SITE_DIR=%MONITOR_ROOT%\site"

set "ALGORITHM_VERSION=%ADVISOR_ALGORITHM_VERSION%"
if "%ALGORITHM_VERSION%"=="" set "ALGORITHM_VERSION=%TTFUND_ALGORITHM_VERSION%"
if "%ALGORITHM_VERSION%"=="" set "ALGORITHM_VERSION=%GFFUNDS_ALGORITHM_VERSION%"
if "%ALGORITHM_VERSION%"=="" set "ALGORITHM_VERSION=standard_rebalance_asset_dual_nav_v10_all_channels_20260528"

set "RUN_TTFUND=%RUN_TTFUND_UPDATE%"
if "%RUN_TTFUND%"=="" set "RUN_TTFUND=1"

set "RUN_GFFUNDS=%RUN_GFFUNDS_UPDATE%"
if "%RUN_GFFUNDS%"=="" set "RUN_GFFUNDS=1"

set "RUN_FULL_GFFUNDS=%GFFUNDS_RUN_FULL_COLLECT%"
if "%RUN_FULL_GFFUNDS%"=="" set "RUN_FULL_GFFUNDS=0"

set "TTFUND_COLLECTION_ARGS=%TTFUND_COLLECTION_ARGS%"
if "%TTFUND_COLLECTION_ARGS%"=="" set "TTFUND_COLLECTION_ARGS=-SkipQuality"

set "GFFUNDS_PERFORMANCE_ARGS=%GFFUNDS_PERFORMANCE_ARGS%"
if "%GFFUNDS_PERFORMANCE_ARGS%"=="" set "GFFUNDS_PERFORMANCE_ARGS=--workers 8"

set "GFFUNDS_COLLECT_ARGS=%GFFUNDS_COLLECT_ARGS%"
if "%GFFUNDS_COLLECT_ARGS%"=="" set "GFFUNDS_COLLECT_ARGS=--apps gffunds --workers 8 --gffunds-skip-fund-nav --gffunds-skip-protocol-pdf"

set "POST_QUALITY_ARGS=%ADVISOR_POST_QUALITY_ARGS%"
if "%POST_QUALITY_ARGS%"=="" set "POST_QUALITY_ARGS=--timeout 7200"

if /I "%~1"=="/?" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage

if not "%~1"=="" set "DEVICE_ID=%~1"
if not "%~2"=="" set "HISTORY_MODE=%~2"

echo ============================================================
echo [INFO] Advisor incremental update + report refresh
echo [INFO] Report root  : %REPORT_ROOT%
echo [INFO] Monitor root : %MONITOR_ROOT%
echo [INFO] Device ID    : %DEVICE_ID%
echo [INFO] History mode : %HISTORY_MODE%
echo [INFO] Prod site    : %PROD_SITE_DIR%
echo [INFO] Algorithm    : %ALGORITHM_VERSION%
echo [INFO] Python       : %PYTHON_EXE%
echo [INFO] Node         : %NODE_EXE%
echo [INFO] Run TTFund   : %RUN_TTFUND%
echo [INFO] Run GFFunds  : %RUN_GFFUNDS%
echo [INFO] GF full data : %RUN_FULL_GFFUNDS%
echo ============================================================
echo.

if not exist "%MONITOR_ROOT%\scripts\run_ttfund_post_update_quality.py" (
    echo [ERROR] Missing unified postprocess script: %MONITOR_ROOT%\scripts\run_ttfund_post_update_quality.py
    exit /b 2
)

if /I "%RUN_TTFUND%"=="1" (
    if not exist "%MONITOR_ROOT%\run_daily_incremental.bat" (
        echo [ERROR] Missing TTFund incremental script: %MONITOR_ROOT%\run_daily_incremental.bat
        exit /b 3
    )
    call :run_ttfund_collection
    if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%
) else (
    echo [INFO] Skip TTFund update because RUN_TTFUND_UPDATE=%RUN_TTFUND%.
)

if /I "%RUN_GFFUNDS%"=="1" (
    if not exist "%MONITOR_ROOT%\scripts\update_gffunds_performance_curves.py" (
        echo [ERROR] Missing GFFunds performance updater: %MONITOR_ROOT%\scripts\update_gffunds_performance_curves.py
        exit /b 4
    )
    call :run_gffunds_update
    if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%
) else (
    echo [INFO] Skip GFFunds update because RUN_GFFUNDS_UPDATE=%RUN_GFFUNDS%.
)

call :run_unified_postprocess
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

call :sync_report_data
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

call :rebuild_report_assets
if not "%ERRORLEVEL%"=="0" exit /b %ERRORLEVEL%

echo.
echo [DONE] Advisor incremental update and report refresh completed successfully.
exit /b 0

:run_ttfund_collection
echo.
echo [INFO] Step 1/5: collect TTFund incremental data only.
echo [INFO] TTFund collection args: %TTFUND_COLLECTION_ARGS%
cd /d "%MONITOR_ROOT%"
set "TTFUND_DEPLOY_SITE_DIR=%PROD_SITE_DIR%"
set "TTFUND_DISABLE_DEFAULT_ARGS=1"
set "TTFUND_EXTRA_ARGS=%TTFUND_COLLECTION_ARGS%"
call "%MONITOR_ROOT%\run_daily_incremental.bat" "%DEVICE_ID%" "%HISTORY_MODE%"
set "TTFUND_EXIT=%ERRORLEVEL%"
if not "%TTFUND_EXIT%"=="0" (
    echo [ERROR] TTFund incremental collection failed. Exit code: %TTFUND_EXIT%
    exit /b %TTFUND_EXIT%
)
exit /b 0

:run_gffunds_performance
echo.
echo [INFO] Step 2/5: update GFFunds official performance curves.
echo [INFO] GFFunds performance args: %GFFUNDS_PERFORMANCE_ARGS%
cd /d "%MONITOR_ROOT%"
"%PYTHON_EXE%" ".\scripts\update_gffunds_performance_curves.py" %GFFUNDS_PERFORMANCE_ARGS%
exit /b %ERRORLEVEL%

:run_gffunds_update
call :run_gffunds_performance
if "%ERRORLEVEL%"=="0" goto gffunds_performance_ok

echo.
echo [WARN] GFFunds performance curve update failed.
echo [WARN] If login state expired, complete login in GFFunds app and retry once.
call :open_gffunds_app_for_login
echo.
call :prompt_gffunds_login_ready
call :run_gffunds_performance
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] GFFunds performance curve update failed after login retry.
    exit /b 5
)

:gffunds_performance_ok
if /I not "%RUN_FULL_GFFUNDS%"=="1" exit /b 0

if not exist "%MONITOR_ROOT%\scripts\collect_official_apps_public.py" (
    echo [ERROR] Missing GFFunds full collector: %MONITOR_ROOT%\scripts\collect_official_apps_public.py
    exit /b 6
)

call :run_gffunds_full_collect
if "%ERRORLEVEL%"=="0" exit /b 0

echo.
echo [WARN] GFFunds full collection failed.
echo [WARN] If login state expired, complete login in GFFunds app and retry once.
call :open_gffunds_app_for_login
echo.
call :prompt_gffunds_login_ready
call :run_gffunds_full_collect
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] GFFunds full collection failed after login retry.
    exit /b 7
)
exit /b 0

:run_gffunds_full_collect
echo.
echo [INFO] Optional: collect GFFunds holdings and rebalance details.
echo [INFO] GFFunds collect args: %GFFUNDS_COLLECT_ARGS%
cd /d "%MONITOR_ROOT%"
"%PYTHON_EXE%" ".\scripts\collect_official_apps_public.py" %GFFUNDS_COLLECT_ARGS%
exit /b %ERRORLEVEL%

:run_unified_postprocess
echo.
echo [INFO] Step 3/5: run one unified load / quality / export pass.
echo [INFO] Postprocess args: %POST_QUALITY_ARGS%
cd /d "%MONITOR_ROOT%"
"%PYTHON_EXE%" ".\scripts\run_ttfund_post_update_quality.py" --algorithm-version "%ALGORITHM_VERSION%" --deploy-site-dir "%PROD_SITE_DIR%" --deploy-page-set basic_data %POST_QUALITY_ARGS%
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Unified postprocess failed. Exit code: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)
exit /b 0

:sync_report_data
if not exist "%PROD_SITE_DIR%\basic_data\data\basic_summary.js" (
    echo [ERROR] Production basic data was not exported: %PROD_SITE_DIR%\basic_data\data\basic_summary.js
    exit /b 8
)

echo.
echo [INFO] Step 4/5: sync production data files into current report.
robocopy "%PROD_SITE_DIR%\basic_data\data" "%REPORT_ROOT%\basic_data\data" /MIR /NFL /NDL /NJH /NJS /NP
set "ROBO_EXIT=%ERRORLEVEL%"
if %ROBO_EXIT% GEQ 8 (
    echo [ERROR] Data sync failed. Robocopy exit code: %ROBO_EXIT%
    exit /b %ROBO_EXIT%
)
echo [INFO] Data sync completed. Robocopy exit code: %ROBO_EXIT%
exit /b 0

:rebuild_report_assets
if not exist "%REPORT_ROOT%\analysis_outputs\apply_field_renames_and_build_insights.js" (
    echo [ERROR] Missing report postprocess script: %REPORT_ROOT%\analysis_outputs\apply_field_renames_and_build_insights.js
    exit /b 9
)

echo.
echo [INFO] Step 5/5: rebuild current report data packs and page assets.
cd /d "%REPORT_ROOT%"
"%NODE_EXE%" "%REPORT_ROOT%\analysis_outputs\apply_field_renames_and_build_insights.js"
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Report postprocess failed. Exit code: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)
exit /b 0

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

:prompt_gffunds_login_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('广发基金投顾数据采集失败，可能需要重新登录。请在手机或投屏窗口完成广发基金 App 登录后，点击确定继续重试。','广发基金登录态处理','OK','Warning') } catch { exit 1 }"
if "%ERRORLEVEL%"=="0" exit /b 0
echo [INFO] Press any key after GFFunds app login is complete. The script will retry once.
pause >nul
exit /b 0

:usage
echo Usage:
echo   run_incremental_update_and_refresh_report.bat [DeviceId] [HistoryMode]
echo.
echo Defaults:
echo   DeviceId    = b27b7c93
echo   HistoryMode = latest_only
echo.
echo Unified flow:
echo   1. collect TTFund incremental data without duplicate postprocess
echo   2. update GFFunds official performance curves
echo   3. optionally collect GFFunds holdings/rebalance with GFFUNDS_RUN_FULL_COLLECT=1
echo   4. run one unified load/quality/export pass
echo   5. sync data into this report and rebuild report assets
echo.
echo Environment overrides:
echo   ADVISOR_MONITOR_ROOT
echo   ADVISOR_DEPLOY_SITE_DIR
echo   ADVISOR_DEVICE_ID
echo   ADVISOR_HISTORY_MODE
echo   ADVISOR_ALGORITHM_VERSION
echo   ADVISOR_PYTHON_EXE
echo   ADVISOR_NODE_EXE
echo   ADVISOR_ADB_EXE
echo   RUN_TTFUND_UPDATE=0
echo   RUN_GFFUNDS_UPDATE=0
echo   TTFUND_COLLECTION_ARGS
echo   GFFUNDS_PERFORMANCE_ARGS
echo   GFFUNDS_RUN_FULL_COLLECT=1
echo   GFFUNDS_COLLECT_ARGS
echo   ADVISOR_POST_QUALITY_ARGS
echo.
echo Examples:
echo   run_incremental_update_and_refresh_report.bat
echo   run_incremental_update_and_refresh_report.bat b27b7c93 all_missing
echo   set GFFUNDS_RUN_FULL_COLLECT=1
echo   run_incremental_update_and_refresh_report.bat
exit /b 0
