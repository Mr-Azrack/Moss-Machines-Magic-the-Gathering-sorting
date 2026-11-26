[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallRoot = 'D:\\MOSS_Diagnostics\\MOSS_AI_Camera_Test'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$Files = @(
    'server.py', 'passenger_wsgi.py', 'catalog_match.py', 'ai_image_api.py',
    'ai_worker.py', 'ai_preflight.py', 'benchmark_ai_worker.py',
    'requirements.txt', 'requirements-ai-worker.txt', 'install_ai_worker.sh',
    'AI_DEPLOYMENT.md', 'README.md', 'export_verified_training.py',
    'camera_evidence.py', 'diagnostics/capture_evidence.py',
    'diagnostics/hosted_camera_live.py', 'diagnostics/server_ai_lookup.py',
    'diagnostics/hosted_live_lookup.py', 'diagnostics/fixed_height_preview.py',
    'diagnostics/evidence_replay.py', 'diagnostics/read_footer_winrt.ps1',
    'diagnostics/START_AI_CAMERA_TEST.cmd', 'diagnostics/START_AI_CAMERA_TEST.ps1',
    'diagnostics/install_ai_camera_test.ps1'
)
$Missing = @($Files | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $SourceRoot $_) -PathType Leaf)
})
if ($Missing.Count -gt 0) {
    throw ('Required camera-test source files are missing: ' + ($Missing -join ', '))
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
    foreach ($RelativePath in $Files) {
        $SourcePath = Join-Path $SourceRoot $RelativePath
        $DestinationPath = Join-Path $PackageRoot $RelativePath
        $DestinationDirectory = Split-Path -Parent $DestinationPath
        if (-not (Test-Path -LiteralPath $DestinationDirectory)) {
            New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
        }
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
    }
    Move-Item -LiteralPath $StageRoot -Destination $InstallRoot

    $InstalledLauncher = Join-Path $InstallRoot 'moss_recognition\diagnostics\START_AI_CAMERA_TEST.cmd'
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
