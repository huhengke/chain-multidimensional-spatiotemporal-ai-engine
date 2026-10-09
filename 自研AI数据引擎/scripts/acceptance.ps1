$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'env.ps1')

function Assert-CommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$acceptanceRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data\acceptance'))
$expectedPrefix = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data')) + [System.IO.Path]::DirectorySeparatorChar
if (-not $acceptanceRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean unexpected acceptance path: $acceptanceRoot"
}
if (Test-Path -LiteralPath $acceptanceRoot) {
    Remove-Item -LiteralPath $acceptanceRoot -Recurse -Force
}

Push-Location $projectRoot
try {
    cargo fmt --all -- --check
    Assert-CommandSucceeded 'cargo fmt'
    cargo clippy --workspace --all-targets -- -D warnings
    Assert-CommandSucceeded 'cargo clippy'
    cargo test --workspace
    Assert-CommandSucceeded 'cargo test'

    cargo run --quiet -p cnts-cli -- build `
        --schema 'schemas/demo-sales.json' `
        --input 'tests/fixtures/demo-sales.jsonl' `
        --root $acceptanceRoot | Out-Null
    Assert-CommandSucceeded 'first generation build'

    $inspectJson = cargo run --quiet -p cnts-cli -- inspect --root $acceptanceRoot
    Assert-CommandSucceeded 'generation inspection'
    $inspect = $inspectJson | ConvertFrom-Json
    if ($inspect.row_count -ne 12 -or $inspect.node_count -ne 10 -or $inspect.branch_nodes -ne 6) {
        throw "Unexpected topology: $($inspect | ConvertTo-Json -Compress)"
    }

    cargo run --quiet -p cnts-cli -- verify --root $acceptanceRoot | Out-Null
    Assert-CommandSucceeded 'generation verification'
    $queryJson = cargo run --quiet -p cnts-cli -- query `
        --root $acceptanceRoot `
        --plan 'tests/fixtures/query-total.json'
    Assert-CommandSucceeded 'total query'
    $query = $queryJson | ConvertFrom-Json
    if ($query.rows[0].metrics.sales_amount -ne '2220.00') {
        throw "Unexpected sales_amount: $($query.rows[0].metrics.sales_amount)"
    }
    if ($query.detail_fallback) {
        throw 'Total query unexpectedly used detail fallback'
    }

    $commonJson = cargo run --quiet -p cnts-cli -- query `
        --root $acceptanceRoot `
        --plan 'tests/fixtures/query-company-common.json'
    Assert-CommandSucceeded 'materialized common-feature query'
    $common = $commonJson | ConvertFrom-Json
    if ($common.rows[0].metrics.sales_amount -ne '960.00' -or $common.rows[0].record_count -ne 6) {
        throw "Unexpected common-feature result: $($common | ConvertTo-Json -Compress -Depth 8)"
    }
    if ($common.detail_fallback -or -not ($common.route.operation -contains 'DESCEND_COMMON')) {
        throw 'Common-feature query did not use the materialized inner topology'
    }

    cargo run --quiet -p cnts-cli -- build `
        --schema 'schemas/demo-sales.json' `
        --input 'tests/fixtures/demo-sales-v2.jsonl' `
        --root $acceptanceRoot | Out-Null
    Assert-CommandSucceeded 'second generation build'
    $rollbackJson = cargo run --quiet -p cnts-cli -- rollback --root $acceptanceRoot
    Assert-CommandSucceeded 'generation rollback'
    $rollback = $rollbackJson | ConvertFrom-Json
    if (-not $rollback.generation_id) {
        throw 'Rollback did not return a generation id'
    }

    if (Test-Path (Join-Path $projectRoot '.venv\Scripts\python.exe')) {
        & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m pytest -q
        Assert-CommandSucceeded 'Python API tests'
    }

    Write-Host 'CNTS phase 0 acceptance passed.' -ForegroundColor Green
}
finally {
    Pop-Location
}
