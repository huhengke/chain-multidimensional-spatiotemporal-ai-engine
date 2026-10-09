param(
    [Parameter(Mandatory = $true)]
    [string]$CatalogId
)

$ErrorActionPreference = "Stop"
$physicalRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspace = & (Join-Path $PSScriptRoot "mount-workspace.ps1")
Set-Location $workspace
. (Join-Path $workspace "scripts\env.ps1")

& (Join-Path $workspace ".venv\Scripts\python.exe") -c @"
import json
from pathlib import Path
from cnts_engine.catalog import CatalogStore, CatalogValidator
root = Path(r'$physicalRoot') / 'data'
catalog = CatalogStore(root / 'catalogs').load('$CatalogId')
report = CatalogValidator(root).validate(catalog)
print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
"@
exit $LASTEXITCODE
