param(
  [string]$Python = $(if ($env:CHAIN_PYTHON) { $env:CHAIN_PYTHON } else { "python" }),
  [string]$Node = $(if ($env:CHAIN_NODE) { $env:CHAIN_NODE } else { "node" }),
  [string]$ServiceToken = $(if ($env:ENGINE_SERVICE_TOKEN) { $env:ENGINE_SERVICE_TOKEN } else { "dev-service-token-please-rotate" })
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$engineRoot = (Resolve-Path (Join-Path $workspace "AI数据引擎\chain-neuron-engine")).Path
$sourceRoot = (Resolve-Path (Join-Path $workspace "AI数据引擎\数据源")).Path
$agentRoot = (Resolve-Path (Join-Path $workspace "Agent")).Path
$frontendRoot = (Resolve-Path (Join-Path $engineRoot "frontend")).Path
$runtimeRoot = Join-Path $workspace ".runtime"
$projectPython = Join-Path $engineRoot ".venv\Scripts\python.exe"
if ($Python -eq "python" -and (Test-Path -LiteralPath $projectPython)) {
  $Python = $projectPython
}
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$env:CHAIN_ENGINE_ROOT = $engineRoot
$env:CHAIN_SOURCE_DATA_ROOT = $sourceRoot
$env:CHAIN_WORKSPACE_ROOT = $workspace
$env:CHAIN_AGENT_ROOT = $agentRoot
$env:ENGINE_BASE_URL = "http://127.0.0.1:8000"
$env:AGENT_BASE_URL = "http://127.0.0.1:8001"
$env:ENGINE_SERVICE_TOKEN = $ServiceToken

function Get-ListenerProcessId {
  param([int]$Port)
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $listener) {
    return $null
  }
  return [int]$listener.OwningProcess
}

function Test-ServiceEndpoint {
  param(
    [string]$Uri,
    [string]$ExpectedService = ""
  )
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
    if ($response.StatusCode -ne 200) {
      return $false
    }
    if (-not $ExpectedService) {
      return $true
    }
    $payload = $response.Content | ConvertFrom-Json
    return $payload.status -eq "ok" -and $payload.service -eq $ExpectedService
  } catch {
    return $false
  }
}

function Ensure-Service {
  param(
    [string]$Name,
    [int]$Port,
    [string]$HealthUri,
    [string]$ExpectedService,
    [scriptblock]$StartAction,
    [string]$ErrorLog
  )

  $listenerProcessId = Get-ListenerProcessId -Port $Port
  if ($listenerProcessId) {
    if (-not (Test-ServiceEndpoint -Uri $HealthUri -ExpectedService $ExpectedService)) {
      throw "$Name 端口 $Port 已被进程 $listenerProcessId 占用，但健康检查不匹配。请先运行 停止全部.ps1 后重试。"
    }
    Write-Host "已复用 $Name (PID $listenerProcessId)"
    return $listenerProcessId
  }

  & $StartAction | Out-Null
  $deadline = (Get-Date).AddSeconds(30)
  do {
    Start-Sleep -Milliseconds 500
    $listenerProcessId = Get-ListenerProcessId -Port $Port
    if ($listenerProcessId -and (Test-ServiceEndpoint -Uri $HealthUri -ExpectedService $ExpectedService)) {
      Write-Host "已启动 $Name (PID $listenerProcessId)"
      return $listenerProcessId
    }
  } while ((Get-Date) -lt $deadline)

  $detail = ""
  if (Test-Path -LiteralPath $ErrorLog) {
    $detail = (Get-Content -LiteralPath $ErrorLog -Tail 20) -join [Environment]::NewLine
  }
  throw "$Name 启动失败，未在 30 秒内通过健康检查。错误日志：$ErrorLog`n$detail"
}

$engineOut = Join-Path $runtimeRoot "engine.out.log"
$engineErr = Join-Path $runtimeRoot "engine.err.log"
$agentOut = Join-Path $runtimeRoot "agent.out.log"
$agentErr = Join-Path $runtimeRoot "agent.err.log"
$frontendOut = Join-Path $runtimeRoot "frontend.out.log"
$frontendErr = Join-Path $runtimeRoot "frontend.err.log"

$engineProcessId = Ensure-Service `
  -Name "AI 数据引擎" `
  -Port 8000 `
  -HealthUri "http://127.0.0.1:8000/healthz" `
  -ExpectedService "chain-neuron-engine" `
  -ErrorLog $engineErr `
  -StartAction {
    Start-Process -FilePath $Python -ArgumentList "-m", "engine.main" -WorkingDirectory $engineRoot -RedirectStandardOutput $engineOut -RedirectStandardError $engineErr -PassThru -WindowStyle Hidden
  }

$agentProcessId = Ensure-Service `
  -Name "Agent Gateway" `
  -Port 8001 `
  -HealthUri "http://127.0.0.1:8001/healthz" `
  -ExpectedService "chain-neuron-agent-gateway" `
  -ErrorLog $agentErr `
  -StartAction {
    Start-Process -FilePath $Node -ArgumentList "src/server.js" -WorkingDirectory $agentRoot -RedirectStandardOutput $agentOut -RedirectStandardError $agentErr -PassThru -WindowStyle Hidden
  }

$frontendProcessId = Ensure-Service `
  -Name "Agent 工作台" `
  -Port 5173 `
  -HealthUri "http://127.0.0.1:5173/" `
  -ExpectedService "" `
  -ErrorLog $frontendErr `
  -StartAction {
    Start-Process -FilePath $Node -ArgumentList "node_modules/vite/bin/vite.js", "--host", "127.0.0.1" -WorkingDirectory $frontendRoot -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru -WindowStyle Hidden
  }

$state = @{
  started_at = (Get-Date).ToString("o")
  engine = $engineProcessId
  agent = $agentProcessId
  frontend = $frontendProcessId
  engine_root = $engineRoot
  agent_root = $agentRoot
} | ConvertTo-Json
Set-Content -LiteralPath (Join-Path $runtimeRoot "processes.json") -Value $state -Encoding UTF8

Write-Host "AI 数据引擎: http://127.0.0.1:8000/healthz"
Write-Host "Agent Gateway: http://127.0.0.1:8001/healthz"
Write-Host "Agent 工作台:  http://127.0.0.1:5173/agent"
Write-Host "日志目录:      $runtimeRoot"
Write-Host "停止命令:      .\说明文件\脚本\停止全部.ps1"
