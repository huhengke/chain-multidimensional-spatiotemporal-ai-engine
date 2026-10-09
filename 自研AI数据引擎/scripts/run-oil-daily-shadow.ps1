param(
    [string]$SourceDatabase,
    [int]$ChunkSize = 10000
)

$ErrorActionPreference = 'Stop'
$physicalRoot = Split-Path -Parent $PSScriptRoot

if (-not $SourceDatabase) {
    $SourceDatabase = Join-Path (Split-Path -Parent $physicalRoot) 'AI数据引擎\chain-neuron-engine\data\engine.db'
}
$SourceDatabase = (Resolve-Path -LiteralPath $SourceDatabase).Path

$workspaceRoot = $physicalRoot
if ($workspaceRoot -match '[^\x00-\x7F]') {
    & (Join-Path $PSScriptRoot 'mount-workspace.ps1') -Drive 'R:' | Out-Null
    $workspaceRoot = 'R:\'
}

Push-Location $workspaceRoot
try {
    . (Join-Path $workspaceRoot 'scripts\env.ps1')
    & cargo build --release -p cnts-cli
    if ($LASTEXITCODE -ne 0) {
        throw "CNTS release build failed with exit code $LASTEXITCODE."
    }

    $python = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
    $env:PYTHONPATH = Join-Path $workspaceRoot 'python'
    & $python -m cnts_engine.shadow.runner `
        --source-database $SourceDatabase `
        --workspace $workspaceRoot `
        --cnts-binary (Join-Path $workspaceRoot 'target\release\cnts.exe') `
        --chunk-size $ChunkSize `
        --expected-row-count 859460 `
        --expected-start '2026-04-01' `
        --expected-end '2026-09-29' `
        --expected-org-unmapped 11117 `
        --expected-category-unmapped 120 `
        --expected-pending-review 2411
    if ($LASTEXITCODE -ne 0) {
        throw "Oil daily shadow migration failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
