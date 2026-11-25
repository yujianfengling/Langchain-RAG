param(
    [int]$Port = 8002,
    [string]$Python = $env:COMPETITION_PYTHON,
    [string]$BundleDir = $env:COMPETITION_OFFICIAL_BUNDLE_DIR,
    [string]$SourceDir = $env:COMPETITION_OFFICIAL_SRC_DIR,
    [string]$ResourceDir = $env:COMPETITION_OFFICIAL_RESOURCE_DIR
)
$ErrorActionPreference = 'Stop'
$localRoot = Join-Path $env:LOCALAPPDATA 'SmartStudyPartner'
if (!$Python) { $Python = Join-Path $PSScriptRoot 'venv/Scripts/python.exe' }
if (!$BundleDir) { $BundleDir = Join-Path $localRoot 'official-bundle' }
if (!$SourceDir) { $SourceDir = Join-Path $localRoot 'official' }
if (!$ResourceDir) { $ResourceDir = Join-Path $localRoot 'resources' }
$env:COMPETITION_OFFICIAL_BUNDLE_DIR = $BundleDir
$env:COMPETITION_OFFICIAL_SRC_DIR = $SourceDir
$env:COMPETITION_OFFICIAL_RESOURCE_DIR = $ResourceDir
if (!$env:EDU_DATA_DIR) { $env:EDU_DATA_DIR = Join-Path $localRoot 'teacher-data' }
$env:PYTHONIOENCODING = 'utf-8'
& npm.cmd --prefix (Join-Path $PSScriptRoot 'frontend') run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
& $Python -m uvicorn api.app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
