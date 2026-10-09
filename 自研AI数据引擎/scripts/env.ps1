$projectRoot = Split-Path -Parent $PSScriptRoot
if ($projectRoot -match '[^\x00-\x7F]') {
    throw 'CNTS Rust tools require an ASCII workspace path on Windows. Run scripts\mount-workspace.ps1, change to the returned drive, then retry.'
}
$env:RUSTUP_HOME = Join-Path $projectRoot '.toolchains\rustup'
$env:CARGO_HOME = Join-Path $projectRoot '.cargo-home'
$env:RUSTUP_TOOLCHAIN = 'stable-x86_64-pc-windows-gnu'
$env:CARGO_TARGET_DIR = Join-Path $projectRoot 'target'
$env:VIRTUAL_ENV = Join-Path $projectRoot '.venv'
$llvmMingwRoot = Join-Path $projectRoot '.toolchains\llvm-mingw'
$llvmMingwBin = Join-Path $llvmMingwRoot 'bin'
$rustToolchainRoot = Join-Path $env:RUSTUP_HOME 'toolchains\stable-x86_64-pc-windows-gnu'
$rustGnuBin = Join-Path $rustToolchainRoot 'lib\rustlib\x86_64-pc-windows-gnu\bin'
$rustGnuSelfContainedBin = Join-Path $rustGnuBin 'self-contained'
$rustGnuLinkerBin = Join-Path $rustGnuBin 'gcc-ld'

$pathEntries = @(
    (Join-Path $env:CARGO_HOME 'bin'),
    $llvmMingwBin,
    $rustGnuBin,
    $rustGnuSelfContainedBin,
    $rustGnuLinkerBin,
    (Join-Path $env:VIRTUAL_ENV 'Scripts')
)

$reversedPathEntries = [object[]]$pathEntries
[array]::Reverse($reversedPathEntries)
foreach ($entry in $reversedPathEntries) {
    if (Test-Path $entry) {
        $env:PATH = "$entry;$env:PATH"
    }
}

if (Test-Path $llvmMingwBin) {
    $gnuCompiler = Join-Path $llvmMingwBin 'x86_64-w64-mingw32-gcc.exe'
    $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $gnuCompiler
    $env:CC_x86_64_pc_windows_gnu = $gnuCompiler
    $env:CXX_x86_64_pc_windows_gnu = Join-Path $llvmMingwBin 'x86_64-w64-mingw32-g++.exe'
    $env:AR_x86_64_pc_windows_gnu = Join-Path $llvmMingwBin 'llvm-ar.exe'
}
elseif (Test-Path $rustGnuSelfContainedBin) {
    $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $null
}

$env:CNTS_PROJECT_ROOT = $projectRoot
Write-Host "CNTS environment loaded: $projectRoot"
