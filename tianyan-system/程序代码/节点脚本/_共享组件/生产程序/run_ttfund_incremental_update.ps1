param(
    [Parameter(Mandatory = $true)]
    [string]$DeviceId,
    [ValidateSet("latest_only", "all_missing", "none")]
    [string]$HistoryMode = "latest_only",
    [string]$AlgorithmVersion = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528",
    [switch]$SkipQuality,
    [switch]$PlanOnly,
    [switch]$SkipLoadAnalysis,
    [switch]$SkipDashboardExport,
    [switch]$FullQualityReplay,
    [string]$PythonExe = "python",
    [string]$AdbExe = "adb",
    [int]$QuoteBatchSize = 50,
    [int]$QuoteProbeTimeoutSec = 20,
    [int]$OfficialCurveWorkers = 8,
    [int]$OfficialCurveRetries = 3,
    [switch]$SkipOfficialPerformanceCurve,
    [switch]$SkipFundNavRefresh,
[int]$FundNavWorkers = 12,
[int]$FundNavIncrementalDays = 10,
    [switch]$FullFundNavRefresh,
    [switch]$SkipIndexQuoteUpdate,
    [string]$DeploySiteDir = "",
    [ValidateSet("basic_data", "all")]
    [string]$DeployPageSet = "basic_data",
    [switch]$ForceDeployExport,
    [switch]$SkipDeployExport,
    [switch]$SkipDiscoveryCacheSync,
    [switch]$SkipCatalogDiscovery,
    [int]$DiscoveryWarmupSec = 6,
    [switch]$DisableDirectInterface,
    [switch]$SkipLatestRebalancePrefilter,
    [string]$DirectInterfaceFlowPath = "",
    [int]$DirectInterfaceWorkers = 16,
    [ValidateSet("all", "updated", "stale", "none")]
    [string]$DirectRebalanceProbeMode = "all",
    [ValidateSet("selected", "missing", "none")]
    [string]$AdbRebalanceFallbackMode = "selected",
    [ValidateSet("missing_detail", "all_missing_text", "none")]
    [string]$BenchmarkDetailRepairMode = "all_missing_text",
    [int]$BenchmarkDetailRepairLimit = 80,
    [int]$BenchmarkDetailCooldownDays = 7,
    [int]$DetailCooldownDays = 7,
    [int]$DetailRefreshLimit = 70,
    [int]$StoppedDetailCooldownDays = 30,
    [int]$CurrentHoldingCooldownDays = 1,
    [int]$CurrentHoldingRefreshLimit = 0,
    [int]$DetailScanSwipes = 3,
    [int]$DeviceFailureCircuitBreakThreshold = 5,
    [int]$DeviceFailureCircuitRecoveryLimit = 2,
    [int]$RebalanceStaleDays = 1,
    [int]$RebalanceRollingLimit = 0,
    [int]$AdbFallbackLimit = 0,
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = if ($env:ADVISOR_CODE_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_CODE_ROOT) } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path }
$RawRoot = if ($env:ADVISOR_RAW_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_RAW_ROOT) } else { Join-Path $ProjectRoot "data\raw" }
$NormalizedRoot = if ($env:ADVISOR_NORMALIZED_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_NORMALIZED_ROOT) } else { Join-Path $ProjectRoot "data\normalized" }
. (Join-Path $PSScriptRoot "json_array_helpers.ps1")
if (-not $DeploySiteDir) {
    $DeploySiteDir = if ($env:ADVISOR_REPORT_ROOT) { $env:ADVISOR_REPORT_ROOT } else { Join-Path $ProjectRoot "site" }
}
$runAt = Get-Date
$day = $runAt.ToString("yyyy-MM-dd")
$runId = if ($RunId) { $RunId } else { $runAt.ToString("yyyyMMddTHHmmssK").Replace(":", "") }
$jobRoot = Join-Path $RawRoot ("ttfund\\incremental_update_runs\\{0}\\{1}" -f $day, $runId)
$null = New-Item -ItemType Directory -Path $jobRoot -Force

$statusPath = Join-Path $jobRoot "status.json"
$summaryPath = Join-Path $jobRoot "summary.json"
$planPath = Join-Path $jobRoot "plan.json"
$script:CurrentStage = ""
$script:CurrentStageLogPath = ""
$script:CurrentStageExitCode = 0

function Resolve-AdbExe {
    param([string]$RequestedAdbExe)
    if ($RequestedAdbExe -and $RequestedAdbExe -ne "adb") {
        return $RequestedAdbExe
    }
    $localAdb = Join-Path $ProjectRoot "tools\platform-tools\adb.exe"
    if (Test-Path -LiteralPath $localAdb) {
        return $localAdb
    }
    return $RequestedAdbExe
}

$AdbExe = Resolve-AdbExe -RequestedAdbExe $AdbExe
$currentHoldingDevices = @($DeviceId)
$currentHoldingUnavailableDevices = @()
$detailPrimaryFailedIds = @()
$detailPhysicalRetrySummary = $null
$detailFinalFailedIds = @()
$currentHoldingPrimaryFailedIds = @()
$currentHoldingPhysicalRetrySummary = $null
$currentHoldingFinalFailedIds = @()
$historyPrimaryFailedIds = @()
$historyPhysicalRetrySummary = $null
$historyFinalFailedIds = @()
$detailPhysicalRetryAttempted = $false
$currentHoldingPhysicalRetryAttempted = $false
$historyPhysicalRetryAttempted = $false

function Get-StageLabel {
    param([string]$Stage)
    $labels = @{
        "bootstrap" = "bootstrap"
        "00_sync_device_cache" = "sync device cache"
        "00_discover_strategy_catalog" = "discover strategy catalog"
        "00_build_plan" = "build incremental plan"
        "device_cache" = "check device cache"
        "01_direct_interface" = "direct interface probe"
        "01_direct_history" = "direct history repair"
        "01_select_rebalance_history_targets" = "select rebalance history targets"
        "01_strategy_work_bundle" = "collect per-strategy work bundles"
        "01_strategy_work_bundle_physical_retry" = "retry incomplete work-bundle fields on physical phone"
        "01_detail_drive" = "collect missing detail"
        "01_detail_drive_physical_retry" = "physical phone second pass for failed detail"
        "01_current_holding_drive" = "collect current holding"
        "01_current_holding_drive_physical_retry" = "physical phone second pass for failed current holding"
        "01_latest_rebalance_prefilter" = "latest rebalance prefilter"
        "02_history_drive" = "collect missing history"
        "02_history_drive_physical_retry" = "physical phone second pass for failed history"
        "03_collect" = "collect logged-in data"
        "03c_official_curve_gap_retry" = "targeted official curve gap retry"
        "04_post_update_quality" = "load quality export"
        "done" = "done"
        "failed" = "failed"
    }
    if ($labels.ContainsKey($Stage)) {
        return $labels[$Stage]
    }
    return $Stage
}

function Write-Status {
    param(
        [string]$Stage,
        [string]$State,
        [string]$Message = ""
    )
    $payload = [ordered]@{
        updated_at = (Get-Date).ToString("s")
        stage = $Stage
        state = $State
        message = $Message
        device_id = $DeviceId
        history_mode = $HistoryMode
        job_root = $jobRoot
        stage_log_path = $script:CurrentStageLogPath
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $statusPath

    $label = Get-StageLabel -Stage $Stage
    $displayMessage = $Message
    if ($displayMessage.Length -gt 220) {
        $displayMessage = $displayMessage.Substring(0, 220) + "..."
    }
    $line = "[{0}] {1} | {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $label, $State
    if ($displayMessage) {
        $line = "{0} | {1}" -f $line, $displayMessage
    }
    Write-Host $line
}

function Invoke-Stage {
    param(
        [string]$StageName,
        [string[]]$CommandArgs
    )
    $logPath = Join-Path $jobRoot ("{0}.log" -f $StageName)
    $script:CurrentStage = $StageName
    $script:CurrentStageLogPath = $logPath
    Write-Status -Stage $StageName -State "running" -Message ($CommandArgs -join " ")
    "[$(Get-Date -Format s)] START $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
    $commandText = $CommandArgs -join " "
    "[$(Get-Date -Format s)] CMD   $PythonExe -u $commandText" | Tee-Object -FilePath $logPath -Append | Out-Null
    Push-Location $ProjectRoot
    $stageExitCode = 0
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonExe "-u" @CommandArgs 2>&1 | ForEach-Object {
                $text = [string]$_
                $text | Tee-Object -FilePath $logPath -Append
            }
            $stageExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }
    finally {
        Pop-Location
    }
    if ($stageExitCode -ne 0) {
        $script:CurrentStageExitCode = $stageExitCode
        "[$(Get-Date -Format s)] FAILED $StageName exit_code=$stageExitCode" | Tee-Object -FilePath $logPath -Append | Out-Null
        throw "Stage $StageName failed with exit code $stageExitCode; log=$logPath"
    }
    "[$(Get-Date -Format s)] DONE  $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
    Write-Status -Stage $StageName -State "completed" -Message "stage finished"
    $script:CurrentStage = ""
    $script:CurrentStageLogPath = ""
    $script:CurrentStageExitCode = 0
}

function Invoke-OptionalStage {
    param(
        [string]$StageName,
        [string[]]$CommandArgs
    )
    $logPath = Join-Path $jobRoot ("{0}.log" -f $StageName)
    Write-Status -Stage $StageName -State "running" -Message ($CommandArgs -join " ")
    "[$(Get-Date -Format s)] START $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
    $commandText = $CommandArgs -join " "
    "[$(Get-Date -Format s)] CMD   $PythonExe -u $commandText" | Tee-Object -FilePath $logPath -Append | Out-Null
    Push-Location $ProjectRoot
    $stageExitCode = 0
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonExe "-u" @CommandArgs 2>&1 | ForEach-Object {
                $text = [string]$_
                $text | Tee-Object -FilePath $logPath -Append
            }
            $stageExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }
    finally {
        Pop-Location
    }
    if ($stageExitCode -ne 0) {
        "[$(Get-Date -Format s)] OPTIONAL_FAILED $StageName exit_code=$stageExitCode" | Tee-Object -FilePath $logPath -Append | Out-Null
        Write-Status -Stage $StageName -State "failed_nonblocking" -Message "备用设备补采失败，保留缺口供最终摘要分析"
    }
    else {
        "[$(Get-Date -Format s)] DONE  $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
        Write-Status -Stage $StageName -State "completed" -Message "备用设备定向补采完成"
    }
}

function Get-DriveFailureIds {
    param(
        [string]$ResultsPath,
        [ValidateSet("detail", "benchmark", "current_holding", "history", "bundle")]
        [string]$Mode,
        [string[]]$ExpectedIds = @()
    )
    $rows = @(Read-JsonArrayStrict -Path $ResultsPath)
    if ($ExpectedIds.Count -gt 0 -and $rows.Count -eq 0) {
        throw "Drive result is empty while strategies were expected: $ResultsPath"
    }
    $failed = New-Object System.Collections.Generic.List[string]
    $processed = @{}
    foreach ($row in $rows) {
        $strategyId = [string]$row.strategy_id
        if (-not $strategyId) {
            throw "Drive result contains a row without strategy_id: $ResultsPath"
        }
        if ($processed.ContainsKey($strategyId)) {
            throw "Drive result contains duplicate strategy_id '$strategyId': $ResultsPath"
        }
        $processed[$strategyId] = $true
        $ok = switch ($Mode) {
            "detail" { [bool]$row.detail_ok }
            "benchmark" { [bool]$row.detail_ok -and [bool]$row.benchmark_text_ok }
            "current_holding" { [bool]$row.detail_ok -and [bool]$row.holding_info_ok }
            "history" { [bool]$row.detail_ok -and ([bool]$row.history_adjustment_ok -or [bool]$row.history_page_seen) }
            "bundle" { [bool]$row.required_fields_ok }
        }
        if (-not $ok) {
            $failed.Add($strategyId)
        }
    }
    foreach ($strategyId in $ExpectedIds) {
        $normalizedId = [string]$strategyId
        if ($normalizedId -and (-not $processed.ContainsKey($normalizedId))) {
            $failed.Add($normalizedId)
        }
    }
    return @($failed | Select-Object -Unique)
}

function Get-OptionalDriveFailureIds {
    param(
        [string]$ResultsPath,
        [ValidateSet("detail", "benchmark", "current_holding", "history", "bundle")]
        [string]$Mode,
        [string[]]$ExpectedIds = @()
    )
    try {
        return @(Get-DriveFailureIds -ResultsPath $ResultsPath -Mode $Mode -ExpectedIds $ExpectedIds)
    }
    catch {
        Write-Host ("[设备补采结果无效] {0}，保留全部失败项。原因：{1}" -f $ResultsPath, $_.Exception.Message)
        return @($ExpectedIds | Where-Object { $_ } | Select-Object -Unique)
    }
}

function Invoke-AdbBestEffort {
    param([string[]]$Args)
    try {
        & $AdbExe @Args | Out-Null
    }
    catch {
    }
}

function Get-LatestTtfundCollectionRunId {
    $summaryRoot = Join-Path $NormalizedRoot "ttfund\collection_summary"
    if (-not (Test-Path -LiteralPath $summaryRoot)) {
        return $null
    }
    $latest = Get-ChildItem -LiteralPath $summaryRoot -Recurse -Filter "*.json" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) {
        return $null
    }
    return [System.IO.Path]::GetFileNameWithoutExtension($latest.Name)
}

function Get-DeployedBasicDataDate {
    param([string]$DeployRoot)
    if (-not $DeployRoot) {
        return $null
    }
    $summaryJs = Join-Path $DeployRoot "basic_data\data\basic_summary.js"
    if (-not (Test-Path -LiteralPath $summaryJs)) {
        return $null
    }
    $dataUpdatedKey = -join ([char[]](0x6570, 0x636E, 0x66F4, 0x65B0, 0x81F3))
    $pattern = '"' + [regex]::Escape($dataUpdatedKey) + '"\s*:\s*"([^"]+)"'
    try {
        $text = Get-Content -LiteralPath $summaryJs -Raw -Encoding UTF8
        $match = [regex]::Match($text, $pattern)
        if ($match.Success) {
            return [string]$match.Groups[1].Value
        }
    }
    catch {
    }
    return $null
}

function ConvertTo-StringArray {
    param($Value)
    if ($null -eq $Value) {
        return @()
    }
    if ($Value -is [System.Array]) {
        return @($Value | ForEach-Object { [string]$_ } | Where-Object { $_ })
    }
    return @([string]$Value | Where-Object { $_ })
}

function Join-UniqueStrings {
    param([object[]]$Groups)
    $seen = @{}
    $result = @()
    foreach ($group in $Groups) {
        foreach ($item in @($group)) {
            $value = [string]$item
            if ($value -and -not $seen.ContainsKey($value)) {
                $seen[$value] = $true
                $result += $value
            }
        }
    }
    return @($result)
}

function Write-StrategyFile {
    param(
        [string]$Path,
        [string[]]$StrategyIds
    )
    $parent = Split-Path -Parent $Path
    if ($parent) {
        $null = New-Item -ItemType Directory -Path $parent -Force
    }
    $StrategyIds | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Write-StrategyWorkBundleFile {
    param(
        [string]$Path,
        [string[]]$DetailIds,
        [string[]]$BenchmarkIds,
        [string[]]$CurrentHoldingIds,
        [string[]]$HistoryIds
    )
    $detailSet = @{}
    $benchmarkSet = @{}
    $holdingSet = @{}
    $historySet = @{}
    foreach ($strategyId in $DetailIds) { if ($strategyId) { $detailSet[[string]$strategyId] = $true } }
    foreach ($strategyId in $BenchmarkIds) { if ($strategyId) { $benchmarkSet[[string]$strategyId] = $true } }
    foreach ($strategyId in $CurrentHoldingIds) { if ($strategyId) { $holdingSet[[string]$strategyId] = $true } }
    foreach ($strategyId in $HistoryIds) { if ($strategyId) { $historySet[[string]$strategyId] = $true } }
    $allIds = Join-UniqueStrings -Groups @($HistoryIds, $CurrentHoldingIds, $DetailIds)
    $bundles = @(
        foreach ($strategyIdValue in $allIds) {
            $strategyId = [string]$strategyIdValue
            $needsHistory = $historySet.ContainsKey($strategyId)
            $needsHolding = $holdingSet.ContainsKey($strategyId)
            $needsDetail = $detailSet.ContainsKey($strategyId) -or $needsHolding -or $needsHistory
            $profile = if ($needsHistory) { "history_bundle" } elseif ($needsHolding) { "current_holding_bundle" } else { "detail_bundle" }
            [ordered]@{
                strategy_id = $strategyId
                capture_profile = $profile
                deep_detail_refresh = $detailSet.ContainsKey($strategyId)
                required_fields = [ordered]@{
                    detail = $needsDetail
                    benchmark_text = $benchmarkSet.ContainsKey($strategyId)
                    current_holding = $needsHolding
                    rebalance_history = $needsHistory
                }
            }
        }
    )
    $parent = Split-Path -Parent $Path
    if ($parent) {
        $null = New-Item -ItemType Directory -Path $parent -Force
    }
    $temporaryPath = "{0}.{1}.tmp" -f $Path, $PID
    [ordered]@{
        schema_version = 1
        generated_at = (Get-Date).ToString("s")
        strategy_work_bundles = $bundles
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    return @($allIds)
}

function Find-DirectInterfaceFlow {
    if ($DirectInterfaceFlowPath) {
        if (Test-Path -LiteralPath $DirectInterfaceFlowPath) {
            return (Resolve-Path -LiteralPath $DirectInterfaceFlowPath).Path
        }
        return $null
    }
    $flowRoot = Join-Path $RawRoot "ttfund\interface_probe"
    if (-not (Test-Path -LiteralPath $flowRoot)) {
        return $null
    }
    $latest = Get-ChildItem -LiteralPath $flowRoot -Recurse -Filter "flows.mitm" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -gt 0 } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) {
        return $null
    }
    return $latest.FullName
}

function Read-DirectInterfaceSuccess {
    param([string]$ResultsPath)
    $success = @{
        detail = @{}
        benchmark = @{}
        holding = @{}
        history = @{}
    }
    $rows = @(Read-JsonArrayStrict -Path $ResultsPath)
    foreach ($row in $rows) {
        $strategyId = [string]$row.strategy_id
        if (-not $strategyId) {
            throw "Direct interface result contains a row without strategy_id: $ResultsPath"
        }
        if ([bool]$row.detail_ok) {
            $success.detail[$strategyId] = $true
        }
        if ([bool]$row.benchmark_text_ok) {
            $success.benchmark[$strategyId] = $true
        }
        if ([bool]$row.detail_ok -and [bool]$row.holding_info_ok) {
            $success.holding[$strategyId] = $true
        }
        if ([bool]$row.history_adjustment_ok -or [bool]$row.history_checked_ok) {
            $success.history[$strategyId] = $true
        }
    }
    return $success
}

function Read-JsonObjectOrNull {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-JsonIntProperty {
    param(
        $Object,
        [string]$Name
    )
    if ($null -eq $Object) {
        return 0
    }
    $value = $Object.$Name
    if ($null -eq $value) {
        return 0
    }
    return [int]$value
}

try {
    Write-Status -Stage "bootstrap" -State "running" -Message "prepare device and build plan"
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "get-state")
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "shell", "svc", "power", "stayon", "usb")
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "shell", "input", "keyevent", "224")

    $discoverySyncState = "skipped"
    if (-not $SkipDiscoveryCacheSync) {
        Invoke-Stage -StageName "00_sync_device_cache" -CommandArgs @(
            ".\节点脚本\_共享组件\生产程序\sync_ttfund_device_cache.py",
            "--device-id", $DeviceId,
            "--adb-path", $AdbExe,
            "--warmup-sec", "$DiscoveryWarmupSec",
            "--allow-missing-device",
            "--run-dir", (Join-Path $jobRoot "00_sync_device_cache")
        )
        $discoverySummaryPath = Join-Path $jobRoot "00_sync_device_cache\summary.json"
        if (Test-Path -LiteralPath $discoverySummaryPath) {
            $discoverySummary = Get-Content -LiteralPath $discoverySummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $discoverySyncState = [string]$discoverySummary.state
        }
        else {
            $discoverySyncState = "completed_no_summary"
        }
    }

    $catalogManifestPath = Join-Path $jobRoot "00_discover_strategy_catalog\catalog_discovery.json"
    $catalogDiscoveryState = "skipped"
    if (-not $SkipCatalogDiscovery) {
        Invoke-OptionalStage -StageName "00_discover_strategy_catalog" -CommandArgs @(
            ".\节点脚本\_共享组件\生产程序\discover_ttfund_strategy_catalog.py",
            "--device-id", $DeviceId,
            "--adb-path", $AdbExe,
            "--cache-dir", (Join-Path $RawRoot "device_cache"),
            "--output-path", $catalogManifestPath,
            "--run-dir", (Join-Path $jobRoot "00_discover_strategy_catalog"),
            "--warmup-sec", "$DiscoveryWarmupSec",
            "--allow-missing-device"
        )
        if (Test-Path -LiteralPath $catalogManifestPath) {
            $catalogDiscovery = Get-Content -LiteralPath $catalogManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $catalogDiscoveryState = [string]$catalogDiscovery.state
        }
        else {
            $catalogDiscoveryState = "completed_no_manifest"
        }
    }

    Invoke-Stage -StageName "00_build_plan" -CommandArgs @(
        ".\节点脚本\_共享组件\生产程序\build_ttfund_incremental_plan.py",
        "--history-mode", $HistoryMode,
        "--quote-batch-size", "$QuoteBatchSize",
        "--quote-probe-timeout-sec", "$QuoteProbeTimeoutSec",
        "--direct-rebalance-probe-mode", $DirectRebalanceProbeMode,
        "--adb-rebalance-fallback-mode", $AdbRebalanceFallbackMode,
        "--benchmark-detail-repair-mode", $BenchmarkDetailRepairMode,
        "--benchmark-detail-repair-limit", "$BenchmarkDetailRepairLimit",
        "--benchmark-detail-cooldown-days", "$BenchmarkDetailCooldownDays",
        "--detail-cooldown-days", "$DetailCooldownDays",
        "--detail-refresh-limit", "$DetailRefreshLimit",
        "--stopped-detail-cooldown-days", "$StoppedDetailCooldownDays",
        "--current-holding-cooldown-days", "$CurrentHoldingCooldownDays",
        "--current-holding-refresh-limit", "$CurrentHoldingRefreshLimit",
        "--rebalance-stale-days", "$RebalanceStaleDays",
        "--rebalance-rolling-limit", "$RebalanceRollingLimit",
        "--summary-only",
        "--catalog-manifest-path", $catalogManifestPath,
        "--output-path", $planPath
    )

    $plan = Get-Content $planPath -Raw | ConvertFrom-Json
    Write-Status -Stage "00_build_plan" -State "completed" -Message (
        "target={0}; local={1}; catalog={2} catalog_new={3} catalog_state={4}; detail={5} stale_detail={6} cooldown_days={7}; current_holding={8} stopped_skipped={9} stopped_refreshable={10}; direct_probe={11}; history={12}; estimate={13}min" -f
        $plan.remote_probe.max_trade_date,
        $plan.local_baseline.latest_trade_date,
        [int]$plan.local_baseline.catalog_strategy_total,
        [int]$plan.local_baseline.catalog_discovered_new_total,
        [string]$plan.local_baseline.catalog_discovery_state,
        [int]$plan.selection.selected_detail_total,
        [int]$plan.selection.stale_detail_total,
        [int]$plan.selection.detail_cooldown_days,
        [int]$plan.selection.selected_current_holding_total,
        [int]$plan.selection.definitively_stopped_current_holding_skipped_total,
        [int]$plan.selection.stopped_but_refreshable_total,
        [int]$plan.selection.selected_rebalance_probe_total,
        [int]$plan.selection.selected_history_total,
        $plan.estimates.total_minutes
    )
    $targetTradeDate = [string]$plan.remote_probe.max_trade_date
    if (-not $targetTradeDate) {
        $targetTradeDate = [string]$plan.local_baseline.latest_trade_date
    }
    $latestLocalTradeDate = [string]$plan.local_baseline.latest_trade_date
    $noNewTradeDate = $false
    if ($targetTradeDate -and $latestLocalTradeDate) {
        $noNewTradeDate = ($targetTradeDate -le $latestLocalTradeDate)
    }
    $deployBasicDataDate = Get-DeployedBasicDataDate -DeployRoot $DeploySiteDir
    $deployNeedsExport = $false
    if ($DeploySiteDir -and (-not $SkipDeployExport) -and $targetTradeDate) {
        $deployNeedsExport = ($ForceDeployExport -or (-not $deployBasicDataDate) -or ($targetTradeDate -gt $deployBasicDataDate))
    }
    $shouldRunPublicDataUpdate = $true
    $fastIncrementalQuality = (
        (-not $FullQualityReplay) -and
        ([bool]$plan.actions.should_collect -or $deployNeedsExport -or $shouldRunPublicDataUpdate)
    )
    $collectRunId = $null
    $officialCurveState = "not_needed"
    $officialCurveSummaryPath = $null
    $officialCurveDeviceRetrySummaryPath = $null
    $officialCurveSummary = $null
    if ($plan.requires_full_capture) {
        $summary = [ordered]@{
            finished_at = (Get-Date).ToString("s")
            state = "blocked"
            device_id = $DeviceId
            device_mode = "physical_only"
            retry_device_id = $DeviceId
            device_retry_policy = "physical_max_attempts_2_then_same_phone_failed_ids_second_pass"
            fund_nav_retry_policy = "public_api_primary_then_low_concurrency_retry_twice"
            history_mode = $HistoryMode
            job_root = $jobRoot
            reason = "missing_local_baseline"
            plan_path = $planPath
        }
        $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $summaryPath
        Write-Status -Stage "done" -State "blocked" -Message "missing local baseline, run full capture first"
        exit 20
    }

    if ($PlanOnly) {
        $summary = [ordered]@{
            finished_at = (Get-Date).ToString("s")
            state = "plan_only"
            device_id = $DeviceId
            device_mode = "physical_only"
            retry_device_id = $DeviceId
            device_retry_policy = "physical_max_attempts_2_then_same_phone_failed_ids_second_pass"
            fund_nav_retry_policy = "public_api_primary_then_low_concurrency_retry_twice"
            history_mode = $HistoryMode
            job_root = $jobRoot
            plan_path = $planPath
            discovery_cache_sync_state = $discoverySyncState
            selected_detail_total = [int]$plan.selection.selected_detail_total
            detail_cooldown_days = [int]$plan.selection.detail_cooldown_days
            stopped_detail_cooldown_days = [int]$plan.selection.stopped_detail_cooldown_days
            stale_detail_total = [int]$plan.selection.stale_detail_total
            stale_active_detail_total = [int]$plan.selection.stale_active_detail_total
            stale_stopped_detail_total = [int]$plan.selection.stale_stopped_detail_total
            mandatory_detail_total = [int]$plan.selection.mandatory_detail_total
            routine_detail_selected_total = [int]$plan.selection.routine_detail_selected_total
            routine_detail_piggyback_total = [int]$plan.selection.routine_detail_piggyback_total
            routine_detail_deferred_total = [int]$plan.selection.routine_detail_deferred_total
            detail_refresh_total = [int]$plan.selection.detail_refresh_total
            current_holding_cooldown_days = [int]$plan.selection.current_holding_cooldown_days
            stale_current_holding_total = [int]$plan.selection.stale_current_holding_total
            selected_current_holding_total = [int]$plan.selection.selected_current_holding_total
            definitively_stopped_current_holding_skipped_total = [int]$plan.selection.definitively_stopped_current_holding_skipped_total
            stopped_but_refreshable_total = [int]$plan.selection.stopped_but_refreshable_total
            current_holding_lifecycle_reason_counts = $plan.selection.current_holding_lifecycle_reason_counts
            benchmark_detail_repair_total = [int]$plan.selection.benchmark_detail_repair_total
            selected_rebalance_probe_total = [int]$plan.selection.selected_rebalance_probe_total
            selected_history_total = [int]$plan.selection.selected_history_total
            latest_local_trade_date = $plan.local_baseline.latest_trade_date
            remote_max_trade_date = $plan.remote_probe.max_trade_date
            new_strategy_total = [int]$plan.local_baseline.new_strategy_total
            cache_discovered_new_total = [int]$plan.local_baseline.cache_discovered_new_total
        }
        $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $summaryPath
        Write-Status -Stage "done" -State "plan_only" -Message "plan built; no collection executed"
        exit 0
    }

    $selectedDetailIds = ConvertTo-StringArray $plan.selection.selected_detail_ids
    $selectedBenchmarkIds = ConvertTo-StringArray $plan.selection.benchmark_detail_repair_ids
    $selectedCurrentHoldingIds = ConvertTo-StringArray $plan.selection.selected_current_holding_ids
    $selectedHistoryIds = ConvertTo-StringArray $plan.selection.selected_history_ids
    $selectedProbeIds = ConvertTo-StringArray $plan.selection.selected_rebalance_probe_ids
    $requiredHistoryIds = @()
    if ($HistoryMode -eq "all_missing" -or $AdbRebalanceFallbackMode -eq "missing") {
        $requiredHistoryIds = ConvertTo-StringArray $plan.selection.all_missing_history_ids
    }
    $detailDriveIds = @($selectedDetailIds)
    $currentHoldingDriveIds = @($selectedCurrentHoldingIds)
    $historyDriveIds = @($selectedHistoryIds)
    $currentHoldingDrivePlannedTotal = $currentHoldingDriveIds.Count
    $currentHoldingDriveExecutedTotal = 0
    $directInterfaceState = "skipped"
    $directInterfaceFlow = $null
    $directInterfaceRunDir = $null
    $directHistoryRunDir = $null
    $directInterfaceError = $null
    $directHistoryState = "not_needed"
    $directHistoryError = $null
    $directRan = $false
    $directHistoryRan = $false
    $directHistoryAttempted = $false
    $latestPrefilterState = "not_needed"
    $latestPrefilterRunDir = $null
    $latestPrefilterReportPath = $null
    $latestPrefilterTargetPath = $null
    $latestPrefilterTargetTotal = 0
    $latestPrefilterRan = $false
    $detailDriveSummary = $null
    $currentHoldingDriveSummary = $null
    $historyDriveSummary = $null
    $strategyWorkBundleSummary = $null
    $strategyWorkBundleIds = @()
    $strategyWorkBundlePrimaryFailedIds = @()
    $strategyWorkBundleFinalFailedIds = @()

    $directInterfaceFlow = Find-DirectInterfaceFlow
    $directTargetIds = @($selectedProbeIds)
    if ($directInterfaceFlow) {
        $directTargetIds = Join-UniqueStrings -Groups @($selectedProbeIds, $detailDriveIds, $currentHoldingDriveIds)
    }
    if ((-not $DisableDirectInterface) -and ($directTargetIds.Count -gt 0) -and ([bool]$plan.actions.should_run_direct_rebalance_probe -or $detailDriveIds.Count -gt 0 -or $currentHoldingDriveIds.Count -gt 0)) {
        $directInterfaceRunDir = Join-Path $jobRoot "01_direct_interface"
        $probeFile = Join-Path $directInterfaceRunDir "strategy_ids.txt"
        Write-StrategyFile -Path $probeFile -StrategyIds $directTargetIds
        $directArgs = @(
            ".\节点脚本\_共享组件\生产程序\collect_ttfund_direct_interfaces.py",
            "--strategy-file", $probeFile,
            "--run-dir", $directInterfaceRunDir,
            "--workers", "$DirectInterfaceWorkers",
            "--fetch-latest",
            "--skip-history",
            "--use-builtin-adjust-templates"
        )
        if ($directInterfaceFlow) {
            $directArgs += @("--flow-path", $directInterfaceFlow)
        }
        else {
            $directArgs += @("--skip-detail")
            $directInterfaceState = "running_builtin_adjust_templates"
            Write-Status -Stage "01_direct_interface" -State "running" -Message "use built-in public rebalance endpoints"
        }
        try {
            Invoke-Stage -StageName "01_direct_interface" -CommandArgs $directArgs
            $directRan = $true
            $directInterfaceState = $(if ($directInterfaceFlow) { "completed" } else { "completed_builtin_adjust_templates" })
            $directSuccess = Read-DirectInterfaceSuccess -ResultsPath (Join-Path $directInterfaceRunDir "results.json")
            $benchmarkRequiredSet = @{}
            foreach ($strategyId in $selectedBenchmarkIds) {
                $benchmarkRequiredSet[[string]$strategyId] = $true
            }
            $detailDriveIds = @(
                $detailDriveIds | Where-Object {
                    $strategyId = [string]$_
                    $detailSatisfied = $directSuccess.detail.ContainsKey($strategyId)
                    $benchmarkSatisfied = (-not $benchmarkRequiredSet.ContainsKey($strategyId)) -or $directSuccess.benchmark.ContainsKey($strategyId)
                    -not ($detailSatisfied -and $benchmarkSatisfied)
                }
            )
            $selectedBenchmarkIds = @(
                $selectedBenchmarkIds | Where-Object { -not $directSuccess.benchmark.ContainsKey([string]$_) }
            )
            $currentHoldingDriveIds = @(
                $currentHoldingDriveIds | Where-Object { -not $directSuccess.holding.ContainsKey([string]$_) }
            )
        }
        catch {
            $directInterfaceState = "failed_fallback_to_adb"
            $directInterfaceError = $_.Exception.Message
            Write-Status -Stage "01_direct_interface" -State "failed" -Message ("direct interface failed; fallback to ADB where selected: {0}" -f $directInterfaceError)
        }
    }
    elseif ($DisableDirectInterface) {
        $directInterfaceState = "disabled"
    }
    elseif ($directTargetIds.Count -eq 0) {
        $directInterfaceState = "skipped_no_direct_targets"
    }

    if (
        (-not $SkipLatestRebalancePrefilter) -and
        $directRan -and
        $selectedProbeIds.Count -gt 0 -and
        $HistoryMode -ne "none" -and
        $AdbRebalanceFallbackMode -ne "none"
    ) {
        $latestPrefilterRunDir = Join-Path $jobRoot "01_select_rebalance_history_targets"
        $latestPrefilterProbeFile = Join-Path $latestPrefilterRunDir "strategy_ids.txt"
        $latestPrefilterTargetPath = Join-Path $latestPrefilterRunDir "history_targets.txt"
        $latestPrefilterReportPath = Join-Path $latestPrefilterRunDir "latest_rebalance_prefilter_report.json"
        Write-StrategyFile -Path $latestPrefilterProbeFile -StrategyIds $selectedProbeIds
        try {
            Invoke-Stage -StageName "01_select_rebalance_history_targets" -CommandArgs @(
                ".\节点脚本\_共享组件\生产程序\select_ttfund_rebalance_history_targets.py",
                "--strategy-file", $latestPrefilterProbeFile,
                "--output-file", $latestPrefilterTargetPath,
                "--report-file", $latestPrefilterReportPath
            )
            $prefilterTargetIds = @()
            if (Test-Path -LiteralPath $latestPrefilterTargetPath) {
                $prefilterTargetIds = @(
                    Get-Content -LiteralPath $latestPrefilterTargetPath -Encoding UTF8 |
                        ForEach-Object { [string]$_ } |
                        Where-Object { $_ }
                )
            }
            $historyDriveIds = Join-UniqueStrings -Groups @($requiredHistoryIds, $prefilterTargetIds)
            $latestPrefilterTargetTotal = $prefilterTargetIds.Count
            $latestPrefilterState = "completed_direct_latest_probe"
            Write-Status -Stage "01_select_rebalance_history_targets" -State "completed" -Message ("direct latest probe selected {0} changed strategies; required repair targets {1}; history targets {2}" -f $latestPrefilterTargetTotal, $requiredHistoryIds.Count, $historyDriveIds.Count)
        }
        catch {
            $latestPrefilterState = "failed_after_direct_latest_probe"
            Write-Status -Stage "01_select_rebalance_history_targets" -State "failed" -Message ("select rebalance history targets failed after direct probe: {0}" -f $_.Exception.Message)
        }
    }
    elseif ($SkipLatestRebalancePrefilter) {
        $latestPrefilterState = "disabled"
    }

    if ((-not $DisableDirectInterface) -and $historyDriveIds.Count -gt 0) {
        $directHistoryRunDir = Join-Path $jobRoot "01_direct_history"
        $historyFile = Join-Path $directHistoryRunDir "strategy_ids.txt"
        Write-StrategyFile -Path $historyFile -StrategyIds $historyDriveIds
        $directHistoryArgs = @(
            ".\节点脚本\_共享组件\生产程序\collect_ttfund_direct_interfaces.py",
            "--strategy-file", $historyFile,
            "--run-dir", $directHistoryRunDir,
            "--workers", "$DirectInterfaceWorkers",
            "--fetch-latest",
            "--skip-detail",
            "--use-builtin-adjust-templates"
        )
        if ($directInterfaceFlow) {
            $directHistoryArgs += @("--flow-path", $directInterfaceFlow)
        }
        try {
            $directHistoryAttempted = $true
            Invoke-Stage -StageName "01_direct_history" -CommandArgs $directHistoryArgs
            $directHistoryRan = $true
            $directHistoryState = "completed"
            $directHistorySuccess = Read-DirectInterfaceSuccess -ResultsPath (Join-Path $directHistoryRunDir "results.json")
            $historyDriveIds = @($historyDriveIds | Where-Object { -not $directHistorySuccess.history.ContainsKey([string]$_) })
        }
        catch {
            $directHistoryState = "failed_fallback_to_adb"
            $directHistoryError = $_.Exception.Message
            Write-Status -Stage "01_direct_history" -State "failed" -Message ("direct history failed; fallback to ADB where selected: {0}" -f $directHistoryError)
        }
    }
    elseif ($DisableDirectInterface -and $historyDriveIds.Count -gt 0) {
        $directHistoryState = "disabled"
    }

    $adbFallbackTruncated = $false
    $needsAdbLatestPrefilter = (
        (-not $SkipLatestRebalancePrefilter) -and
        (-not $directRan) -and
        $selectedProbeIds.Count -gt 0 -and
        $HistoryMode -ne "none" -and
        $AdbRebalanceFallbackMode -ne "none"
    )
    $needsAdbStages = ($detailDriveIds.Count -gt 0 -or $currentHoldingDriveIds.Count -gt 0 -or $historyDriveIds.Count -gt 0 -or $needsAdbLatestPrefilter)
    $deviceCacheSyncState = $(if ($needsAdbStages) { "pending" } else { "not_needed" })
    $canSyncDeviceCache = $needsAdbStages
    $skippedDeviceStages = @()
    if ($needsAdbStages) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $deviceOutput = & $AdbExe "-s" $DeviceId "get-state" 2>&1
            $deviceExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $deviceState = ($deviceOutput | Out-String).Trim()
        if ($deviceExitCode -ne 0 -or $deviceState -ne "device") {
            $canSyncDeviceCache = $false
            $deviceCacheSyncState = "skipped_device_unavailable"
            Write-Status -Stage "device_cache" -State "skipped" -Message "ADB device unavailable; continue with public quote collection only"
        }
        else {
            $deviceCacheSyncState = "available"
            Write-Status -Stage "device_cache" -State "available" -Message ("physical phone ready={0}" -f $DeviceId)
        }
    }

    if (
        $needsAdbLatestPrefilter -and
        $canSyncDeviceCache -and
        $selectedProbeIds.Count -gt 0
    ) {
        $latestPrefilterRunDir = Join-Path $jobRoot "01_latest_rebalance_prefilter"
        $latestPrefilterProbeFile = Join-Path $latestPrefilterRunDir "strategy_ids.txt"
        $latestPrefilterTargetPath = Join-Path $latestPrefilterRunDir "history_targets.txt"
        $latestPrefilterReportPath = Join-Path $latestPrefilterRunDir "latest_rebalance_prefilter_report.json"
        Write-StrategyFile -Path $latestPrefilterProbeFile -StrategyIds $selectedProbeIds
        try {
            Invoke-Stage -StageName "01_latest_rebalance_prefilter" -CommandArgs @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--strategy-file", $latestPrefilterProbeFile,
                "--skip-history",
                "--max-attempts", "1",
                "--retry-wait-ms", "1000"
            )
            $latestPrefilterRan = $true
            Invoke-Stage -StageName "01_select_rebalance_history_targets" -CommandArgs @(
                ".\节点脚本\_共享组件\生产程序\select_ttfund_rebalance_history_targets.py",
                "--strategy-file", $latestPrefilterProbeFile,
                "--output-file", $latestPrefilterTargetPath,
                "--report-file", $latestPrefilterReportPath
            )
            $prefilterTargetIds = @()
            if (Test-Path -LiteralPath $latestPrefilterTargetPath) {
                $prefilterTargetIds = @(
                    Get-Content -LiteralPath $latestPrefilterTargetPath -Encoding UTF8 |
                        ForEach-Object { [string]$_ } |
                        Where-Object { $_ }
                )
            }
            $historyDriveIds = Join-UniqueStrings -Groups @($requiredHistoryIds, $prefilterTargetIds)
            $latestPrefilterTargetTotal = $prefilterTargetIds.Count
            $latestPrefilterState = "completed_adb_latest_probe"
            Write-Status -Stage "01_select_rebalance_history_targets" -State "completed" -Message ("latest prefilter selected {0} changed strategies; required repair targets {1}; history drive targets {2}" -f $latestPrefilterTargetTotal, $requiredHistoryIds.Count, $historyDriveIds.Count)
        }
        catch {
            $latestPrefilterState = "failed_no_history_delta"
            $historyDriveIds = @($requiredHistoryIds)
            Write-Status -Stage "01_latest_rebalance_prefilter" -State "failed" -Message ("latest rebalance prefilter failed; only required repair targets will be used: {0}" -f $_.Exception.Message)
        }
    }
    elseif ($SkipLatestRebalancePrefilter -and $latestPrefilterState -eq "not_needed") {
        $latestPrefilterState = "disabled"
    }
    elseif ($directRan -and $latestPrefilterState -eq "not_needed") {
        $latestPrefilterState = "skipped_direct_interface_completed"
    }
    elseif ($needsAdbLatestPrefilter -and (-not $canSyncDeviceCache)) {
        $latestPrefilterState = "skipped_device_unavailable"
    }

    if ((-not $DisableDirectInterface) -and (-not $directHistoryAttempted) -and $historyDriveIds.Count -gt 0) {
        $directHistoryRunDir = Join-Path $jobRoot "01_direct_history"
        $historyFile = Join-Path $directHistoryRunDir "strategy_ids.txt"
        Write-StrategyFile -Path $historyFile -StrategyIds $historyDriveIds
        $directHistoryArgs = @(
            ".\节点脚本\_共享组件\生产程序\collect_ttfund_direct_interfaces.py",
            "--strategy-file", $historyFile,
            "--run-dir", $directHistoryRunDir,
            "--workers", "$DirectInterfaceWorkers",
            "--fetch-latest",
            "--skip-detail",
            "--use-builtin-adjust-templates"
        )
        if ($directInterfaceFlow) {
            $directHistoryArgs += @("--flow-path", $directInterfaceFlow)
        }
        try {
            $directHistoryAttempted = $true
            Invoke-Stage -StageName "01_direct_history" -CommandArgs $directHistoryArgs
            $directHistoryRan = $true
            $directHistoryState = "completed_after_adb_latest_probe"
            $directHistorySuccess = Read-DirectInterfaceSuccess -ResultsPath (Join-Path $directHistoryRunDir "results.json")
            $historyDriveIds = @($historyDriveIds | Where-Object { -not $directHistorySuccess.history.ContainsKey([string]$_) })
        }
        catch {
            $directHistoryState = "failed_fallback_to_adb"
            $directHistoryError = $_.Exception.Message
            Write-Status -Stage "01_direct_history" -State "failed" -Message ("direct history failed after ADB latest probe; fallback to ADB where selected: {0}" -f $directHistoryError)
        }
    }

    if ($AdbFallbackLimit -gt 0 -and $historyDriveIds.Count -gt $AdbFallbackLimit) {
        $historyDriveIds = @($historyDriveIds | Select-Object -First $AdbFallbackLimit)
        $adbFallbackTruncated = $true
    }

    # The device unit of work is one strategy, not one stage. A history visit also
    # validates detail/benchmark/current holding, and a holding visit validates detail.
    # Only fields proven complete by the direct interface were removed above.
    $bundleDetailIds = @($detailDriveIds)
    $bundleBenchmarkIds = @($selectedBenchmarkIds | Where-Object { $_ -in $bundleDetailIds })
    $bundleCurrentHoldingIds = @($currentHoldingDriveIds)
    $bundleHistoryIds = @($historyDriveIds)
    $strategyWorkBundleRunDir = Join-Path $jobRoot "01_strategy_work_bundle"
    $strategyWorkBundleFile = Join-Path $strategyWorkBundleRunDir "work_bundles.json"
    $strategyWorkBundleIds = @(
        Write-StrategyWorkBundleFile `
            -Path $strategyWorkBundleFile `
            -DetailIds $bundleDetailIds `
            -BenchmarkIds $bundleBenchmarkIds `
            -CurrentHoldingIds $bundleCurrentHoldingIds `
            -HistoryIds $bundleHistoryIds
    )
    $currentHoldingDriveExecutedTotal = $bundleCurrentHoldingIds.Count
    if ($strategyWorkBundleIds.Count -gt 0 -and $canSyncDeviceCache) {
        $bundleArgs = @(
            ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
            "--adb-path", $AdbExe,
            "--device-id", $DeviceId,
            "--work-bundle-file", $strategyWorkBundleFile,
            "--run-dir", $strategyWorkBundleRunDir,
            "--skip-existing-results",
            "--detail-scan-swipes", "$DetailScanSwipes",
            "--max-attempts", "2",
            "--retry-wait-ms", "2500",
            "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
            "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
            "--capture-failures"
        )
        Invoke-Stage -StageName "01_strategy_work_bundle" -CommandArgs $bundleArgs
        $bundleResultsPath = Join-Path $strategyWorkBundleRunDir "results.json"
        $strategyWorkBundleSummary = Read-JsonObjectOrNull -Path (Join-Path $strategyWorkBundleRunDir "summary.json")
        $detailOnlyPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath $bundleResultsPath -Mode "detail" -ExpectedIds $bundleDetailIds)
        $benchmarkPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath $bundleResultsPath -Mode "benchmark" -ExpectedIds $bundleBenchmarkIds)
        $detailPrimaryFailedIds = @(Join-UniqueStrings -Groups @($detailOnlyPrimaryFailedIds, $benchmarkPrimaryFailedIds))
        $currentHoldingPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath $bundleResultsPath -Mode "current_holding" -ExpectedIds $bundleCurrentHoldingIds)
        $historyPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath $bundleResultsPath -Mode "history" -ExpectedIds $bundleHistoryIds)
        $strategyWorkBundlePrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath $bundleResultsPath -Mode "bundle" -ExpectedIds $strategyWorkBundleIds)
        $detailFinalFailedIds = @($detailPrimaryFailedIds)
        $currentHoldingFinalFailedIds = @($currentHoldingPrimaryFailedIds)
        $historyFinalFailedIds = @($historyPrimaryFailedIds)
        $strategyWorkBundleFinalFailedIds = @($strategyWorkBundlePrimaryFailedIds)

        if ($strategyWorkBundlePrimaryFailedIds.Count -gt 0) {
            $retryControlDir = Join-Path $jobRoot "01_strategy_work_bundle_physical_retry"
            $retryFile = Join-Path $retryControlDir "strategy_ids.txt"
            Write-StrategyFile -Path $retryFile -StrategyIds $strategyWorkBundlePrimaryFailedIds
            $retryArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--strategy-file", $retryFile,
                "--work-bundle-file", $strategyWorkBundleFile,
                "--run-dir", $strategyWorkBundleRunDir,
                "--skip-existing-results",
                "--detail-scan-swipes", "$DetailScanSwipes",
                "--max-attempts", "2",
                "--retry-wait-ms", "5000",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures",
                "--keep-run-cache"
            )
            Write-Host ("[真机补采] 单策略任务包首轮仍有 {0} 个字段不完整对象，继续使用同一真机 {1} 定向补采。" -f $strategyWorkBundlePrimaryFailedIds.Count, $DeviceId)
            $detailPhysicalRetryAttempted = $detailPrimaryFailedIds.Count -gt 0
            $currentHoldingPhysicalRetryAttempted = $currentHoldingPrimaryFailedIds.Count -gt 0
            $historyPhysicalRetryAttempted = $historyPrimaryFailedIds.Count -gt 0
            Invoke-OptionalStage -StageName "01_strategy_work_bundle_physical_retry" -CommandArgs $retryArgs
            $retryResultsPath = Join-Path $strategyWorkBundleRunDir "results.json"
            $retrySummary = Read-JsonObjectOrNull -Path (Join-Path $strategyWorkBundleRunDir "summary.json")
            $strategyWorkBundleSummary = $retrySummary
            $detailPhysicalRetrySummary = $retrySummary
            $currentHoldingPhysicalRetrySummary = $retrySummary
            $historyPhysicalRetrySummary = $retrySummary
            $detailOnlyFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $retryResultsPath -Mode "detail" -ExpectedIds $detailOnlyPrimaryFailedIds)
            $benchmarkFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $retryResultsPath -Mode "benchmark" -ExpectedIds $benchmarkPrimaryFailedIds)
            $detailFinalFailedIds = @(Join-UniqueStrings -Groups @($detailOnlyFinalFailedIds, $benchmarkFinalFailedIds))
            $currentHoldingFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $retryResultsPath -Mode "current_holding" -ExpectedIds $currentHoldingPrimaryFailedIds)
            $historyFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $retryResultsPath -Mode "history" -ExpectedIds $historyPrimaryFailedIds)
            $strategyWorkBundleFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $retryResultsPath -Mode "bundle" -ExpectedIds $strategyWorkBundlePrimaryFailedIds)
        }
    }
    elseif ($strategyWorkBundleIds.Count -gt 0) {
        $skippedDeviceStages += "01_strategy_work_bundle"
        $detailFinalFailedIds = @($bundleDetailIds)
        $currentHoldingFinalFailedIds = @($bundleCurrentHoldingIds)
        $historyFinalFailedIds = @($bundleHistoryIds)
        $strategyWorkBundleFinalFailedIds = @($strategyWorkBundleIds)
    }

    # The legacy per-stage blocks below remain as a compatibility boundary, but
    # receive no ids after the unified bundle stage and therefore cannot reopen a strategy.
    $detailDriveIds = @()
    $currentHoldingDriveIds = @()
    $historyDriveIds = @()

    if ($detailDriveIds.Count -gt 0 -and $canSyncDeviceCache) {
        $detailDriveRunDir = Join-Path $jobRoot "01_detail_drive"
        $detailArgs = @(
            ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
            "--adb-path", $AdbExe,
            "--device-id", $DeviceId,
            "--run-dir", $detailDriveRunDir,
            "--skip-history",
            "--detail-scan-swipes", "$DetailScanSwipes",
            "--max-attempts", "2",
            "--retry-wait-ms", "2500",
            "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
            "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
            "--capture-failures"
        )
        foreach ($strategyId in $detailDriveIds) {
            $detailArgs += @("--strategy-id", [string]$strategyId)
        }
        Invoke-Stage -StageName "01_detail_drive" -CommandArgs $detailArgs
        $detailDriveSummary = Read-JsonObjectOrNull -Path (Join-Path $detailDriveRunDir "summary.json")
        $detailPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath (Join-Path $detailDriveRunDir "results.json") -Mode "detail" -ExpectedIds $detailDriveIds)
        $detailFinalFailedIds = @($detailPrimaryFailedIds)
        if ($detailPrimaryFailedIds.Count -gt 0) {
            $physicalRunDir = Join-Path $jobRoot "01_detail_drive_physical_retry"
            $physicalFile = Join-Path $physicalRunDir "strategy_ids.txt"
            Write-StrategyFile -Path $physicalFile -StrategyIds $detailPrimaryFailedIds
            $physicalArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--strategy-file", $physicalFile,
                "--run-dir", $physicalRunDir,
                "--skip-history",
                "--detail-scan-swipes", "$DetailScanSwipes",
                "--max-attempts", "2",
                "--retry-wait-ms", "5000",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures",
                "--keep-run-cache"
            )
            Write-Host ("[真机补采] 策略详情首轮仍失败 {0} 个，继续使用同一真机 {1} 做第二批次补采。" -f $detailPrimaryFailedIds.Count, $DeviceId)
            $detailPhysicalRetryAttempted = $true
            Invoke-OptionalStage -StageName "01_detail_drive_physical_retry" -CommandArgs $physicalArgs
            $detailPhysicalRetrySummary = Read-JsonObjectOrNull -Path (Join-Path $physicalRunDir "summary.json")
            $physicalResultsPath = Join-Path $physicalRunDir "results.json"
            $detailFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $physicalResultsPath -Mode "detail" -ExpectedIds $detailPrimaryFailedIds)
        }
    }
    elseif ($detailDriveIds.Count -gt 0) {
        $skippedDeviceStages += "01_detail_drive"
    }

    if ($currentHoldingDriveIds.Count -gt 0 -and $canSyncDeviceCache) {
        $currentHoldingDriveExecutedTotal = $currentHoldingDriveIds.Count
        $currentHoldingRunDir = Join-Path $jobRoot "01_current_holding_drive"
        $currentHoldingScanSwipes = [Math]::Min([int]$DetailScanSwipes, 1)
        if ($currentHoldingDevices.Count -gt 1) {
            $currentHoldingStrategyFile = Join-Path $currentHoldingRunDir "strategy_ids.txt"
            Write-StrategyFile -Path $currentHoldingStrategyFile -StrategyIds $currentHoldingDriveIds
            $currentHoldingArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app_sharded.py",
                "--adb-path", $AdbExe,
                "--strategy-file", $currentHoldingStrategyFile,
                "--run-dir", $currentHoldingRunDir,
                "--python-exe", $PythonExe,
                "--detail-scan-swipes", "$currentHoldingScanSwipes",
                "--max-attempts", "2",
                "--retry-wait-ms", "2500",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures"
            )
            foreach ($currentHoldingDevice in $currentHoldingDevices) {
                $currentHoldingArgs += @("--device", [string]$currentHoldingDevice)
            }
        }
        else {
            $currentHoldingArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--run-dir", $currentHoldingRunDir,
                "--skip-history",
                "--current-holding-fast",
                "--detail-scan-swipes", "$currentHoldingScanSwipes",
                "--max-attempts", "2",
                "--retry-wait-ms", "2500",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures"
            )
            foreach ($strategyId in $currentHoldingDriveIds) {
                $currentHoldingArgs += @("--strategy-id", [string]$strategyId)
            }
        }
        Invoke-Stage -StageName "01_current_holding_drive" -CommandArgs $currentHoldingArgs
        $currentHoldingDriveSummary = Read-JsonObjectOrNull -Path (Join-Path $currentHoldingRunDir "summary.json")
        $currentHoldingPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath (Join-Path $currentHoldingRunDir "results.json") -Mode "current_holding" -ExpectedIds $currentHoldingDriveIds)
        $currentHoldingFinalFailedIds = @($currentHoldingPrimaryFailedIds)
        if ($currentHoldingPrimaryFailedIds.Count -gt 0) {
            $physicalRunDir = Join-Path $jobRoot "01_current_holding_drive_physical_retry"
            $physicalFile = Join-Path $physicalRunDir "strategy_ids.txt"
            Write-StrategyFile -Path $physicalFile -StrategyIds $currentHoldingPrimaryFailedIds
            $physicalArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--strategy-file", $physicalFile,
                "--run-dir", $physicalRunDir,
                "--skip-history",
                "--current-holding-fast",
                "--detail-scan-swipes", "$currentHoldingScanSwipes",
                "--max-attempts", "2",
                "--retry-wait-ms", "5000",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures",
                "--keep-run-cache"
            )
            Write-Host ("[真机补采] 当前仓位首轮仍失败 {0} 个，继续使用同一真机 {1} 做第二批次补采。" -f $currentHoldingPrimaryFailedIds.Count, $DeviceId)
            $currentHoldingPhysicalRetryAttempted = $true
            Invoke-OptionalStage -StageName "01_current_holding_drive_physical_retry" -CommandArgs $physicalArgs
            $currentHoldingPhysicalRetrySummary = Read-JsonObjectOrNull -Path (Join-Path $physicalRunDir "summary.json")
            $physicalResultsPath = Join-Path $physicalRunDir "results.json"
            $currentHoldingFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $physicalResultsPath -Mode "current_holding" -ExpectedIds $currentHoldingPrimaryFailedIds)
        }
    }
    elseif ($currentHoldingDriveIds.Count -gt 0) {
        $skippedDeviceStages += "01_current_holding_drive"
    }

    if ($historyDriveIds.Count -gt 0 -and $canSyncDeviceCache) {
        $historyDriveRunDir = Join-Path $jobRoot "02_history_drive"
        $historyArgs = @(
            ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
            "--adb-path", $AdbExe,
            "--device-id", $DeviceId,
            "--run-dir", $historyDriveRunDir,
            "--max-attempts", "2",
            "--retry-wait-ms", "2500",
            "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
            "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
            "--capture-failures"
        )
        foreach ($strategyId in $historyDriveIds) {
            $historyArgs += @("--strategy-id", [string]$strategyId)
        }
        Invoke-Stage -StageName "02_history_drive" -CommandArgs $historyArgs
        $historyDriveSummary = Read-JsonObjectOrNull -Path (Join-Path $historyDriveRunDir "summary.json")
        $historyPrimaryFailedIds = @(Get-DriveFailureIds -ResultsPath (Join-Path $historyDriveRunDir "results.json") -Mode "history" -ExpectedIds $historyDriveIds)
        $historyFinalFailedIds = @($historyPrimaryFailedIds)
        if ($historyPrimaryFailedIds.Count -gt 0) {
            $physicalRunDir = Join-Path $jobRoot "02_history_drive_physical_retry"
            $physicalFile = Join-Path $physicalRunDir "strategy_ids.txt"
            Write-StrategyFile -Path $physicalFile -StrategyIds $historyPrimaryFailedIds
            $physicalArgs = @(
                ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
                "--adb-path", $AdbExe,
                "--device-id", $DeviceId,
                "--strategy-file", $physicalFile,
                "--run-dir", $physicalRunDir,
                "--max-attempts", "2",
                "--retry-wait-ms", "5000",
                "--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold",
                "--soft-circuit-break-max-recoveries", "$DeviceFailureCircuitRecoveryLimit",
                "--capture-failures",
                "--keep-run-cache"
            )
            Write-Host ("[真机补采] 调仓历史首轮仍失败 {0} 个，继续使用同一真机 {1} 做第二批次补采。" -f $historyPrimaryFailedIds.Count, $DeviceId)
            $historyPhysicalRetryAttempted = $true
            Invoke-OptionalStage -StageName "02_history_drive_physical_retry" -CommandArgs $physicalArgs
            $historyPhysicalRetrySummary = Read-JsonObjectOrNull -Path (Join-Path $physicalRunDir "summary.json")
            $physicalResultsPath = Join-Path $physicalRunDir "results.json"
            $historyFinalFailedIds = @(Get-OptionalDriveFailureIds -ResultsPath $physicalResultsPath -Mode "history" -ExpectedIds $historyPrimaryFailedIds)
        }
    }
    elseif ($historyDriveIds.Count -gt 0) {
        $skippedDeviceStages += "02_history_drive"
    }

    $shouldRunCollect = ([bool]$plan.actions.should_collect -or $directRan -or $directHistoryRan -or $strategyWorkBundleIds.Count -gt 0 -or $detailDriveIds.Count -gt 0 -or $currentHoldingDriveIds.Count -gt 0 -or $historyDriveIds.Count -gt 0)
    if ($shouldRunCollect) {
        $collectRunId = "${runId}__collect"
        $collectArgs = @(
            ".\节点脚本\_共享组件\生产程序\collect_ttfund_loggedin.py",
            "--device-id", $DeviceId,
            "--adb-path", $AdbExe,
            "--quote-batch-size", "$QuoteBatchSize",
            "--run-id", $collectRunId
        )
        $collectArgs += "--no-sync-device-cache"
        Invoke-Stage -StageName "03_collect" -CommandArgs $collectArgs
    }

    if ($collectRunId -and (-not $SkipOfficialPerformanceCurve)) {
        $officialCurveArgs = @(
            ".\节点脚本\_共享组件\生产程序\collect_ttfund_official_performance_curve.py",
            "--run-id", $collectRunId,
            "--workers", "$OfficialCurveWorkers",
            "--retries", "$OfficialCurveRetries",
            "--retry-failed-rounds", "1",
            "--auto-incremental",
            "--overlap-days", "3",
            "--full-history-gap-days", "4",
            "--catalog-manifest-path", $catalogManifestPath,
            "--merge-existing-run"
        )
        if ($targetTradeDate) {
            $officialCurveArgs += @("--expected-latest-date", $targetTradeDate)
        }
        Invoke-Stage -StageName "03b_official_performance_curve" -CommandArgs $officialCurveArgs
        $officialCurveDay = $day
        if ($collectRunId -match "^(\d{4})(\d{2})(\d{2})") {
            $officialCurveDay = "{0}-{1}-{2}" -f $Matches[1], $Matches[2], $Matches[3]
        }
        $officialCurveSummaryPath = Join-Path $ProjectRoot (
            "outputs\ttfund_official_performance_curve\{0}\{1}\official_curve_summary.json" -f $officialCurveDay, $collectRunId
        )
        if (-not (Test-Path -LiteralPath $officialCurveSummaryPath)) {
            $officialCurveSummaryFile = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "outputs\ttfund_official_performance_curve") `
                -Recurse -Filter "official_curve_summary.json" -ErrorAction SilentlyContinue |
                Where-Object { $_.Directory.Name -eq $collectRunId } |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($officialCurveSummaryFile) {
                $officialCurveSummaryPath = $officialCurveSummaryFile.FullName
            }
        }
        if (Test-Path -LiteralPath $officialCurveSummaryPath) {
            $officialCurveSummary = Read-JsonObjectOrNull -Path $officialCurveSummaryPath
        }
        if ($canSyncDeviceCache -and (Test-Path -LiteralPath $officialCurveSummaryPath)) {
            $officialCurveDeviceRetryRunDir = Join-Path $jobRoot "03b_official_curve_device_retry"
            $officialCurveRetryArgs = @(
                ".\节点脚本\_共享组件\生产程序\retry_ttfund_official_curve_gaps.py",
                "--summary-path", $officialCurveSummaryPath,
                "--adb-path", $AdbExe,
                "--primary-device-id", $DeviceId,
                "--run-dir", $officialCurveDeviceRetryRunDir,
                "--python-exe", $PythonExe
            )
            Invoke-OptionalStage -StageName "03c_official_curve_gap_retry" -CommandArgs $officialCurveRetryArgs
            $officialCurveDeviceRetrySummaryPath = Join-Path $officialCurveDeviceRetryRunDir "official_curve_device_retry_summary.json"
            if (-not (Test-Path -LiteralPath $officialCurveDeviceRetrySummaryPath)) {
                $officialCurveDeviceRetrySummaryPath = $null
            }
        }
        $officialCurveState = "completed"
    }
    elseif ($SkipOfficialPerformanceCurve) {
        $officialCurveState = "skipped"
    }

    $shouldRunPostQuality = (
        (-not $SkipQuality) -and (
            $shouldRunCollect -or
            $directRan -or
            $directHistoryRan -or
            $detailDriveIds.Count -gt 0 -or
            $currentHoldingDriveIds.Count -gt 0 -or
            $historyDriveIds.Count -gt 0 -or
            $deployNeedsExport -or
            $shouldRunPublicDataUpdate
        )
    )
    $deployOnlyPost = ($deployNeedsExport -and (-not $shouldRunCollect) -and (-not $collectRunId))
    $publicDataOnlyPost = ($shouldRunPublicDataUpdate -and (-not $shouldRunCollect) -and (-not $collectRunId))
    $qualityState = "skipped_no_work"
    $qualityMode = "no_incremental_work"
    $qualityLogPath = $null
    $deployExportState = "skipped"
    $deployAuditState = "skipped"
    $fundNavRefreshState = "skipped"
    $indexQuoteUpdateState = "skipped"
    if ($SkipQuality) {
        $qualityState = "skipped"
        $qualityMode = "skipped"
    }

    if ($shouldRunPostQuality) {
        $postArgs = @(
            ".\节点脚本\_共享组件\生产程序\run_ttfund_post_update_quality.py",
            "--algorithm-version", $AlgorithmVersion,
            "--fund-nav-workers", "$FundNavWorkers",
            "--fund-nav-incremental-days", "$FundNavIncrementalDays"
        )
        if ($targetTradeDate) {
            $postArgs += @("--target-trade-date", $targetTradeDate)
        }
        if ($collectRunId) {
            $postArgs += @("--incremental-run-id", $collectRunId)
        }
        if ($DeploySiteDir) {
            $postArgs += @("--deploy-site-dir", $DeploySiteDir)
            $postArgs += @("--deploy-page-set", $DeployPageSet)
        }
        if ($SkipDeployExport) {
            $postArgs += "--skip-deploy-export"
        }
        if ($fastIncrementalQuality) {
            $postArgs += @("--fast-incremental", "--lightweight")
        }
        if ($SkipLoadAnalysis -or $deployOnlyPost -or $publicDataOnlyPost) {
            $postArgs += "--skip-load-analysis"
        }
        if ($SkipDashboardExport) {
            $postArgs += "--skip-dashboard-export"
        }
        if ($SkipFundNavRefresh) {
            $postArgs += "--skip-fund-nav-refresh"
        }
        if ($FullFundNavRefresh) {
            $postArgs += "--full-fund-nav-refresh"
        }
        if ($SkipIndexQuoteUpdate) {
            $postArgs += "--skip-index-quote-update"
        }
        Invoke-Stage -StageName "04_post_update_quality" -CommandArgs $postArgs
        $qualityState = "completed"
        $qualityMode = $(if ($fastIncrementalQuality) { "lightweight_incremental" } else { "full_quality" })
        $qualityLogPath = Join-Path $jobRoot "04_post_update_quality.log"
        $deployExportState = $(if ($SkipDeployExport -or -not $DeploySiteDir) { "skipped" } else { "completed" })
        $deployAuditState = $(if ($SkipDeployExport -or -not $DeploySiteDir) { "skipped" } else { "completed_in_post_quality" })
        $fundNavRefreshState = $(if ($SkipFundNavRefresh) { "skipped" } else { "conditional" })
        $indexQuoteUpdateState = $(if ($SkipIndexQuoteUpdate) { "skipped" } else { "conditional" })
    }
    elseif ((-not $SkipQuality) -and $DeploySiteDir -and (-not $SkipDeployExport)) {
        $auditArgs = @(
            ".\节点脚本\_共享组件\生产程序\audit_basic_data_deploy_integrity.py",
            "--report-root", $DeploySiteDir
        )
        Invoke-Stage -StageName "04_audit_basic_data_deploy_integrity" -CommandArgs $auditArgs
        $deployAuditState = "completed"
    }

    $softGapTotal = (
        $strategyWorkBundleFinalFailedIds.Count +
        [int](Get-JsonIntProperty -Object $officialCurveSummary -Name "missing_strategy_total")
    )
    $summaryState = if ($softGapTotal -gt 0) { "completed_with_warning" } else { "completed" }
    $summary = [ordered]@{
        finished_at = (Get-Date).ToString("s")
        state = $summaryState
        device_id = $DeviceId
        device_mode = "physical_only"
        retry_device_id = $DeviceId
        device_retry_policy = "physical_max_attempts_2_then_same_phone_failed_ids_second_pass"
        fund_nav_retry_policy = "public_api_primary_then_low_concurrency_retry_twice"
        history_mode = $HistoryMode
        algorithm_version = $AlgorithmVersion
        job_root = $jobRoot
        plan_path = $planPath
        quality_state = $qualityState
        quality_mode = $qualityMode
        quality_log_path = $qualityLogPath
        deploy_site_dir = $DeploySiteDir
        deploy_page_set = $DeployPageSet
        deploy_export_state = $deployExportState
        deploy_integrity_audit_state = $deployAuditState
        discovery_cache_sync_state = $discoverySyncState
        direct_interface_state = $directInterfaceState
        direct_interface_flow = $directInterfaceFlow
        direct_interface_run_dir = $directInterfaceRunDir
        direct_interface_error = $directInterfaceError
        direct_interface_probe_total = [int]$plan.selection.selected_rebalance_probe_total
        direct_interface_detail_target_total = ($selectedDetailIds.Count + $selectedCurrentHoldingIds.Count)
        direct_history_state = $directHistoryState
        direct_history_run_dir = $directHistoryRunDir
        direct_history_error = $directHistoryError
        direct_history_ran = $directHistoryRan
        latest_rebalance_prefilter_state = $latestPrefilterState
        latest_rebalance_prefilter_run_dir = $latestPrefilterRunDir
        latest_rebalance_prefilter_report_path = $latestPrefilterReportPath
        latest_rebalance_prefilter_target_path = $latestPrefilterTargetPath
        latest_rebalance_prefilter_target_total = $latestPrefilterTargetTotal
        latest_rebalance_prefilter_ran = $latestPrefilterRan
        fund_nav_refresh_state = $fundNavRefreshState
        fund_nav_workers = $FundNavWorkers
        fund_nav_incremental_days = $(if ($FullFundNavRefresh) { $null } else { $FundNavIncrementalDays })
        fund_nav_refresh_mode = $(if ($FullFundNavRefresh) { "full_history" } else { "per_fund_incremental_from_existing" })
        index_quote_update_state = $indexQuoteUpdateState
        public_data_update_state = $(if ($SkipQuality) { "skipped" } else { "conditional" })
        should_run_detail_drive = [bool]$plan.actions.should_run_detail_drive
        should_run_current_holding_drive = [bool]$plan.actions.should_run_current_holding_drive
        should_run_history_drive = [bool]$plan.actions.should_run_history_drive
        should_run_direct_rebalance_probe = [bool]$plan.actions.should_run_direct_rebalance_probe
        should_collect = $shouldRunCollect
        should_sync_device_cache = $needsAdbStages
        device_cache_sync_state = $deviceCacheSyncState
        skipped_device_stages = $skippedDeviceStages
        selected_detail_total = [int]$plan.selection.selected_detail_total
        detail_cooldown_days = [int]$plan.selection.detail_cooldown_days
        stopped_detail_cooldown_days = [int]$plan.selection.stopped_detail_cooldown_days
        stale_detail_total = [int]$plan.selection.stale_detail_total
        stale_active_detail_total = [int]$plan.selection.stale_active_detail_total
        stale_stopped_detail_total = [int]$plan.selection.stale_stopped_detail_total
        mandatory_detail_total = [int]$plan.selection.mandatory_detail_total
        routine_detail_selected_total = [int]$plan.selection.routine_detail_selected_total
        routine_detail_piggyback_total = [int]$plan.selection.routine_detail_piggyback_total
        routine_detail_deferred_total = [int]$plan.selection.routine_detail_deferred_total
        detail_refresh_total = [int]$plan.selection.detail_refresh_total
        current_holding_cooldown_days = [int]$plan.selection.current_holding_cooldown_days
        stale_current_holding_total = [int]$plan.selection.stale_current_holding_total
        selected_current_holding_total = [int]$plan.selection.selected_current_holding_total
        definitively_stopped_current_holding_skipped_total = [int]$plan.selection.definitively_stopped_current_holding_skipped_total
        stopped_but_refreshable_total = [int]$plan.selection.stopped_but_refreshable_total
        current_holding_lifecycle_reason_counts = $plan.selection.current_holding_lifecycle_reason_counts
        benchmark_detail_repair_total = [int]$plan.selection.benchmark_detail_repair_total
        strategy_work_bundle_run_dir = $strategyWorkBundleRunDir
        strategy_work_bundle_file = $strategyWorkBundleFile
        strategy_work_bundle_total = $strategyWorkBundleIds.Count
        strategy_work_bundle_primary_failed_total = $strategyWorkBundlePrimaryFailedIds.Count
        strategy_work_bundle_primary_failed_ids = $strategyWorkBundlePrimaryFailedIds
        strategy_work_bundle_final_failed_total = $strategyWorkBundleFinalFailedIds.Count
        strategy_work_bundle_final_failed_ids = $strategyWorkBundleFinalFailedIds
        strategy_work_bundle_reused_checkpoint_total = (Get-JsonIntProperty -Object $strategyWorkBundleSummary -Name "reused_checkpoint_total")
        adb_detail_drive_total = $bundleDetailIds.Count
        adb_current_holding_planned_total = $currentHoldingDrivePlannedTotal
        adb_current_holding_drive_total = $currentHoldingDriveExecutedTotal
        adb_current_holding_device_total = $currentHoldingDevices.Count
        adb_current_holding_device_ids = $currentHoldingDevices
        adb_current_holding_unavailable_device_ids = $currentHoldingUnavailableDevices
        adb_detail_drive_detail_missing_total = $detailFinalFailedIds.Count
        adb_current_holding_detail_missing_total = $currentHoldingFinalFailedIds.Count
        adb_history_drive_detail_missing_total = $historyFinalFailedIds.Count
        adb_detail_source_unavailable_total = (Get-JsonIntProperty -Object $strategyWorkBundleSummary -Name "source_unavailable_total")
        adb_current_holding_source_unavailable_total = (Get-JsonIntProperty -Object $strategyWorkBundleSummary -Name "source_unavailable_total")
        adb_history_source_unavailable_total = (Get-JsonIntProperty -Object $strategyWorkBundleSummary -Name "source_unavailable_total")
        adb_source_unavailable_ids = (ConvertTo-StringArray $strategyWorkBundleSummary.source_unavailable_ids)
        selected_history_total = [int]$plan.selection.selected_history_total
        selected_rebalance_probe_total = [int]$plan.selection.selected_rebalance_probe_total
        stale_history_total = [int]$plan.selection.stale_history_total
        stale_history_fallback_total = [int]$plan.selection.stale_history_fallback_total
        adb_history_drive_total = $bundleHistoryIds.Count
        fallback_device_id = $null
        detail_primary_failed_total = $detailPrimaryFailedIds.Count
        detail_primary_failed_ids = $detailPrimaryFailedIds
        detail_physical_retry_total = $(if ($detailPhysicalRetryAttempted) { $detailPrimaryFailedIds.Count } else { 0 })
        detail_physical_retry_success_total = [Math]::Max(0, $detailPrimaryFailedIds.Count - $detailFinalFailedIds.Count)
        detail_final_failed_total = $detailFinalFailedIds.Count
        detail_final_failed_ids = $detailFinalFailedIds
        current_holding_primary_failed_total = $currentHoldingPrimaryFailedIds.Count
        current_holding_primary_failed_ids = $currentHoldingPrimaryFailedIds
        current_holding_physical_retry_total = $(if ($currentHoldingPhysicalRetryAttempted) { $currentHoldingPrimaryFailedIds.Count } else { 0 })
        current_holding_physical_retry_success_total = [Math]::Max(0, $currentHoldingPrimaryFailedIds.Count - $currentHoldingFinalFailedIds.Count)
        current_holding_final_failed_total = $currentHoldingFinalFailedIds.Count
        current_holding_final_failed_ids = $currentHoldingFinalFailedIds
        history_primary_failed_total = $historyPrimaryFailedIds.Count
        history_primary_failed_ids = $historyPrimaryFailedIds
        history_physical_retry_total = $(if ($historyPhysicalRetryAttempted) { $historyPrimaryFailedIds.Count } else { 0 })
        history_physical_retry_success_total = [Math]::Max(0, $historyPrimaryFailedIds.Count - $historyFinalFailedIds.Count)
        history_final_failed_total = $historyFinalFailedIds.Count
        history_final_failed_ids = $historyFinalFailedIds
        fallback_device_unavailable_stages = @()
        adb_fallback_limit = $AdbFallbackLimit
        adb_fallback_truncated = $adbFallbackTruncated
        estimate_total_minutes = $plan.estimates.total_minutes
        latest_local_trade_date = $plan.local_baseline.latest_trade_date
        remote_max_trade_date = $plan.remote_probe.max_trade_date
        new_strategy_total = [int]$plan.local_baseline.new_strategy_total
        cache_discovered_new_total = [int]$plan.local_baseline.cache_discovered_new_total
        target_trade_date = $targetTradeDate
        no_new_trade_date = $noNewTradeDate
        deploy_basic_data_date = $deployBasicDataDate
        deploy_needs_export = $deployNeedsExport
        collect_run_id = $collectRunId
        official_curve_state = $officialCurveState
        official_curve_summary_path = $officialCurveSummaryPath
        official_curve_device_retry_summary_path = $officialCurveDeviceRetrySummaryPath
        official_curve_missing_strategy_total = [int](Get-JsonIntProperty -Object $officialCurveSummary -Name "missing_strategy_total")
        official_curve_workers = $OfficialCurveWorkers
        deploy_only_post = $deployOnlyPost
        soft_gap_total = $softGapTotal
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $summaryPath
    Write-Status -Stage "done" -State $summaryState -Message "incremental update finished"
}
catch {
    $message = $_.Exception.Message
    $errorDetail = ($_ | Out-String).Trim()
    $failedOfficialCurveSummary = $null
    if ($script:CurrentStage -eq "03b_official_performance_curve" -and $collectRunId) {
        $failedOfficialCurveSummaryFile = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "outputs\ttfund_official_performance_curve") `
            -Recurse -Filter "official_curve_summary.json" -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -eq $collectRunId } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($failedOfficialCurveSummaryFile) {
            $failedOfficialCurveSummary = Read-JsonObjectOrNull -Path $failedOfficialCurveSummaryFile.FullName
            $officialCurveSummaryPath = $failedOfficialCurveSummaryFile.FullName
        }
    }
    $failureClass = if ($failedOfficialCurveSummary -and $failedOfficialCurveSummary.failure_class) {
        [string]$failedOfficialCurveSummary.failure_class
    }
    elseif ($script:CurrentStage -match "device_cache|strategy_work_bundle|detail_drive|current_holding_drive|history_drive") {
        "device_or_login"
    }
    elseif ($script:CurrentStage -match "direct_interface|direct_history|03_collect") {
        "upstream_or_collection"
    }
    else {
        "program_error"
    }
    $summary = [ordered]@{
        finished_at = (Get-Date).ToString("s")
        state = "failed"
        device_id = $DeviceId
        history_mode = $HistoryMode
        job_root = $jobRoot
        failed_stage = $script:CurrentStage
        failed_stage_log_path = $script:CurrentStageLogPath
        failed_stage_exit_code = $script:CurrentStageExitCode
        failure_class = $failureClass
        error = $message
        error_detail = $errorDetail
        plan_path = $planPath
        status_path = $statusPath
        collect_run_id = $collectRunId
        official_curve_state = if ($failedOfficialCurveSummary) { $failedOfficialCurveSummary.state } else { $officialCurveState }
        official_curve_summary_path = $officialCurveSummaryPath
        official_curve_device_retry_summary_path = $officialCurveDeviceRetrySummaryPath
        official_curve_source_effective_date = if ($failedOfficialCurveSummary) { $failedOfficialCurveSummary.source_effective_date } else { $null }
        official_curve_source_lag_business_days = if ($failedOfficialCurveSummary) { $failedOfficialCurveSummary.source_lag_business_days } else { $null }
        latest_local_trade_date = if ($plan) { $plan.local_baseline.latest_trade_date } else { $null }
        remote_max_trade_date = if ($plan) { $plan.remote_probe.max_trade_date } else { $null }
        target_trade_date = $targetTradeDate
        selected_detail_total = if ($plan) { $plan.selection.selected_detail_total } else { $null }
        selected_current_holding_total = if ($plan) { $plan.selection.selected_current_holding_total } else { $null }
        selected_rebalance_probe_total = if ($plan) { $plan.selection.selected_rebalance_probe_total } else { $null }
        selected_history_total = if ($plan) { $plan.selection.selected_history_total } else { $null }
        direct_interface_state = $directInterfaceState
        direct_interface_run_dir = $directInterfaceRunDir
        direct_history_state = $directHistoryState
        direct_history_run_dir = $directHistoryRunDir
        device_mode = "physical_only"
        retry_device_id = $DeviceId
        fallback_device_id = $null
        detail_physical_retry_attempted = $detailPhysicalRetryAttempted
        current_holding_physical_retry_attempted = $currentHoldingPhysicalRetryAttempted
        history_physical_retry_attempted = $historyPhysicalRetryAttempted
        fallback_device_unavailable_stages = @()
        detail_primary_failed_total = $detailPrimaryFailedIds.Count
        detail_final_failed_total = $detailFinalFailedIds.Count
        current_holding_primary_failed_total = $currentHoldingPrimaryFailedIds.Count
        current_holding_final_failed_total = $currentHoldingFinalFailedIds.Count
        history_primary_failed_total = $historyPrimaryFailedIds.Count
        history_final_failed_total = $historyFinalFailedIds.Count
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $summaryPath
    Write-Status -Stage "failed" -State "failed" -Message $message
    throw
}
