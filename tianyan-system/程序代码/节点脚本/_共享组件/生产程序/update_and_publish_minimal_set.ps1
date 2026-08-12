param(
    [string]$DeviceId = "",
    [ValidateSet("", "latest_only", "all_missing", "none")]
    [string]$HistoryMode = "",
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)))),
    [string]$ReportRoot = "",
    [string]$PublishRoot = "",
    [string]$PagesBaseUrl = "https://mao-70r7.github.io/invest",
    [string]$CommitMessage = "",
    [string]$ExtraIncrementalArgs = "",
    [int]$SmokeTestPort = 7791,
    [int]$WaitPagesSeconds = 1200,
    [string]$RunDirectory = "",
    [switch]$SkipDataUpdate,
    [switch]$SkipAudit,
    [switch]$SkipPagesVerify,
    [switch]$SkipPush,
    [switch]$NoSmokeTest,
    [switch]$AllowDirtyPublishRepo
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$script:ProductionProgramRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$script:NodeRoot = (Resolve-Path -LiteralPath (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))).Path
$script:MonthlyReportDate = (Get-Date -Day 1).AddMonths(-1)
$script:MonthlyReportCompact = $script:MonthlyReportDate.ToString("yyyyMM")
$script:MonthlyReportPageName = "monthly-rebalance-report-$($script:MonthlyReportCompact).html"
$script:MonthlyReportAssetDirectoryName = "monthly-rebalance-report-$($script:MonthlyReportCompact)"

function Join-ArgumentLine {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') {
            '"' + ($_.Replace('"', '\"')) + '"'
        }
        else {
            $_
        }
    }) -join " ")
}

function Split-ArgumentLine {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @()
    }
    return @($Text -split "\s+" | Where-Object { $_ })
}

function Resolve-NodeScript {
    param([Parameter(Mandatory = $true)][string]$LeafName)
    $matches = @(
        Get-ChildItem -LiteralPath $script:NodeRoot -Recurse -File -Filter $LeafName |
            Select-Object -ExpandProperty FullName
    )
    if ($matches.Count -ne 1) {
        throw "Expected exactly one node script named '$LeafName' under '$($script:NodeRoot)', found $($matches.Count)."
    }
    return $matches[0]
}

function Get-DefaultPlatformRoot {
    if ($env:ADVISOR_REPORT_ROOT) {
        return $env:ADVISOR_REPORT_ROOT
    }
    return (Join-Path $ProjectRoot "site")
}

function Get-MinimalPublishLeaf {
    return -join ([char[]](0x6700, 0x5C0F, 0x53D1, 0x5E03, 0x96C6))
}

function Get-MonthlyReportTitle {
    # Keep Chinese validation text encoding-safe under Windows PowerShell 5.1.
    $suffix = -join ([char[]](
        0x57FA, 0x91D1, 0x6295, 0x987E, 0x4EA7, 0x54C1,
        0x8C03, 0x4ED3, 0x5206, 0x6790
    ))
    return ("{0}{1}{2}{3}{4}" -f $script:MonthlyReportDate.Year, [char]0x5E74, $script:MonthlyReportDate.Month, [char]0x6708, $suffix)
}

function Write-RunLog {
    param([string]$Text = "")
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text
    Write-Host $line
    Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    Write-RunLog ""
    Write-RunLog ("========== {0} ==========" -f $Name)
    $started = Get-Date
    try {
        & $Body
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        Write-RunLog ("[OK] {0} elapsed_seconds={1}" -f $Name, $elapsed)
    }
    catch {
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        Write-RunLog ("[FAILED] {0} elapsed_seconds={1}" -f $Name, $elapsed)
        Write-RunLog ("[ERROR] {0}" -f $_.Exception.Message)
        throw
    }
}

function Invoke-ExternalCommand {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = $script:ProjectRoot
    )
    Write-RunLog ("[CMD] {0} {1}" -f $FilePath, (Join-ArgumentLine -Arguments $ArgumentList))
    Push-Location $WorkingDirectory
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $global:LASTEXITCODE = 0
        # Native tools such as git may write warnings to stderr while exiting 0.
        # Keep logging stderr, but decide success strictly from LASTEXITCODE.
        $ErrorActionPreference = "Continue"
        & $FilePath @ArgumentList 2>&1 | ForEach-Object {
            Write-RunLog ([string]$_)
        }
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) {
            $exitCode = 0
        }
        if ([int]$exitCode -ne 0) {
            throw "$Name failed with exit code $exitCode"
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
}

function Read-Json {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing JSON file: $Path"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Invoke-Git {
    param([string[]]$GitArgs)
    Invoke-ExternalCommand -Name "git" -FilePath "git" -ArgumentList $GitArgs -WorkingDirectory $script:PublishRoot
}

function Get-GitOutput {
    param([string[]]$GitArgs)
    Push-Location $script:PublishRoot
    try {
        $output = & git @GitArgs 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "git $($GitArgs -join ' ') failed: $($output | Out-String)"
        }
        return @($output | ForEach-Object { ([string]$_).TrimEnd() })
    }
    finally {
        Pop-Location
    }
}

function Assert-PublishPackage {
    $validationPath = Join-Path $script:PublishRoot "package_validation.json"
    $manifestPath = Join-Path $script:PublishRoot "deployment_manifest.json"
    $versionPath = Join-Path $script:PublishRoot "version.json"
    $validation = Read-Json -Path $validationPath
    $manifest = Read-Json -Path $manifestPath
    $version = Read-Json -Path $versionPath

    if ($validation.status -ne "ready") {
        throw "package_validation status is not ready: $($validation.status)"
    }
    $checks = $validation.checks
    $requiredZero = if ($validation.policy -and $validation.policy.blockingZeroChecks) {
        @($validation.policy.blockingZeroChecks)
    }
    else {
        @(
            "strategyDetailMissingCount",
            "strategyDetailParseErrorCount",
            "brokenFundDetailCount",
            "fundDetailParseErrorCount",
            "fundDetailChartScaleErrorCount",
            "currentHoldingScaleErrorReferenceCount"
        )
    }
    foreach ($name in $requiredZero) {
        $value = [int]($checks.$name)
        if ($value -ne 0) {
            throw "package validation failed: $name=$value"
        }
    }
    $warningOnly = if ($validation.policy -and $validation.policy.warningOnlyChecks) {
        @($validation.policy.warningOnlyChecks)
    }
    else {
        @("activeCurrentHoldingRankMissingReferenceCount")
    }
    $script:PackageWarnings = [ordered]@{}
    foreach ($name in $warningOnly) {
        $value = [int]($checks.$name)
        if ($value -ne 0) {
            $script:PackageWarnings[$name] = $value
            Write-RunLog ("[WARN] Non-blocking package quality gap: {0}={1}" -f $name, $value)
        }
    }

    $monthlyPage = Join-Path $script:PublishRoot ("basic_data\{0}" -f $script:MonthlyReportPageName)
    $monthlyAssets = Join-Path $script:PublishRoot ("basic_data\assets\{0}" -f $script:MonthlyReportAssetDirectoryName)
    $oldInsights = Join-Path $script:PublishRoot "basic_data\insights.html"
    $oldMonthDir = Join-Path $script:PublishRoot "basic_data\data\insight_rebalance_months"
    $oldFundMonthDir = Join-Path $script:PublishRoot "basic_data\data\rebalance_fund_category_months"
    if (-not (Test-Path -LiteralPath $monthlyPage)) {
        throw "Static monthly rebalance report is missing: $monthlyPage"
    }
    $monthlyPageContent = Get-Content -LiteralPath $monthlyPage -Raw -Encoding UTF8
    $expectedMonthlyTitle = Get-MonthlyReportTitle
    if (-not $monthlyPageContent.Contains($expectedMonthlyTitle)) {
        throw "Static monthly rebalance report content is incomplete: $monthlyPage"
    }
    if (-not (Test-Path -LiteralPath $monthlyAssets)) {
        throw "Static monthly rebalance asset directory is missing: $monthlyAssets"
    }
    $assetCount = (Get-ChildItem -LiteralPath $monthlyAssets -File | Measure-Object).Count
    if ($assetCount -lt 7) {
        throw "Static monthly rebalance asset count is too low: $assetCount"
    }
    if (Test-Path -LiteralPath $oldInsights) {
        throw "Old dynamic insights page still exists in minimal package: $oldInsights"
    }
    if ((Test-Path -LiteralPath $oldMonthDir) -or (Test-Path -LiteralPath $oldFundMonthDir)) {
        throw "Old dynamic monthly rebalance shard directories still exist."
    }

    $script:PackageValidation = $validation
    $script:PackageManifest = $manifest
    $script:PackageVersion = $version
    Write-RunLog ("[PACKAGE] build_id={0} files={1} total_mib={2}" -f $manifest.buildId, $manifest.fileCount, [math]::Round(([double]$manifest.totalBytes) / 1MB, 2))
    Write-RunLog ("[PACKAGE] strategy_details={0} fund_details={1} monthly_assets={2}" -f $checks.strategyDetailCount, $checks.enhancedFundDetailCount, $assetCount)
}

function Invoke-SmokeTest {
    if ($NoSmokeTest) {
        Write-RunLog "[SKIP] Local smoke test skipped."
        return
    }
    $process = $null
    try {
        $args = @(
            "-X", "utf8",
            "scripts\serve_basic_data_site.py",
            "--host", "127.0.0.1",
            "--port", "$SmokeTestPort",
            "--directory", "."
        )
        Write-RunLog ("[CMD] Start local smoke server: python {0}" -f (Join-ArgumentLine -Arguments $args))
        $process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $script:PublishRoot -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 3
        $base = "http://127.0.0.1:$SmokeTestPort"
        $urls = @(
            "$base/basic_data/strategies.html",
            "$base/basic_data/compare.html",
            "$base/basic_data/mixed-performance-scatter.html",
            "$base/basic_data/ai-strategy.html",
            "$base/basic_data/$($script:MonthlyReportPageName)",
            "$base/basic_data/assets/$($script:MonthlyReportAssetDirectoryName)/type_performance.png",
            "$base/basic_data/data/strategy_list_pack.js.gz",
            "$base/basic_data/data/mixed_performance_scatter_pack.js.gz"
        )
        $detail = @($script:PackageManifest.files | Where-Object { $_.path -like "basic_data/data/details/*.js.gz" } | Select-Object -First 1)
        $fundDetail = @($script:PackageManifest.files | Where-Object { $_.path -like "basic_data/data/fund_details/*.js.gz" } | Select-Object -First 1)
        if ($detail.Count -gt 0) {
            $urls += "$base/$($detail[0].path)"
        }
        if ($fundDetail.Count -gt 0) {
            $urls += "$base/$($fundDetail[0].path)"
        }
        foreach ($url in $urls) {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 30
            if ([int]$response.StatusCode -ne 200) {
                throw "Smoke test failed: $url status=$($response.StatusCode)"
            }
            Write-RunLog ("[SMOKE] 200 {0} bytes={1}" -f $url, $response.RawContentLength)
        }
    }
    finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
    }
}

function Invoke-Audit {
    if ($SkipAudit) {
        Write-RunLog "[SKIP] Data audit skipped."
        return
    }
    Invoke-ExternalCommand `
        -Name "data_audit_hook" `
        -FilePath "python" `
        -ArgumentList @(
            "-X", "utf8", (Join-Path $script:ProductionProgramRoot "run_project_data_audit_hook.py"),
            "--mode", "manual",
            "--audit-only",
            "--report-root", $script:ReportRoot
        ) `
        -WorkingDirectory $script:ProjectRoot
    $hook = Get-ChildItem -LiteralPath (Join-Path $script:ProjectRoot "outputs\data_audit_hook") -Recurse -Filter "hook_summary.json" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($hook) {
        $summary = Read-Json -Path $hook.FullName
        $script:AuditSummary = $summary
        Write-RunLog ("[AUDIT] status={0} static_errors={1} audit_errors={2} audit_warns={3}" -f $summary.status, $summary.staticErrorCount, $summary.auditSummary.error, $summary.auditSummary.warn)
        Write-RunLog ("[AUDIT] hook_summary={0}" -f $hook.FullName)
        if ($summary.auditReportPath) {
            Write-RunLog ("[AUDIT] report={0}" -f $summary.auditReportPath)
        }
        if ([int]$summary.staticErrorCount -ne 0 -or [int]$summary.auditSummary.error -ne 0) {
            throw "Audit has blocking errors."
        }
    }
}

function Invoke-GitPublish {
    if ($SkipPush) {
        Write-RunLog "[SKIP] Git commit and push skipped."
        return
    }
    $branchLines = @(Get-GitOutput -GitArgs @("rev-parse", "--abbrev-ref", "HEAD"))
    $branch = if ($branchLines.Count -gt 0) { ([string]$branchLines[0]).Trim() } else { "" }
    if ([string]::IsNullOrWhiteSpace($branch) -or $branch -eq "HEAD") {
        throw "Publish repo is not on a named branch."
    }
    Invoke-Git -GitArgs @("add", "-A")
    $staged = Get-GitOutput -GitArgs @("diff", "--cached", "--name-only")
    if ($staged.Count -eq 0 -or ($staged.Count -eq 1 -and [string]::IsNullOrWhiteSpace([string]$staged[0]))) {
        Write-RunLog "[GIT] No publish package changes to commit."
    }
    else {
        if (-not $CommitMessage) {
            $CommitMessage = "Update minimal publish set $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
        }
        Invoke-Git -GitArgs @("commit", "-m", $CommitMessage)
    }
    $script:PublishedBranch = $branch

    $maxPushAttempts = 3
    for ($pushAttempt = 1; $pushAttempt -le $maxPushAttempts; $pushAttempt++) {
        Invoke-Git -GitArgs @("fetch", "origin", $branch)
        $counts = ((Get-GitOutput -GitArgs @("rev-list", "--left-right", "--count", "HEAD...origin/$branch")) -join " ").Trim() -split "\s+"
        if ($counts.Count -lt 2) {
            throw "Unable to determine local/remote branch divergence."
        }
        $behind = [int]$counts[1]
        if ($behind -gt 0) {
            Write-RunLog ("[GIT] Remote branch advanced by {0} commit(s); rebasing safely before push." -f $behind)
            try {
                Invoke-Git -GitArgs @("rebase", "origin/$branch")
            }
            catch {
                $rebaseError = $_.Exception.Message
                try {
                    Invoke-Git -GitArgs @("rebase", "--abort")
                }
                catch {
                    Write-RunLog "[WARN] Rebase abort reported an additional error; inspect the publish repository."
                }
                throw "Remote publish changes overlap the generated package; rebase was aborted. $rebaseError"
            }
        }

        $headLines = @(Get-GitOutput -GitArgs @("rev-parse", "HEAD"))
        $script:PublishedCommit = ([string]$headLines[0]).Trim()
        try {
            Invoke-Git -GitArgs @("push", "origin", $branch)
            break
        }
        catch {
            if ($pushAttempt -ge $maxPushAttempts) {
                throw
            }
            Write-RunLog ("[WARN] Push attempt {0}/{1} failed; refreshing the remote branch and retrying." -f $pushAttempt, $maxPushAttempts)
        }
    }
    $remote = (Get-GitOutput -GitArgs @("ls-remote", "origin", "refs/heads/$branch")) -join "`n"
    if ($remote -notlike "$script:PublishedCommit*") {
        throw "Remote branch does not match local commit after push."
    }
    Write-RunLog ("[GIT] pushed branch={0} commit={1}" -f $branch, $script:PublishedCommit)
}

function Wait-GitHubPages {
    if ($SkipPush -or $SkipPagesVerify -or -not $PagesBaseUrl) {
        Write-RunLog "[SKIP] GitHub Pages verification skipped."
        return
    }
    $targetBuild = [string]$script:PackageVersion.buildId
    $deadline = (Get-Date).AddSeconds($WaitPagesSeconds)
    $base = $PagesBaseUrl.TrimEnd("/")
    $last = ""
    while ((Get-Date) -le $deadline) {
        $ts = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        try {
            $versionResp = Invoke-WebRequest -Uri "$base/version.json?ts=$ts" -UseBasicParsing -TimeoutSec 30
            $version = $versionResp.Content | ConvertFrom-Json
            $monthly = Invoke-WebRequest -Uri "$base/basic_data/$($script:MonthlyReportPageName)?ts=$ts" -UseBasicParsing -TimeoutSec 30
            $asset = Invoke-WebRequest -Uri "$base/basic_data/assets/$($script:MonthlyReportAssetDirectoryName)/type_performance.png?ts=$ts" -UseBasicParsing -TimeoutSec 30
            $oldStatus = "unknown"
            try {
                $old = Invoke-WebRequest -Uri "$base/basic_data/insights.html?ts=$ts" -UseBasicParsing -TimeoutSec 15
                $oldStatus = [string]$old.StatusCode
            }
            catch {
                if ($_.Exception.Response) {
                    $oldStatus = [string][int]$_.Exception.Response.StatusCode
                }
                else {
                    $oldStatus = "error"
                }
            }
            $buildMatch = ([string]$version.buildId -eq $targetBuild)
            $monthlyStatusOk = ([int]$monthly.StatusCode -eq 200)
            $monthlyContentOk = ([string]$monthly.Content).Contains((Get-MonthlyReportTitle))
            $assetStatusOk = ([int]$asset.StatusCode -eq 200)
            $oldInsightsRemoved = ($oldStatus -eq "404")
            $script:PagesLastState = [ordered]@{
                checkedAt = (Get-Date).ToString("s")
                targetBuild = $targetBuild
                observedBuild = [string]$version.buildId
                buildMatch = $buildMatch
                monthlyStatus = [int]$monthly.StatusCode
                monthlyStatusOk = $monthlyStatusOk
                monthlyContentOk = $monthlyContentOk
                assetStatus = [int]$asset.StatusCode
                assetStatusOk = $assetStatusOk
                oldInsightsStatus = $oldStatus
                oldInsightsRemoved = $oldInsightsRemoved
            }
            if ($buildMatch) {
                $script:PagesTargetBuildObserved = $true
                if ($monthlyStatusOk -and $assetStatusOk) {
                    $script:PagesTargetBuildReachable = $true
                }
            }
            $last = "build=$($version.buildId) buildMatch=$buildMatch monthly=$($monthly.StatusCode) monthlyContent=$monthlyContentOk asset=$($asset.StatusCode) oldInsights=$oldStatus"
            Write-RunLog ("[PAGES] {0}" -f $last)
            if ($buildMatch -and $monthlyStatusOk -and $monthlyContentOk -and $assetStatusOk -and $oldInsightsRemoved) {
                $script:PagesVerified = $true
                return
            }
        }
        catch {
            $last = $_.Exception.Message
            Write-RunLog ("[PAGES] waiting: {0}" -f $last)
        }
        Start-Sleep -Seconds 20
    }
    if ($script:PagesTargetBuildObserved -and $script:PagesTargetBuildReachable) {
        $script:PagesVerificationPending = $true
        Write-RunLog ("[WARN] GitHub Pages target build is reachable but edge content is still propagating. Last: {0}" -f $last)
        return
    }
    throw "GitHub Pages verification timed out. Last: $last"
}

$script:ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if (-not $ReportRoot) {
    $ReportRoot = Get-DefaultPlatformRoot
}
$script:ReportRoot = (Resolve-Path -LiteralPath $ReportRoot).Path
if (-not $PublishRoot) {
    $PublishRoot = Join-Path $script:ReportRoot (Get-MinimalPublishLeaf)
}
$script:PublishRoot = (Resolve-Path -LiteralPath $PublishRoot).Path

$runStartedAt = Get-Date
$runDay = $runStartedAt.ToString("yyyy-MM-dd")
$runId = $runStartedAt.ToString("yyyyMMdd_HHmmss")
if ($RunDirectory) {
    New-Item -ItemType Directory -Path $RunDirectory -Force | Out-Null
    $script:RunDir = (Resolve-Path -LiteralPath $RunDirectory).Path
}
else {
    $script:RunDir = Join-Path $script:ProjectRoot ("logs\minimal_publish_update\{0}\{1}" -f $runDay, $runId)
}
New-Item -ItemType Directory -Path $script:RunDir -Force | Out-Null
$script:LogFile = Join-Path $script:RunDir "console.log"
New-Item -ItemType File -Path $script:LogFile -Force | Out-Null
$script:PackageVersion = $null
$script:PackageManifest = $null
$script:PackageValidation = $null
$script:PackageWarnings = [ordered]@{}
$script:AuditSummary = $null
$script:PublishedCommit = $null
$script:PublishedBranch = $null
$script:PagesVerified = $false
$script:PagesVerificationPending = $false
$script:PagesTargetBuildObserved = $false
$script:PagesTargetBuildReachable = $false
$script:PagesLastState = $null

$status = "failed"
$errorMessage = $null
try {
    Write-RunLog "Minimal publish update started."
    Write-RunLog ("ProjectRoot : {0}" -f $script:ProjectRoot)
    Write-RunLog ("ReportRoot  : {0}" -f $script:ReportRoot)
    Write-RunLog ("PublishRoot : {0}" -f $script:PublishRoot)
    Write-RunLog ("PagesBaseUrl: {0}" -f $PagesBaseUrl)
    Write-RunLog ("RunDir      : {0}" -f $script:RunDir)

    if (-not $AllowDirtyPublishRepo) {
        $preDirty = Get-GitOutput -GitArgs @("status", "--short")
        if ($preDirty.Count -gt 0 -and -not ([string]::IsNullOrWhiteSpace(($preDirty -join "")))) {
            throw "Publish repo is dirty before rebuild. Use -AllowDirtyPublishRepo only if this is expected."
        }
    }

    Invoke-Step -Name "1. Unified Incremental Update" -Body {
        if ($SkipDataUpdate) {
            Write-RunLog "[SKIP] Unified incremental data update skipped."
            return
        }
        $args = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", (Resolve-NodeScript -LeafName "run_incremental_update_with_logs.ps1"),
            "-ProjectRoot", $script:ProjectRoot,
            "-ReportRoot", $script:ReportRoot,
            "-Unattended"
        )
        if ($DeviceId) {
            $args += @("-DeviceId", $DeviceId)
        }
        if ($HistoryMode) {
            $args += @("-HistoryMode", $HistoryMode)
        }
        if ($ExtraIncrementalArgs) {
            $args += @("-ExtraBatArgs", $ExtraIncrementalArgs)
        }
        Invoke-ExternalCommand -Name "unified_incremental_update" -FilePath "powershell" -ArgumentList $args -WorkingDirectory $script:ProjectRoot
    }

    Invoke-Step -Name "2. Build Minimal Publish Package" -Body {
        Invoke-ExternalCommand `
            -Name "build_minimal_publish_set" `
            -FilePath "python" `
            -ArgumentList @(
                "-X", "utf8",
                (Join-Path $script:ProductionProgramRoot "build_minimal_publish_set.py"),
                "--source-basic-data", (Join-Path $script:ReportRoot "basic_data"),
                "--target-dir", $script:PublishRoot,
                "--allowed-target-parent", (Split-Path -Parent $script:PublishRoot)
            ) `
            -WorkingDirectory $script:ProjectRoot
    }

    Invoke-Step -Name "3. Validate Minimal Package" -Body {
        Assert-PublishPackage
    }

    Invoke-Step -Name "4. Local HTTP Smoke Test" -Body {
        Invoke-SmokeTest
    }

    Invoke-Step -Name "5. Data Audit Hook" -Body {
        Invoke-Audit
    }

    Invoke-Step -Name "6. Commit And Push Minimal Package" -Body {
        Invoke-GitPublish
    }

    Invoke-Step -Name "7. Verify GitHub Pages" -Body {
        Wait-GitHubPages
    }

    $status = if ($script:PagesVerificationPending) { "success_pending_pages" } else { "success" }
}
catch {
    $errorMessage = $_.Exception.Message
    $status = "failed"
    throw
}
finally {
    $finishedAt = Get-Date
    $summary = [ordered]@{
        version = 1
        status = $status
        error = $errorMessage
        runId = $runId
        startedAt = $runStartedAt.ToString("s")
        finishedAt = $finishedAt.ToString("s")
        elapsedSeconds = [int]($finishedAt - $runStartedAt).TotalSeconds
        projectRoot = $script:ProjectRoot
        reportRoot = $script:ReportRoot
        publishRoot = $script:PublishRoot
        logFile = $script:LogFile
        packageBuildId = if ($script:PackageVersion) { $script:PackageVersion.buildId } else { $null }
        packageFileCount = if ($script:PackageManifest) { $script:PackageManifest.fileCount } else { $null }
        packageTotalBytes = if ($script:PackageManifest) { $script:PackageManifest.totalBytes } else { $null }
        packageWarnings = $script:PackageWarnings
        auditStatus = if ($script:AuditSummary) { $script:AuditSummary.status } else { $null }
        auditErrorCount = if ($script:AuditSummary) { $script:AuditSummary.auditSummary.error } else { $null }
        auditWarnCount = if ($script:AuditSummary) { $script:AuditSummary.auditSummary.warn } else { $null }
        auditReportPath = if ($script:AuditSummary) { $script:AuditSummary.auditReportPath } else { $null }
        publishedBranch = $script:PublishedBranch
        publishedCommit = $script:PublishedCommit
        pagesVerified = $script:PagesVerified
        pagesVerificationSkipped = [bool]$SkipPagesVerify
        pagesVerificationPending = $script:PagesVerificationPending
        pagesLastState = $script:PagesLastState
        pagesUrl = if ($PagesBaseUrl) { $PagesBaseUrl.TrimEnd("/") + "/basic_data/" + $script:MonthlyReportPageName } else { $null }
    }
    $summaryPath = Join-Path $script:RunDir "summary.json"
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-RunLog ("[SUMMARY] {0}" -f $summaryPath)
    Write-RunLog ("[RESULT] {0}" -f $status.ToUpperInvariant())
}
