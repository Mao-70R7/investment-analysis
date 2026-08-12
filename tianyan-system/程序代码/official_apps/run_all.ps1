param(
    [string]$Apps = "huaxia_tougu,zocaifu,harvestwm,southern,cmfchina,efundcf,gffunds,fullgoal,fund99,qieman",
    [int]$Workers = 8,
    [int]$HarvestPages = 0,
    [switch]$ZocaifuSkipFundNav,
    [switch]$GffundsSkipFundNav,
    [switch]$GffundsSkipProtocolPdf
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $Root "scripts\collect_official_apps_public.py"

$argsList = @($Script, "--apps", $Apps, "--workers", "$Workers")
if ($HarvestPages -gt 0) {
    $argsList += @("--harvest-pages", "$HarvestPages")
}
if ($ZocaifuSkipFundNav) {
    $argsList += "--zocaifu-skip-fund-nav"
}
if ($GffundsSkipFundNav) {
    $argsList += "--gffunds-skip-fund-nav"
}
if ($GffundsSkipProtocolPdf) {
    $argsList += "--gffunds-skip-protocol-pdf"
}

python @argsList
