$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $repo '.env'
if (Test-Path -LiteralPath $destination) { throw '.env already exists; refusing to overwrite it.' }
function New-RandomBytes {
    $buffer = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return ,$buffer
}
$content = Get-Content -LiteralPath (Join-Path $repo '.env.example') -Raw
foreach ($key in @('POSTGRES_PASSWORD', 'API_DB_PASSWORD', 'WORKER_DB_PASSWORD')) {
    $secret = [BitConverter]::ToString((New-RandomBytes)).Replace('-', '').ToLowerInvariant()
    $content = $content.Replace("$key=`n", "$key=$secret`n").Replace("$key=`r`n", "$key=$secret`r`n")
}
$ledgerKey = [Convert]::ToBase64String((New-RandomBytes))
$content = $content.Replace("LEDGER_KEY=`n", "LEDGER_KEY=$ledgerKey`n").Replace("LEDGER_KEY=`r`n", "LEDGER_KEY=$ledgerKey`r`n")
[IO.File]::WriteAllText($destination, $content, [Text.UTF8Encoding]::new($false))
Write-Output 'Created development .env with random credentials. Model metadata must still be probed.'
