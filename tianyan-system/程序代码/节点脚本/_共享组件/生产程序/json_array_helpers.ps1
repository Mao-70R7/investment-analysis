function Read-JsonArrayStrict {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "JSON array file not found: $Path"
    }

    try {
        $parsed = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "Invalid JSON array file '$Path': $($_.Exception.Message)"
    }

    if ($null -eq $parsed) {
        return
    }
    if ($parsed -isnot [System.Array]) {
        throw "Expected a JSON array in '$Path', got $($parsed.GetType().FullName)"
    }

    # Windows PowerShell can emit a JSON array as one Object[] pipeline item.
    # Explicit foreach expansion keeps callers from treating the whole batch as one row.
    foreach ($item in $parsed) {
        Write-Output $item
    }
}
