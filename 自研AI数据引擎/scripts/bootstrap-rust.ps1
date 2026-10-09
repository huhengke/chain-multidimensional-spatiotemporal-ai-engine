$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'env.ps1')

$installerDirectory = Join-Path $projectRoot '.toolchains'
$installerPath = Join-Path $installerDirectory 'rustup-init.exe'
$llvmMingwVersion = '20260908'
$llvmMingwArchive = Join-Path $installerDirectory "llvm-mingw-$llvmMingwVersion-ucrt-x86_64.zip"
$llvmMingwInstall = Join-Path $installerDirectory 'llvm-mingw'
New-Item -ItemType Directory -Force $installerDirectory | Out-Null
New-Item -ItemType Directory -Force $env:RUSTUP_HOME | Out-Null
New-Item -ItemType Directory -Force $env:CARGO_HOME | Out-Null

function Assert-CommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Invoke-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    try {
        Invoke-WebRequest $Url -OutFile $Destination
        return
    }
    catch {
        $python = Get-Command python -ErrorAction SilentlyContinue
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if (-not $python -or -not $curl) {
            throw
        }

        $certificateBundle = & $python.Source -c 'import certifi; print(certifi.where())'
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $certificateBundle)) {
            throw
        }

        Write-Host 'Windows certificate chain is incomplete; retrying with the installed certifi CA bundle.'
        & $curl.Source --cacert $certificateBundle -L --fail --output $Destination $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Verified download failed with curl exit code $LASTEXITCODE."
        }
    }
}

if (-not (Test-Path $installerPath)) {
    Write-Host 'Downloading rustup-init from the official Rust distribution endpoint...'
    Invoke-VerifiedDownload 'https://win.rustup.rs/x86_64' $installerPath
}

if (-not (Test-Path (Join-Path $env:CARGO_HOME 'bin\rustup.exe'))) {
    & $installerPath -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-gnu --default-toolchain stable
    Assert-CommandSucceeded 'rustup installation'
}

& (Join-Path $env:CARGO_HOME 'bin\rustup.exe') toolchain install stable-x86_64-pc-windows-gnu --profile minimal --component rustfmt --component clippy
Assert-CommandSucceeded 'Rust toolchain installation'
& (Join-Path $env:CARGO_HOME 'bin\rustup.exe') default stable-x86_64-pc-windows-gnu
Assert-CommandSucceeded 'Rust default toolchain selection'

if ($env:CNTS_INSTALL_LLVM_MINGW -eq '1' -and -not (Test-Path (Join-Path $llvmMingwInstall 'bin\clang.exe'))) {
    if (-not (Test-Path $llvmMingwArchive)) {
        $llvmMingwUrl = "https://github.com/mstorsjo/llvm-mingw/releases/download/$llvmMingwVersion/llvm-mingw-$llvmMingwVersion-ucrt-x86_64.zip"
        Write-Host "Downloading project-local LLVM-MinGW $llvmMingwVersion..."
        Invoke-VerifiedDownload $llvmMingwUrl $llvmMingwArchive
    }

    $extractRoot = Join-Path $installerDirectory "llvm-mingw-$llvmMingwVersion-extract"
    if (Test-Path $extractRoot) {
        Remove-Item -Recurse -Force $extractRoot
    }
    New-Item -ItemType Directory -Force $extractRoot | Out-Null
    Expand-Archive -LiteralPath $llvmMingwArchive -DestinationPath $extractRoot -Force
    $expandedDirectory = Get-ChildItem $extractRoot -Directory | Select-Object -First 1
    if (-not $expandedDirectory) {
        throw 'LLVM-MinGW archive did not contain a toolchain directory.'
    }
    if (Test-Path $llvmMingwInstall) {
        Remove-Item -Recurse -Force $llvmMingwInstall
    }
    Move-Item -LiteralPath $expandedDirectory.FullName -Destination $llvmMingwInstall
    Remove-Item -Recurse -Force $extractRoot
}

. (Join-Path $PSScriptRoot 'env.ps1')

& (Join-Path $env:CARGO_HOME 'bin\rustc.exe') --version
Assert-CommandSucceeded 'rustc version check'
& (Join-Path $env:CARGO_HOME 'bin\cargo.exe') --version
Assert-CommandSucceeded 'cargo version check'
if (Test-Path (Join-Path $llvmMingwInstall 'bin\clang.exe')) {
    & (Join-Path $llvmMingwInstall 'bin\clang.exe') --version
    Assert-CommandSucceeded 'clang version check'
}
