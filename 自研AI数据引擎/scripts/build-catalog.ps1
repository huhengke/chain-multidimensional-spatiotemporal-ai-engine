param(
    [string]$CatalogId = "cnts-candidate-v1",
    [int]$ChunkSize = 10000,
    [string]$LegacyEngineRoot = ""
)

$ErrorActionPreference = "Stop"
$physicalRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $LegacyEngineRoot) {
    $LegacyEngineRoot = (Resolve-Path (Join-Path $physicalRoot "..\AI数据引擎\chain-neuron-engine")).Path
}
$workspace = & (Join-Path $PSScriptRoot "mount-workspace.ps1")
Set-Location $workspace
. (Join-Path $workspace "scripts\env.ps1")

cargo build --release -p cnts-cli
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $workspace ".venv\Scripts\python.exe") -m cnts_engine.build.catalog_runner `
    --legacy-engine-root $LegacyEngineRoot `
    --project-root $physicalRoot `
    --data-root (Join-Path $physicalRoot "data") `
    --catalog-id $CatalogId `
    --cnts-binary (Join-Path $physicalRoot "target\release\cnts.exe") `
    --chunk-size $ChunkSize
exit $LASTEXITCODE
