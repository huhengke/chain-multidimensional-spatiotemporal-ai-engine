$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$statePath = Join-Path $workspace ".runtime\processes.json"
if (-not (Test-Path -LiteralPath $statePath)) {
  Write-Host "未找到运行状态文件，无需停止。"
  exit 0
}
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
foreach ($name in "frontend", "agent", "engine") {
  $processId = [int]$state.$name
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $processId -Force
    Write-Host "已停止 $name (PID $processId)"
  }
}
Remove-Item -LiteralPath $statePath -Force
