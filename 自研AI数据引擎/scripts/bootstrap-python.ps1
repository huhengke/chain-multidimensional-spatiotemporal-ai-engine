$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot '.venv'

function Assert-CommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

Push-Location $projectRoot
try {
    if (-not (Test-Path (Join-Path $venvPath 'Scripts\python.exe'))) {
        uv venv --python 3.11 $venvPath
        Assert-CommandSucceeded 'virtual environment creation'
    }

    . (Join-Path $PSScriptRoot 'env.ps1')
    $python = Join-Path $venvPath 'Scripts\python.exe'
    uv pip install --python $python maturin pytest
    Assert-CommandSucceeded 'Python build dependency installation'
    maturin develop
    Assert-CommandSucceeded 'native Python extension build'
    & $python --version
    Assert-CommandSucceeded 'Python runtime check'
}
finally {
    Pop-Location
}
