[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallRoot = 'D:\\MOSS_Diagnostics\\MOSS_AI_Camera_Test'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$Launcher = Join-Path $SourceRoot 'diagnostics\\START_AI_CAMERA_TEST.cmd'
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "The AI camera test launcher is missing from $SourceRoot"
}
if (Test-Path -LiteralPath $InstallRoot) {
    throw "Install folder already exists; preserving it: $InstallRoot"
}

$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'MOSS AI Camera Test.lnk'
if (Test-Path -LiteralPath $ShortcutPath) {
    throw "Desktop shortcut already exists; preserving it: $ShortcutPath"
}

$StageRoot = "$InstallRoot.stage.$PID"
if (Test-Path -LiteralPath $StageRoot) {
    throw "Temporary staging folder already exists; preserving it: $StageRoot"
}

try {
    New-Item -ItemType Directory -Path $StageRoot | Out-Null
    $PackageRoot = Join-Path $StageRoot 'moss_recognition'
    New-Item -ItemType Directory -Path $PackageRoot | Out-Null
    Copy-Item -Path (Join-Path $SourceRoot '*') -Destination $PackageRoot -Recurse -Force
    Move-Item -LiteralPath $StageRoot -Destination $InstallRoot

    $InstalledLauncher = Join-Path $InstallRoot 'moss_recognition\\diagnostics\\START_AI_CAMERA_TEST.cmd'
    if (-not (Test-Path -LiteralPath $InstalledLauncher -PathType Leaf)) {
        throw 'Installed launcher is missing; remove only the newly created installation folder and retry.'
    }
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $InstalledLauncher
    $Shortcut.WorkingDirectory = $InstallRoot
    $Shortcut.Description = 'Camera-only MOSS AI recognition; motors remain disabled.'
    $Shortcut.Save()
    Write-Output "Installed MOSS AI Camera Test at $InstallRoot"
    Write-Output "Created desktop shortcut: $ShortcutPath"
}
catch {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
    throw
}
