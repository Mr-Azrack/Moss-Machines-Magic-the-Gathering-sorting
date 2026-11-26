$ErrorActionPreference = 'Stop'
$env:MOSS_AI_SERVER_ENABLED = '1'
$recognitionRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Split-Path -Parent $recognitionRoot
$diagnosticsRoot = Split-Path -Parent $installRoot
$pilotPath = Join-Path $diagnosticsRoot 'Foundations_Pilot'
$pythonPaths = @($recognitionRoot, $pilotPath, $PSScriptRoot)
if ($env:PYTHONPATH) { $pythonPaths += $env:PYTHONPATH }
$env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
$secretFile = Join-Path $env:LOCALAPPDATA 'MOSS\Hosted\lookup.dpapi'
if (-not (Test-Path -LiteralPath $secretFile)) { throw 'MOSS lookup credential not configured on this PC.' }
$secret = Get-Content -LiteralPath $secretFile | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:MOSS_LOOKUP_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $env:PYTHONIOENCODING = 'utf-8'
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python -u (Join-Path $PSScriptRoot 'hosted_camera_live.py')
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:\MOSS_LOOKUP_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
