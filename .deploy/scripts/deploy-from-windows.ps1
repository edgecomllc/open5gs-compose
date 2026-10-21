# Deploys the open5gs-compose stack with encrypted PSMDB to the test stand.
#
# Usage (from the project root on Windows):
#   powershell -ExecutionPolicy Bypass -File .deploy\scripts\deploy-from-windows.ps1
#
# Why this script exists: passing complex shell commands through
# ssh from PowerShell breaks quoting (backticks, $, nested quotes).
# Instead we: convert files to LF -> scp them -> run ONE simple ssh command.
# No Cygwin needed - plain Windows OpenSSH (ssh/scp) is enough.

$ErrorActionPreference = 'Stop'

$Server    = 'mazay@192.168.0.9'
$BindAddr  = '192.168.0.2'
$RemoteDir = 'open5gs-compose'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# Files that must be synced to the server (relative to the project root, forward slashes)
$Files = @(
    'docker-compose.yaml',
    '.deploy/docker/openbao/openbao.hcl',
    '.deploy/docker/openbao/entrypoint.sh',
    '.deploy/docker/psmdb/mongod.conf',
    '.deploy/scripts/generate-certs.sh',
    '.deploy/scripts/deploy-encrypted.sh'
)

Write-Host "== 1/3: normalize line endings to LF (bash on the server requires LF) ==" -ForegroundColor Cyan
foreach ($f in $Files) {
    $p = Join-Path $ProjectRoot ($f -replace '/', '\')
    if (-not (Test-Path $p)) { throw "File not found: $p" }
    $c = [IO.File]::ReadAllText($p) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($p, $c)
    Write-Host "  LF: $f"
}

Write-Host "== 2/3: copy files to $Server via scp ==" -ForegroundColor Cyan
foreach ($f in $Files) {
    $local = Join-Path $ProjectRoot ($f -replace '/', '\')
    & scp -o "BindAddress=$BindAddr" $local "${Server}:${RemoteDir}/${f}"
    if ($LASTEXITCODE -ne 0) { throw "scp failed for $f" }
    Write-Host "  OK: $f"
}

Write-Host "== 3/3: run deployment on the server (single ssh command) ==" -ForegroundColor Cyan
& ssh -o BatchMode=yes -b $BindAddr $Server "bash ~/${RemoteDir}/.deploy/scripts/deploy-encrypted.sh 2>&1 | tee ~/${RemoteDir}/deploy-encrypted.log"
exit $LASTEXITCODE
