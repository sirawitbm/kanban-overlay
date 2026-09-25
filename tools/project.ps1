# Single source of truth for release metadata.
#
# The version is read out of kanban_overlay.py rather than repeated here, so
# the git tag, the version stamped into the EXE, and the installer name
# cannot drift apart. Bump __version__ and everything downstream follows.

$ProjectRoot = Split-Path $PSScriptRoot -Parent

$sourcePath = Join-Path $ProjectRoot "kanban_overlay.py"
$source = Get-Content $sourcePath -Raw
if ($source -notmatch '(?m)^__version__\s*=\s*"(?<v>\d+\.\d+\.\d+)"') {
    throw "Could not read a semantic __version__ from $sourcePath."
}

$ProjectVersion   = $Matches.v
$ProjectName      = "Kanban Overlay"
$ProjectExeName   = "KanbanOverlay"

# Shown in the EXE's file properties and the installer's publisher field.
# Change it here only - both consumers read this one value.
$ProjectPublisher = "Sirawit Butmaratthaya"
$ProjectUrl       = "https://github.com/sirawitbm/kanban-overlay"

function New-VersionInfoFile {
    <#
        .SYNOPSIS
        Write a PyInstaller --version-file stamped with $ProjectVersion.
        Generated rather than committed, so it can never be stale.
    #>
    param([Parameter(Mandatory)][string]$Path)

    $p = $ProjectVersion.Split(".")
    $tuple = "($($p[0]), $($p[1]), $($p[2]), 0)"

    $content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$tuple,
    prodvers=$tuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable(
      '040904B0',
      [StringStruct('CompanyName', '$ProjectPublisher'),
       StringStruct('FileDescription', '$ProjectName'),
       StringStruct('FileVersion', '$ProjectVersion'),
       StringStruct('InternalName', '$ProjectExeName'),
       StringStruct('OriginalFilename', '$ProjectExeName.exe'),
       StringStruct('ProductName', '$ProjectName'),
       StringStruct('ProductVersion', '$ProjectVersion')]
    )]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    Set-Content -Path $Path -Value $content -Encoding ascii
}
