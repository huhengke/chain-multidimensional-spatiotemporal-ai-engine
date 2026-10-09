# Compatibility wrapper: start the top-level Harness Agent Gateway on port 8001.
$ErrorActionPreference = "Stop"
$engineRoot = Split-Path -Parent $PSScriptRoot
$workspace = (Resolve-Path (Join-Path $engineRoot "..\..")).Path
$agentRoot = (Resolve-Path (Join-Path $workspace "Agent")).Path
$env:CHAIN_AGENT_ROOT = $agentRoot
Set-Location $agentRoot
node src/server.js
