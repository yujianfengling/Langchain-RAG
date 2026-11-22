param(
    [int]$Port = 8002
)

$ErrorActionPreference = 'Stop'
$Python = 'D:/Document/langchain-rag-venv/Scripts/python.exe'
$Frontend = Join-Path $PSScriptRoot 'frontend'
$env:EDU_DATA_DIR = 'D:/Document/langchain-rag-work/demo_data'
$env:COMPETITION_OFFICIAL_BUNDLE_DIR = 'D:/Document/langchain-rag-work/competition-data/official-bundle'
$env:PYTHONIOENCODING = 'utf-8'

if (!(Test-Path $Python)) { throw "Python environment not found: $Python" }
if (!(Test-Path (Join-Path $Frontend 'node_modules'))) {
    throw "Install frontend dependencies first: npm --prefix `"$Frontend`" ci"
}
if (!(Test-Path $env:COMPETITION_OFFICIAL_BUNDLE_DIR)) { throw 'The private official data bundle is missing.' }

& npm.cmd --prefix $Frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
& $Python -m uvicorn api.app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
