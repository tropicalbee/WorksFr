$TrivyImage = 'aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb'
$GitleaksImage = 'zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f'
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host 'Auditing backend Python dependencies'
& (Join-Path $RepoRoot 'backend/.venv/Scripts/python.exe') -m pip_audit
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'Auditing frontend npm dependencies'
Push-Location (Join-Path $RepoRoot 'frontend')
try {
    npm audit --audit-level=high
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

$ScanFolder = Join-Path $RepoRoot '.scratch/trivy'
New-Item -ItemType Directory -Force -Path $ScanFolder | Out-Null
foreach ($Image in @('worksfr-sandbox', 'worksfr-video-tools')) {
    Write-Host "Scanning Docker image $Image with Trivy"
    $Archive = Join-Path $ScanFolder "${Image}.tar"
    docker save --output $Archive $Image
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    docker run --rm -v "${ScanFolder}:/scan:ro" $TrivyImage image --input "/scan/${Image}.tar" --severity HIGH,CRITICAL --exit-code 1
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host 'Scanning repository history for secrets with gitleaks'
docker run --rm -v "${RepoRoot}:/repo:ro" $GitleaksImage detect --source /repo --no-banner --redact
exit $LASTEXITCODE
