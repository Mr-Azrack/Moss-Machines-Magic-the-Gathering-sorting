$ErrorActionPreference = 'Stop'
$env:MOSS_AI_SERVER_ENABLED = '1'
$diagRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pilotPath = Join-Path $diagRoot 'Foundations_Pilot'
$env:PYTHONPATH = if ($env:PYTHONPATH) { $pilotPath + [IO.Path]::PathSeparator + $env:PYTHONPATH } else { $pilotPath }
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
