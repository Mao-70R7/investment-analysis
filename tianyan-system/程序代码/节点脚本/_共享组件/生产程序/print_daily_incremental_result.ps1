param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$RunDay = (Get-Date -Format "yyyy-MM-dd"),
    [int]$ExitCode = 0
)

$ErrorActionPreference = "Stop"

function Write-Field {
    param(
        [string]$Name,
        [object]$Value
    )
    if ($null -eq $Value) {
        $Value = ""
    }
    elseif ($Value -is [array]) {
        $Value = ($Value -join ",")
    }
    "{0,-30}: {1}" -f $Name, $Value
}

$runRoot = Join-Path $ProjectRoot ("data\raw\ttfund\incremental_update_runs\{0}" -f $RunDay)
$latestRun = $null
if (Test-Path -LiteralPath $runRoot) {
    $latestRun = Get-ChildItem -LiteralPath $runRoot -Directory |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

Write-Host ""
Write-Host "================ DAILY INCREMENTAL RESULT ================"
Write-Field "wrapper_exit_code" $ExitCode

if ($null -eq $latestRun) {
    Write-Field "summary" "not found"
    Write-Field "run_root" $runRoot
    Write-Host "=========================================================="
    exit 0
}

$summaryPath = Join-Path $latestRun.FullName "summary.json"
$statusPath = Join-Path $latestRun.FullName "status.json"
$planPath = Join-Path $latestRun.FullName "plan.json"

Write-Field "run_dir" $latestRun.FullName
Write-Field "summary_path" $summaryPath

if (-not (Test-Path -LiteralPath $summaryPath)) {
    Write-Field "summary" "missing"
    if (Test-Path -LiteralPath $statusPath) {
        Write-Field "status_path" $statusPath
        Get-Content -LiteralPath $statusPath -Raw
    }
    Write-Host "=========================================================="
    exit 0
}

$summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
$plan = $null
if (Test-Path -LiteralPath $planPath) {
    $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
}

Write-Field "state" $summary.state
Write-Field "finished_at" $summary.finished_at
Write-Field "failed_stage" $summary.failed_stage
Write-Field "failed_stage_log_path" $summary.failed_stage_log_path
Write-Field "error" $summary.error
Write-Field "device_id" $summary.device_id
Write-Field "history_mode" $summary.history_mode
Write-Field "latest_local_trade_date" $summary.latest_local_trade_date
Write-Field "remote_max_trade_date" $summary.remote_max_trade_date
Write-Field "target_trade_date" $summary.target_trade_date
Write-Field "no_new_trade_date" $summary.no_new_trade_date
Write-Field "should_collect" $summary.should_collect
Write-Field "should_sync_device_cache" $summary.should_sync_device_cache
Write-Field "device_cache_sync_state" $summary.device_cache_sync_state
Write-Field "selected_detail_total" $summary.selected_detail_total
Write-Field "adb_detail_drive_total" $summary.adb_detail_drive_total
Write-Field "detail_cooldown_days" $summary.detail_cooldown_days
Write-Field "stale_detail_total" $summary.stale_detail_total
Write-Field "detail_refresh_total" $summary.detail_refresh_total
Write-Field "benchmark_detail_repair_total" $summary.benchmark_detail_repair_total
Write-Field "selected_rebalance_probe_total" $summary.selected_rebalance_probe_total
Write-Field "selected_history_total" $summary.selected_history_total
Write-Field "adb_history_drive_total" $summary.adb_history_drive_total
Write-Field "quality_state" $summary.quality_state
Write-Field "quality_mode" $summary.quality_mode
Write-Field "fund_nav_refresh_state" $summary.fund_nav_refresh_state
Write-Field "fund_nav_incremental_days" $summary.fund_nav_incremental_days
Write-Field "fund_nav_refresh_mode" $summary.fund_nav_refresh_mode
Write-Field "index_quote_update_state" $summary.index_quote_update_state
Write-Field "deploy_export_state" $summary.deploy_export_state
Write-Field "deploy_page_set" $summary.deploy_page_set
Write-Field "deploy_basic_data_date" $summary.deploy_basic_data_date
Write-Field "deploy_needs_export" $summary.deploy_needs_export
Write-Field "deploy_only_post" $summary.deploy_only_post
Write-Field "deploy_site_dir" $summary.deploy_site_dir
Write-Field "collect_run_id" $summary.collect_run_id

if ($null -ne $plan) {
    Write-Field "quote_strategy_total" $plan.remote_probe.quote_strategy_total
    Write-Field "newer_strategy_total" $plan.remote_probe.newer_strategy_total_gt_watermark
    Write-Field "detail_refresh_total(plan)" $plan.selection.detail_refresh_total
    Write-Field "benchmark_repair_total(plan)" $plan.selection.benchmark_detail_repair_total
    Write-Field "stale_history_total(plan)" $plan.selection.stale_history_total
    Write-Field "same_day_coverage_ok" $plan.local_baseline.same_day_coverage_ok
    Write-Field "latest_trade_rows" $plan.local_baseline.latest_trade_rows
    Write-Field "latest_trade_strategy_total" $plan.local_baseline.latest_trade_strategy_total
    Write-Field "estimate_total_minutes" $plan.estimates.total_minutes
}

if ($summary.deploy_site_dir) {
    $manifestPath = Join-Path ([string]$summary.deploy_site_dir) "deployment_manifest.json"
    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Field "deploy_manifest" $manifestPath
        Write-Field "deploy_status" $manifest.status
        Write-Field "deploy_default_page" $manifest.defaultPage
        Write-Field "basic_data_files" $manifest.basicData.files
        Write-Field "basic_data_bytes" $manifest.basicData.bytes
        Write-Field "missing_files" $manifest.missing
    }
}

Write-Host "=========================================================="
