param(
    [string]$Version,
    [switch]$SkipBuild,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "tools\project.ps1")

# overlay_board.py owns the version. An explicit -Version (the tag, in CI) is
# only allowed to agree with it, so a tag can never ship an EXE stamped with
# a different number than the installer around it.
if (-not $Version) {
    $Version = $ProjectVersion
} elseif ($Version -ne $ProjectVersion) {
    throw ("Version mismatch: asked for $Version but overlay_board.py " +
           "declares $ProjectVersion. Bump __version__ and re-tag.")
}

$exePath = Join-Path $PSScriptRoot "dist\OverlayBoard.exe"
$releaseDir = Join-Path $PSScriptRoot "dist\release"
$artifactName = "OverlayBoard-v$Version-windows-x64"
$zipPath = Join-Path $releaseDir "$artifactName.zip"
$zipChecksumPath = "$zipPath.sha256"
$installerPath = Join-Path $releaseDir "OverlayBoard-v$Version-Setup.exe"
$installerChecksumPath = "$installerPath.sha256"
$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("OverlayBoard-release-" + [guid]::NewGuid())
$stageApp = Join-Path $stageRoot $artifactName

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build.ps1")
}
if (-not (Test-Path $exePath)) {
    throw "Packaged app not found. Run .\build.ps1 first or omit -SkipBuild."
}

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
New-Item -ItemType Directory -Path $stageApp -Force | Out-Null

try {
    Copy-Item $exePath $stageApp
    New-Item -ItemType File -Path (Join-Path $stageApp "portable.flag") | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "README.md") $stageApp
    Copy-Item (Join-Path $PSScriptRoot "LICENSE") $stageApp
    Copy-Item (Join-Path $PSScriptRoot "docs") $stageApp -Recurse

    Remove-Item $zipPath, $zipChecksumPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $stageApp -DestinationPath $zipPath -CompressionLevel Optimal

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $relativePaths = @($archive.Entries | ForEach-Object {
            $_.FullName -replace '^[^/\\]+[/\\]', ''
        })
        $required = @('OverlayBoard.exe', 'portable.flag', 'README.md', 'LICENSE')
        $missing = $required | Where-Object { $_ -notin $relativePaths }
        if ($missing) {
            throw "Release is missing required files: $($missing -join ', ')"
        }
        $private = $relativePaths | Where-Object {
            $_ -in @('OverlayBoard.json', 'OverlayBoard.json.bak', 'OverlayBoard.json.tmp')
        }
        if ($private) {
            throw "Release contains private runtime files: $($private -join ', ')"
        }
    }
    finally {
        $archive.Dispose()
    }

    $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content $zipChecksumPath "$hash  $([System.IO.Path]::GetFileName($zipPath))" -Encoding ascii

    if (-not $SkipInstaller) {
        $isccCandidates = @(
            (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        ) | Where-Object { $_ -and (Test-Path $_) }
        $iscc = $isccCandidates | Select-Object -First 1
        if (-not $iscc) {
            throw "Inno Setup 6 was not found. Install it or use -SkipInstaller."
        }

        Remove-Item $installerPath, $installerChecksumPath -Force -ErrorAction SilentlyContinue
        & $iscc "/DMyAppVersion=$Version" "/DMyAppPublisher=$ProjectPublisher" `
            "/DMyAppURL=$ProjectUrl" (Join-Path $PSScriptRoot "installer.iss")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $installerPath)) {
            throw "Installer build failed with exit code $LASTEXITCODE."
        }
        $hash = (Get-FileHash $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content $installerChecksumPath `
            "$hash  $([System.IO.Path]::GetFileName($installerPath))" -Encoding ascii
    }
}
finally {
    Remove-Item $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Portable: $zipPath"
Write-Host "SHA-256: $zipChecksumPath"
if (-not $SkipInstaller) {
    Write-Host "Installer: $installerPath"
    Write-Host "SHA-256: $installerChecksumPath"
}