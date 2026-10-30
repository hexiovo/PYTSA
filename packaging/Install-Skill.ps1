param([string]$Destination)

$ErrorActionPreference = "Stop"
function Resolve-InstallPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) {
        throw "A drive or filesystem root cannot be an install destination."
    }
    $separatorChars = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $cursor = $root
    foreach ($part in $full.Substring($root.Length).Split($separatorChars, [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Install paths cannot pass through a reparse point: $cursor"
            }
        }
    }
    return $full
}

$source = Join-Path $PSScriptRoot "skills\pytsa-study-config"
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "The bundled Skill directory was not found: $source"
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $env:USERPROFILE ".codex\skills\pytsa-study-config"
}
$target = Resolve-InstallPath $Destination
$parent = Split-Path -Parent $target
New-Item -ItemType Directory -Path $parent -Force | Out-Null
if (Test-Path -LiteralPath $target) {
    if (-not (Test-Path -LiteralPath $target -PathType Container)) {
        throw "The install destination exists and is not a directory: $target"
    }
    $backup = Resolve-InstallPath "$target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
    Move-Item -LiteralPath $target -Destination $backup
    Write-Host "Previous Skill backed up to: $backup"
}
Copy-Item -LiteralPath $source -Destination $target -Recurse
Write-Host "Installed PYTSA study Skill to: $target"
