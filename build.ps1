param(
    [string]$DeliveryDir = "C:\Users\DELL\Desktop\APP"
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distPath = Join-Path $projectRoot "build\dist"
$workPath = Join-Path $projectRoot "build\work"
$specPath = Join-Path $projectRoot "build\spec"
$sourceExe = Join-Path $distPath "PYTSA.exe"
$iconPath = Join-Path $projectRoot "assets\PYTSA.ico"
$researchFile = Join-Path $projectRoot "research_notes.json"
$assetDir = Join-Path $projectRoot "assets"
if ([string]::IsNullOrWhiteSpace($DeliveryDir)) {
    throw "DeliveryDir must be a non-empty path."
}
$deliveryPath = $DeliveryDir.Trim()
if (-not [System.IO.Path]::IsPathRooted($deliveryPath)) {
    $deliveryPath = Join-Path $projectRoot $deliveryPath
}
$deliveryPath = [System.IO.Path]::GetFullPath($deliveryPath)
$deliveryExe = Join-Path $deliveryPath "PYTSA.exe"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Project-local Python was not found: $pythonExe"
}

Push-Location $projectRoot
try {
    $pyinstallerArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "PYTSA",
        "--icon", $iconPath,
        "--distpath", $distPath,
        "--workpath", $workPath,
        "--specpath", $specPath,
        "--add-data", "$researchFile;.",
        "--add-data", "$assetDir;assets",
        "--hidden-import", "core",
        "--hidden-import", "experimental",
        "--hidden-import", "report_export",
        "--hidden-import", "PIL.ImageGrab",
        "--collect-all", "neurokit2",
        "--collect-all", "statsmodels",
        "app.py"
    )

    & $pythonExe @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
        throw "PyInstaller did not create the expected executable: $sourceExe"
    }

    New-Item -ItemType Directory -Path $deliveryPath -Force | Out-Null
    Copy-Item -LiteralPath $sourceExe -Destination $deliveryExe -Force
    $built = Get-Item -LiteralPath $sourceExe
    $delivered = Get-Item -LiteralPath $deliveryExe
    if ($delivered.Length -ne $built.Length -or $delivered.Length -le 0) {
        throw "Delivered executable does not match the build output."
    }

    [pscustomobject]@{
        source = $sourceExe
        delivery = $deliveryExe
        bytes = $delivered.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $deliveryExe).Hash
        created_utc = $delivered.LastWriteTimeUtc.ToString("o")
    } | ConvertTo-Json -Depth 4
}
finally {
    Pop-Location
}
