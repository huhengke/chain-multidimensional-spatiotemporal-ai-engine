param(
  [string]$DshCommand = "dsh"
)

$ErrorActionPreference = "Stop"

$engineRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$projectRoot = (Resolve-Path (Join-Path $engineRoot "..")).Path

$env:CHAIN_ENGINE_ROOT = $engineRoot
$env:CHAIN_PROJECT_ROOT = $projectRoot
$env:ENGINE_BASE_URL = if ($env:ENGINE_BASE_URL) { $env:ENGINE_BASE_URL } else { "http://127.0.0.1:8000" }
$env:ENGINE_QUERY_URL = if ($env:ENGINE_QUERY_URL) { $env:ENGINE_QUERY_URL } else { "$env:ENGINE_BASE_URL/api/v1/engine/query" }

if (-not $env:ENGINE_SERVICE_TOKEN) {
  throw "请先设置 ENGINE_SERVICE_TOKEN，再启动 DeepSeek Harness。"
}

Write-Host "CHAIN_ENGINE_ROOT = $env:CHAIN_ENGINE_ROOT"
Write-Host "ENGINE_BASE_URL   = $env:ENGINE_BASE_URL"
Write-Host "ENGINE_QUERY_URL  = $env:ENGINE_QUERY_URL"
Write-Host "启动 DeepSeek Harness；启动后在 GUI 的会话设置中选择 preset: chain-neuron"

& $DshCommand --profile web
if ($LASTEXITCODE -ne 0) {
  throw "DeepSeek Harness 启动失败，退出码：$LASTEXITCODE"
}
