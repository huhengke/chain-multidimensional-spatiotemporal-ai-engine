$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $projectRoot 'scripts\env.ps1')

function Assert-CommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$benchmarkRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data\benchmark'))
$expectedPrefix = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data')) + [System.IO.Path]::DirectorySeparatorChar
if (-not $benchmarkRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean unexpected benchmark path: $benchmarkRoot"
}
if (Test-Path -LiteralPath $benchmarkRoot) {
    Remove-Item -LiteralPath $benchmarkRoot -Recurse -Force
}

Push-Location $projectRoot
try {
    cargo build --release -p cnts-cli
    Assert-CommandSucceeded 'release build'
    $binary = Join-Path $env:CARGO_TARGET_DIR 'release\cnts.exe'
    $buildElapsed = (Measure-Command {
        & $binary build `
            --schema 'schemas/demo-sales.json' `
            --input 'tests/fixtures/demo-sales.jsonl' `
            --root $benchmarkRoot | Out-Null
        Assert-CommandSucceeded 'benchmark generation build'
    }).TotalMilliseconds

    $queryTimes = @()
    1..20 | ForEach-Object {
        $queryTimes += (Measure-Command {
            & $binary query `
                --root $benchmarkRoot `
                --plan 'tests/fixtures/query-total.json' | Out-Null
            Assert-CommandSucceeded 'benchmark query'
        }).TotalMilliseconds
    }
    $generationId = (Get-Content (Join-Path $benchmarkRoot 'CURRENT')).Trim()
    $generationPath = Join-Path $benchmarkRoot "generations\$generationId"
    $cntsBytes = (Get-ChildItem $generationPath -File | Measure-Object Length -Sum).Sum
    $sourceBytes = (Get-Item 'tests/fixtures/demo-sales.jsonl').Length
    $ordered = $queryTimes | Sort-Object
    $p95Index = [Math]::Min($ordered.Count - 1, [Math]::Ceiling($ordered.Count * 0.95) - 1)
    $report = [ordered]@{
        timestamp = (Get-Date).ToString('o')
        rows = 12
        source_bytes = $sourceBytes
        cnts_bytes = $cntsBytes
        build_ms = [Math]::Round($buildElapsed, 3)
        query_min_ms = [Math]::Round($ordered[0], 3)
        query_p95_ms = [Math]::Round($ordered[$p95Index], 3)
        query_max_ms = [Math]::Round($ordered[-1], 3)
    }
    $resultDirectory = Join-Path $projectRoot 'benchmarks\results'
    New-Item -ItemType Directory -Force $resultDirectory | Out-Null
    $report | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $resultDirectory 'phase0.json')
    $report | Format-List
}
finally {
    Pop-Location
}
