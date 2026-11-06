$ErrorActionPreference = 'Stop'
if ($args.Count -ne 2 -or $args[0] -ne '--run-id') {
    Write-Error 'Usage: ./scripts/test-clean.ps1 --run-id <canonical-uuid>'
    exit 2
}
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Prepare backend dependencies first: uv sync --project backend --frozen'
    exit 2
}
& $python (Join-Path $PSScriptRoot 'test_runner.py') --clean-run-id $args[1]
exit $LASTEXITCODE
