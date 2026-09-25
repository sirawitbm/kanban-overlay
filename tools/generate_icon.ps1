$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NativeIcon {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

$assetDir = Join-Path $PSScriptRoot "..\assets"
$iconPath = Join-Path $assetDir "OverlayBoard.ico"
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null

$bitmap = New-Object System.Drawing.Bitmap 256, 256
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::Transparent)

$background = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(16, 18, 22))
$blue = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(121, 192, 255))
$yellow = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 209, 102))
$green = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(126, 231, 135))
$text = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(232, 232, 234)), 12
$text.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$text.EndCap = [System.Drawing.Drawing2D.LineCap]::Round

$graphics.FillRectangle($background, 12, 12, 232, 232)
$graphics.FillRectangle($blue, 12, 12, 232, 34)
$graphics.FillRectangle($yellow, 34, 72, 188, 62)
$graphics.FillRectangle($green, 34, 148, 188, 62)
$graphics.DrawLine($text, 61, 102, 81, 119)
$graphics.DrawLine($text, 81, 119, 111, 85)
$graphics.DrawLine($text, 61, 178, 81, 195)
$graphics.DrawLine($text, 81, 195, 111, 161)

$handle = $bitmap.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($handle)
$stream = [System.IO.File]::Create($iconPath)
try {
    $icon.Save($stream)
}
finally {
    $stream.Dispose()
    $icon.Dispose()
    [NativeIcon]::DestroyIcon($handle) | Out-Null
    $text.Dispose()
    $green.Dispose()
    $yellow.Dispose()
    $blue.Dispose()
    $background.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

Write-Host "Icon: $iconPath"