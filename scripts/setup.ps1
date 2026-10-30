$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
python -m pip install -e "$Root\backend[dev]"
Push-Location "$Root\frontend"
try { npm install; npm run build } finally { Pop-Location }
$StaticDir = Join-Path $Root 'backend\frame\static'
if (Test-Path -LiteralPath $StaticDir) { Remove-Item -LiteralPath $StaticDir -Recurse -Force }
Copy-Item -LiteralPath "$Root\frontend\dist" -Destination $StaticDir -Recurse
Write-Host 'FRAME dependencies installed.' -ForegroundColor Green
