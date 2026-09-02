param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceRoot,
    [switch]$CheckOnly,
    [switch]$RecreateVenv,
    [switch]$InstallPlaywrightChromium
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$WorkspaceRoot = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$CodeRoot = Join-Path $WorkspaceRoot "程序代码"
$Requirements = Join-Path $CodeRoot "config\runtime\requirements-runtime.txt"
$VenvRoot = Join-Path $WorkspaceRoot "运行环境\python"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$OutputDir = Join-Path $WorkspaceRoot "运行状态\outputs\workspace_check"
$RunId = Get-Date -Format "yyyyMMddTHHmmssK"
$ReportPath = Join-Path $OutputDir ("environment_{0}.json" -f ($RunId -replace ':', ''))

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (($machine, $user) | Where-Object { $_ }) -join ";"
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$Label
    )
    Write-Step ("执行 {0}: {1} {2}" -f $Label, $FilePath, ($ArgumentList -join " "))
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} 失败，退出码={1}" -f $Label, $LASTEXITCODE)
    }
}

function Find-Python312 {
    $knownPaths = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($knownPath in $knownPaths) {
        if (Test-Path -LiteralPath $knownPath) {
            $version = (& $knownPath -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") { return $knownPath }
        }
    }
    $py = Get-CommandPath "py"
    if ($py) {
        $candidate = (& $py -3.12 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath $candidate)) {
            return [string]$candidate
        }
    }
    $python = Get-CommandPath "python"
    if ($python) {
        $version = (& $python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") { return $python }
    }
    return $null
}

function Install-WingetPackage {
    param([string]$Id, [string]$Label)
    $winget = Get-CommandPath "winget"
    if (-not $winget) {
        throw "未找到 winget，无法自动安装 $Label。请先从 Microsoft Store 安装应用安装程序，再重试。"
    }
    $arguments = @(
        "install", "--id", $Id, "--exact", "--silent",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    Write-Step ("执行 安装 {0}: {1} {2}" -f $Label, $winget, ($arguments -join " "))
    & $winget @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Step ("winget 返回退出码 {0}，将重新探测 {1} 是否已经可用。" -f $exitCode, $Label)
    }
    Refresh-ProcessPath
}

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "依赖清单不存在：$Requirements"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$result = [ordered]@{
    generatedAt = (Get-Date).ToString("o")
    workspaceRoot = $WorkspaceRoot
    checkOnly = [bool]$CheckOnly
    python312 = $null
    git = $null
    node = $null
    venvPython = $VenvPython
    dependencyCheck = $null
    status = "running"
    errors = @()
}

try {
    Refresh-ProcessPath
    $python312 = Find-Python312
    $git = Get-CommandPath "git"
    $node = Get-CommandPath "node"

    if (-not $CheckOnly) {
        if (-not $python312) {
            Install-WingetPackage -Id "Python.Python.3.12" -Label "Python 3.12"
            $python312 = Find-Python312
        }
        if (-not $git) {
            Install-WingetPackage -Id "Git.Git" -Label "Git"
            $git = Get-CommandPath "git"
        }
        if (-not $node) {
            Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -Label "Node.js LTS"
            $node = Get-CommandPath "node"
        }
    }

    if (-not $python312) { throw "缺少 Python 3.12。" }
    if (-not $git) { throw "缺少 Git。" }
    if (-not $node) { throw "缺少 Node.js。" }

    if ($RecreateVenv -and (Test-Path -LiteralPath $VenvRoot)) {
        $resolvedVenv = [System.IO.Path]::GetFullPath($VenvRoot)
        if (-not $resolvedVenv.StartsWith($WorkspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝删除工作区外的虚拟环境：$resolvedVenv"
        }
        Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($CheckOnly) { throw "工作区 Python 虚拟环境尚未创建。" }
        Invoke-Native -FilePath $python312 -ArgumentList @("-m", "venv", $VenvRoot) -Label "创建工作区 Python 环境"
    }

    & $VenvPython -c "import sys;raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "工作区 Python 版本不是 3.12；请使用 -RecreateVenv 重建。"
    }
    if (-not $CheckOnly) {
        Invoke-Native -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") -Label "更新 Python 安装工具"
        Invoke-Native -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "--requirement", $Requirements) -Label "安装项目 Python 依赖"
        if ($InstallPlaywrightChromium) {
            Invoke-Native -FilePath $VenvPython -ArgumentList @("-m", "playwright", "install", "chromium") -Label "安装 Playwright Chromium"
        }
        & $git config --global core.longpaths true
    }

    $importProbe = "import requests,pandas,numpy,openpyxl,matplotlib,seaborn,pdfplumber,playwright,bs4,lxml,PIL,reportlab,akshare,pypdf,yaml,xlsxwriter,mitmproxy;print('ready')"
    $dependencyCheck = (& $VenvPython -c $importProbe 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $dependencyCheck -ne "ready") {
        throw "Python 依赖导入检查失败：$dependencyCheck"
    }

    $result.python312 = $python312
    $result.git = $git
    $result.node = $node
    $result.dependencyCheck = $dependencyCheck
    $result.status = "ready"
    Write-Step "运行环境已就绪。"
}
catch {
    $result.status = "blocked"
    $result.errors = @($_.Exception.Message)
    Write-Step ("失败：{0}" -f $_.Exception.Message)
}
finally {
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Step ("环境报告：{0}" -f $ReportPath)
}

if ($result.status -ne "ready") { exit 2 }
exit 0
