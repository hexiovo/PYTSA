param(
    [switch]$Apply,
    [string]$RptPath
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$lockPath = Join-Path $PSScriptRoot "cleanup-targets.txt"
$prefix = $projectRoot.TrimEnd('\') + '\'
$separatorChars = [char[]]@([System.IO.Path]::DirectorySeparatorChar)
$results = @()

foreach ($line in Get-Content -LiteralPath $lockPath -Encoding UTF8) {
    $relative = $line.Trim()
    if (-not $relative -or $relative.StartsWith('#')) { continue }
    if ([System.IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or $relative -match '[*?\[\]]') {
        throw "Cleanup lock entries must be exact relative paths without wildcards: $relative"
    }
    $parts = $relative.Replace('/', '\').Split($separatorChars, [System.StringSplitOptions]::RemoveEmptyEntries)
    if ($parts.Count -eq 0 -or $parts -contains '.' -or $parts -contains '..') {
        throw "Cleanup lock entry is not a safe project-relative target: $relative"
    }

    $target = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
    if ($target -eq $projectRoot -or -not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target must remain inside the project: $relative"
    }

    $cursor = $projectRoot
    foreach ($part in $parts) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Cleanup refuses reparse points: $cursor"
            }
        }
    }

    if (-not (Test-Path -LiteralPath $target)) {
        $action = "NOT_PRESENT"
    }
    elseif ($Apply) {
        $item = Get-Item -LiteralPath $target -Force
        if ($item.PSIsContainer) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        else {
            Remove-Item -LiteralPath $target -Force
        }
        $action = "REMOVED"
    }
    else {
        $action = "WOULD_REMOVE"
    }
    $results += [pscustomobject]@{ relative_path = $relative; path = $target; action = $action }
}

$report = [pscustomobject]@{
    project = $projectRoot
    apply = [bool]$Apply
    generated_utc = [DateTime]::UtcNow.ToString("o")
    targets = $results
}
$json = $report | ConvertTo-Json -Depth 5
if ($RptPath) {
    $reportPath = [System.IO.Path]::GetFullPath($RptPath)
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $reportPath) -PathType Container)) {
        throw "Report directory does not exist: $(Split-Path -Parent $reportPath)"
    }
    [System.IO.File]::WriteAllText($reportPath, $json, [System.Text.UTF8Encoding]::new($false))
}
$json
