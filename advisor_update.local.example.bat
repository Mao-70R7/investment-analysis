@echo off
rem Copy this file to advisor_update.local.bat on each PC and update the values.
rem advisor_update.local.bat is ignored by git because these paths and device IDs are machine-specific.

rem Required: root of the collector/monitor project that contains run_daily_incremental.bat and scripts\*.py.
set "ADVISOR_MONITOR_ROOT=D:\SyncthingShareToMpc\全市场投顾监控"

rem Optional: where the collector exports the production static site.
rem Defaults to %ADVISOR_MONITOR_ROOT%\site when left unset.
rem set "ADVISOR_DEPLOY_SITE_DIR=D:\SyncthingShareToMpc\全市场投顾监控\site"

rem Optional: Android device ID used by adb. Run "adb devices" to find it on this PC.
set "ADVISOR_DEVICE_ID=b27b7c93"

rem Optional: use explicit runtimes when PATH differs across machines.
rem set "ADVISOR_PYTHON_EXE=E:\software\python\python.exe"
rem set "ADVISOR_NODE_EXE=E:\nodejs\node.exe"
rem set "ADVISOR_ADB_EXE=D:\Android\platform-tools\adb.exe"

rem Optional workflow switches.
rem set "ADVISOR_HISTORY_MODE=latest_only"
rem set "RUN_TTFUND_UPDATE=1"
rem set "RUN_GFFUNDS_UPDATE=1"
rem set "GFFUNDS_RUN_FULL_COLLECT=0"
rem set "GFFUNDS_RECONSTRUCT_STANDARD_NAV=0"

rem Optional script arguments.
rem set "TTFUND_COLLECTION_ARGS=-SkipQuality"
rem set "GFFUNDS_PERFORMANCE_ARGS=--workers 8"
rem set "GFFUNDS_COLLECT_ARGS=--apps gffunds --workers 8 --gffunds-skip-fund-nav --gffunds-skip-protocol-pdf"
rem set "ADVISOR_POST_QUALITY_ARGS=--timeout 7200"
