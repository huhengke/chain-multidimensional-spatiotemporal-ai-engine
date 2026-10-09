param([int]$Port = 8000)
$ErrorActionPreference = 'SilentlyContinue'
$pids = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -ExpandProperty OwningProcess -Unique
if ($pids) {
  foreach ($p in $pids) { Write-Output ("killing " + $p); Stop-Process -Id $p -Force }
} else {
  Write-Output "no listener on $Port"
}
Start-Sleep 3
$left = Get-NetTCPConnection -LocalPort $Port -State Listen
if ($left) { Write-Output ("STILL LISTENING: " + (($left.OwningProcess | Sort-Object -Unique) -join ',')) }
else { Write-Output "port $Port free" }
