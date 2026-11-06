$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Prepare backend dependencies first: uv sync --project backend --frozen'
    exit 2
}
& $python (Join-Path $PSScriptRoot 'test_runner.py') @args
exit $LASTEXITCODE
