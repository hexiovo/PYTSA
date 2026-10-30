param(
    [string]$ExecutablePath = "deliver\PYTSA-1.3.1\PYTSA.exe",
    [string]$ReleaseDir = "deliver\releases\v1.3.1",
    [string]$SkillSourceDir = "skills\pytsa-study-config",
    [string]$QuickStartPath = "docs\快速开始.md",
    [string]$SkillGuidePath = "docs\Skill使用说明.md",
    [string]$ReleaseNotesPath = "docs\发布说明-1.3.1.md"
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$separatorChars = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)

function Resolve-ProjectPath([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) {
        $fullPath = [System.IO.Path]::GetFullPath($Value)
    }
    else {
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
    }
    $prefix = $projectRoot.TrimEnd('\') + '\'
    if ($fullPath -eq $projectRoot -or -not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release paths must remain inside the project: $Value"
    }
    $cursor = $projectRoot
    foreach ($part in $fullPath.Substring($prefix.Length).Split($separatorChars, [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Release paths cannot pass through a reparse point: $cursor"
            }
        }
    }
    return $fullPath
}

function Write-Utf8([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

$exePath = Resolve-ProjectPath $ExecutablePath
$releaseRoot = Resolve-ProjectPath $ReleaseDir
$skillSource = Resolve-ProjectPath $SkillSourceDir
$quickStartSource = Resolve-ProjectPath $QuickStartPath
$skillGuideSource = Resolve-ProjectPath $SkillGuidePath
$releaseNotesSource = Resolve-ProjectPath $ReleaseNotesPath
$docsSource = Resolve-ProjectPath "docs"
$licensesSource = Resolve-ProjectPath "assets\licenses"
$thirdPartySource = Resolve-ProjectPath "assets\THIRD_PARTY_LICENSES.txt"
$launcherSource = Join-Path $PSScriptRoot "RunWithLocalData.cmd"
$installCommandSource = Join-Path $PSScriptRoot "InstallSkill.cmd"
$installerSource = Join-Path $PSScriptRoot "Install-Skill.ps1"
foreach ($path in @($exePath, $skillSource, $quickStartSource, $skillGuideSource, $releaseNotesSource, $docsSource, $licensesSource, $thirdPartySource, $launcherSource, $installCommandSource, $installerSource)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required release input was not found: $path" }
}
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "The final executable must exist before release packaging: $exePath"
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$portableDir = Join-Path $releaseRoot "PYTSA-1.3.1-Windows-x64"
$skillStage = Join-Path $releaseRoot ".skill-stage"
$releaseFiles = @(
    "PYTSA-1.3.1-Windows-x64.zip", "PYTSA-study-skill-1.3.1.zip",
    "PYTSA-study-skill-1.3.1.md", "QuickStart.md", "Skill-Guide.md",
    "SHA256SUMS.txt"
)
foreach ($name in $releaseFiles) {
    $path = Join-Path $releaseRoot $name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath (Resolve-ProjectPath $path) -Force }
}
foreach ($path in @($portableDir, $skillStage)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath (Resolve-ProjectPath $path) -Recurse -Force }
}

New-Item -ItemType Directory -Path $portableDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $portableDir "docs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $portableDir "licenses") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $portableDir "skills") -Force | Out-Null
New-Item -ItemType Directory -Path $skillStage -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $skillStage "skills") -Force | Out-Null
Copy-Item -LiteralPath $exePath -Destination (Join-Path $portableDir "PYTSA.exe")
Copy-Item -LiteralPath $thirdPartySource -Destination (Join-Path $portableDir "THIRD_PARTY_LICENSES.txt")
foreach ($file in Get-ChildItem -LiteralPath $licensesSource -Recurse -File) {
    $relative = $file.FullName.Substring($licensesSource.Length).TrimStart('\', '/')
    $destination = Join-Path (Join-Path $portableDir "licenses") $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}
$quickStartText = Get-Content -LiteralPath $quickStartSource -Raw -Encoding UTF8
$quickStartText = $quickStartText.Replace("(操作指南.md)", "(https://github.com/hexiovo/PYTSA/blob/v1.3.1/docs/%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97.md)")
$quickStartText = $quickStartText.Replace("(使用说明.md)", "(https://github.com/hexiovo/PYTSA/blob/v1.3.1/docs/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md)")
Write-Utf8 (Join-Path $releaseRoot "QuickStart.md") $quickStartText
$skillGuideText = Get-Content -LiteralPath $skillGuideSource -Raw -Encoding UTF8
$skillGuideText = $skillGuideText.Replace("(../skills/pytsa-study-config/SKILL.md)", "(https://github.com/hexiovo/PYTSA/blob/v1.3.1/skills/pytsa-study-config/SKILL.md)")
Write-Utf8 (Join-Path $releaseRoot "Skill-Guide.md") $skillGuideText

$excludedDocs = @("common-parameters-plan.md", "implementation-plan.md", "taskbar-fix-20261031.md", "verification-1.3.0.md")
foreach ($file in Get-ChildItem -LiteralPath $docsSource -Recurse -File) {
    $relative = $file.FullName.Substring($docsSource.Length).TrimStart('\', '/')
    if ($file.Name -in $excludedDocs -or $relative -match '(^|[\\/])(__pycache__|output)([\\/]|$)') { continue }
    $destination = Join-Path (Join-Path $portableDir "docs") $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}

$skillFiles = @()
foreach ($file in Get-ChildItem -LiteralPath $skillSource -Recurse -File | Sort-Object FullName) {
    $relative = $file.FullName.Substring($skillSource.Length).TrimStart('\', '/')
    if ($file.Extension -eq ".pyc" -or $relative -match '(^|[\\/])__pycache__([\\/]|$)' -or $relative -match '^examples[\\/]synthetic_only[\\/]output([\\/]|$)') { continue }
    $skillFiles += [pscustomobject]@{ Source = $file.FullName; Relative = $relative; Extension = $file.Extension }
}
if ($skillFiles.Count -eq 0) { throw "No Skill source files were found: $skillSource" }

$singleFile = [System.Text.StringBuilder]::new()
[void]$singleFile.AppendLine("# PYTSA Study Configuration Skill 1.3.1")
[void]$singleFile.AppendLine()
[void]$singleFile.AppendLine("This single-file edition combines the Skill instructions and text references, scripts, templates, and expected example values. The ZIP package contains the complete directory structure and installer.")
[void]$singleFile.AppendLine()
$textExtensions = @(".md", ".txt", ".json", ".py", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".html", ".css", ".js", ".xml", ".sh", ".ps1")
foreach ($file in $skillFiles) {
    $destinations = @(
        (Join-Path (Join-Path $portableDir "skills\pytsa-study-config") $file.Relative),
        (Join-Path (Join-Path $skillStage "skills\pytsa-study-config") $file.Relative)
    )
    foreach ($destination in $destinations) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.Source -Destination $destination
    }
    $displayPath = $file.Relative -replace '\\', '/'
    [void]$singleFile.AppendLine("## ``pytsa-study-config/$displayPath``")
    [void]$singleFile.AppendLine()
    if ($file.Extension.ToLowerInvariant() -notin $textExtensions) {
        [void]$singleFile.AppendLine("Binary asset; included in the ZIP directory at this path.")
        [void]$singleFile.AppendLine()
        continue
    }
    $content = Get-Content -LiteralPath $file.Source -Raw -Encoding UTF8
    $tickRuns = @([regex]::Matches($content, '`+') | ForEach-Object { $_.Value.Length })
    $fenceLength = 4
    if ($tickRuns.Count -gt 0) { $fenceLength = [Math]::Max(4, (($tickRuns | Measure-Object -Maximum).Maximum + 1)) }
    $fence = ('`' * $fenceLength)
    [void]$singleFile.AppendLine($fence)
    [void]$singleFile.AppendLine($content.TrimEnd("`r", "`n"))
    [void]$singleFile.AppendLine($fence)
    [void]$singleFile.AppendLine()
}
$singleFilePath = Join-Path $releaseRoot "PYTSA-study-skill-1.3.1.md"
Write-Utf8 $singleFilePath $singleFile.ToString()
Copy-Item -LiteralPath $singleFilePath -Destination (Join-Path (Join-Path $portableDir "skills") "PYTSA-study-skill-1.3.1.md")

Copy-Item -LiteralPath $launcherSource -Destination (Join-Path $portableDir "RunWithLocalData.cmd")
foreach ($root in @($portableDir, $skillStage)) {
    Copy-Item -LiteralPath $installCommandSource -Destination (Join-Path $root "InstallSkill.cmd")
    Copy-Item -LiteralPath $installerSource -Destination (Join-Path $root "Install-Skill.ps1")
}
Copy-Item -LiteralPath $singleFilePath -Destination (Join-Path (Join-Path $skillStage "skills") "PYTSA-study-skill-1.3.1.md")
Write-Utf8 (Join-Path $skillStage "Skill-Guide.md") $skillGuideText

$readme = @'
# PYTSA 1.3.1 Windows x64 便携版

1. 将压缩包解压到有写入权限的位置。
2. 双击 `RunWithLocalData.cmd` 启动。项目文件和运行记录保存在程序目录旁的 `Data` 文件夹；升级时请保留该文件夹。
3. 首次使用请阅读 `docs/快速开始.md`；界面截图指南为 `docs/操作指南.html`，参数与工作流程说明为 `docs/使用说明.md`。
4. 双击 `InstallSkill.cmd` 安装随包提供的 `pytsa-study-config` Skill。已有安装会先备份，入口不会修改 Codex 全局设置。详细步骤见 `docs/Skill使用说明.md`。

若要交给其他支持 Skill 的 AI 使用，可提供 `skills/PYTSA-study-skill-1.3.1.md` 和实验设计文件，并请它按 Skill 说明处理。

CPU 模式无需预装 Python。GPU 和 cvxEDA 为可选功能，条件与限制见随包文档。
'@
Write-Utf8 (Join-Path $portableDir "README.md") $readme
Write-Utf8 (Join-Path $skillStage "README.md") @'
# PYTSA 实验设计配置 Skill

双击 `InstallSkill.cmd` 安装 Skill。已有 `pytsa-study-config` 会先备份；安装入口只复制到用户的 Codex Skill 目录，不修改全局设置。使用流程见 `Skill-Guide.md`。

也可将 `skills/PYTSA-study-skill-1.3.1.md` 与实验设计文件一并提供给支持 Skill 的其他 AI，并请它按单文件中的说明处理。完整目录和安装脚本在本 ZIP 中。
'@

$portableZip = Join-Path $releaseRoot "PYTSA-1.3.1-Windows-x64.zip"
$skillZip = Join-Path $releaseRoot "PYTSA-study-skill-1.3.1.zip"
Compress-Archive -LiteralPath $portableDir -DestinationPath $portableZip -CompressionLevel Optimal -Force
$skillZipItems = Get-ChildItem -LiteralPath $skillStage -Force | Select-Object -ExpandProperty FullName
Compress-Archive -LiteralPath $skillZipItems -DestinationPath $skillZip -CompressionLevel Optimal -Force
Remove-Item -LiteralPath (Resolve-ProjectPath $skillStage) -Recurse -Force

$assets = foreach ($name in $releaseFiles | Where-Object { $_ -ne "SHA256SUMS.txt" }) {
    $path = Join-Path $releaseRoot $name
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name"
}
Set-Content -LiteralPath (Join-Path $releaseRoot "SHA256SUMS.txt") -Value $assets -Encoding ASCII
Get-ChildItem -LiteralPath $releaseRoot -File | Select-Object Name, Length
