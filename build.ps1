$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "tools\project.ps1")

$iconPath = Join-Path $PSScriptRoot "assets\KanbanOverlay.ico"
$outputPath = Join-Path $PSScriptRoot "dist\$ProjectExeName.exe"
$versionInfoPath = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("KanbanOverlay-version-" + [guid]::NewGuid() + ".txt")

if (-not (Test-Path $iconPath)) {
    & (Join-Path $PSScriptRoot "tools\generate_icon.ps1")
}

python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller installation failed with exit code $LASTEXITCODE."
}

Write-Host "Building $ProjectName $ProjectVersion"
New-VersionInfoFile -Path $versionInfoPath

try {
    python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name $ProjectExeName `
        --icon $iconPath `
        --version-file $versionInfoPath `
        (Join-Path $PSScriptRoot "kanban_overlay.py")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $outputPath)) {
        throw "Kanban Overlay build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item $versionInfoPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Done: $outputPath"
