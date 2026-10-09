param(
    [ValidatePattern('^[D-Z]:$')]
    [string]$Drive = 'R:'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$driveRoot = "$Drive\"
$marker = Join-Path $driveRoot 'Cargo.toml'

if (Test-Path -LiteralPath $driveRoot) {
    if (-not (Test-Path -LiteralPath $marker)) {
        throw "$Drive is already in use and is not the CNTS workspace. Choose another drive, for example: .\scripts\mount-workspace.ps1 -Drive Q:"
    }
}
else {
    & subst.exe $Drive $projectRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to map $Drive to $projectRoot."
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $driveRoot 'scripts\env.ps1'))) {
    throw "$Drive does not point to the CNTS workspace."
}

Write-Host "CNTS ASCII workspace is ready: $driveRoot" -ForegroundColor Green
Write-Output $driveRoot
